"""Parakeet backend, powered by parakeet-mlx (Apple Silicon / MLX)."""
from uiwhispercpp.models.audio import ensure_ffmpeg_on_path
from uiwhispercpp.models.base import (
  Model,
  ModelOption,
  OnProgress,
  OnSegment,
  Segment,
  Word,
)

# Long audio is transcribed in overlapping windows. Chunking also gives us the
# progress callbacks parakeet-mlx only emits when it splits a file.
CHUNK_DURATION = 120.0
OVERLAP_DURATION = 15.0
# TDT beam search explores several hypotheses per step instead of greedily
# committing to one; on our test audio it fixes real word errors ("taxes" ->
# "taxis") for ~4x slower decoding, which is still ~15x realtime on Apple
# Silicon.
BEAM_SIZE = 5


class Parakeet(Model):
  """NVIDIA Parakeet models running locally through MLX.

  parakeet-mlx and mlx are imported lazily, so the app starts fast and only
  pulls in the heavy MLX runtime once a Parakeet model is actually used. The v3
  model is multilingual and detects the language itself, so the `language` hint
  is ignored. (The English-only v2, `mlx-community/parakeet-tdt-0.6b-v2`, scores
  ~0.3 WER points better on English — judged too small to be worth a second
  dropdown entry.)
  """

  _OPTIONS = [
    ModelOption(
      "mlx-community/parakeet-tdt-0.6b-v3",
      "Parakeet v3 - Fast multilingual, Apple Silicon (600MB)",
    ),
  ]

  def __init__(self) -> None:
    # Typed as object because parakeet_mlx is imported lazily in _load.
    self._model: object | None = None
    self._loaded_key: str | None = None

  @property
  def options(self) -> list[ModelOption]:
    return list(self._OPTIONS)

  def transcribe(
    self,
    audio_path: str,
    *,
    model_key: str,
    language: str,
    on_segment: OnSegment,
    on_progress: OnProgress,
  ) -> list[Segment]:
    ensure_ffmpeg_on_path()
    model = self._load(model_key)

    def report_progress(position: int, total: int) -> None:
      if total > 0:
        on_progress(int(position / total * 100))

    from parakeet_mlx.parakeet import Beam, DecodingConfig

    # parakeet-mlx loads and resamples the audio itself, so we hand it the
    # original file untouched.
    result = model.transcribe(
      audio_path,
      chunk_duration=CHUNK_DURATION,
      overlap_duration=OVERLAP_DURATION,
      chunk_callback=report_progress,
      decoding_config=DecodingConfig(decoding=Beam(beam_size=BEAM_SIZE)),
    )

    # parakeet returns the whole result at once; replay it through the same
    # streaming callback the rest of the app expects. We also carry the per-word
    # timing (reconstructed from the model's sub-word tokens) so speaker
    # diarization can attribute speakers word-by-word.
    segments = [
      Segment(
        start=sentence.start,
        end=sentence.end,
        text=sentence.text.strip(),
        words=_words_from_sentence(sentence),
      )
      for sentence in result.sentences
    ]
    for segment in segments:
      on_segment(segment)
    return segments

  def unload(self) -> None:
    self._model = None
    self._loaded_key = None

  def _load(self, model_key: str) -> object:
    if self._model is not None and self._loaded_key == model_key:
      return self._model
    self.unload()
    try:
      from parakeet_mlx import from_pretrained
    except ImportError as error:
      raise RuntimeError(
        "parakeet-mlx is not installed. Install it with `uv add parakeet-mlx` "
        "(requires an Apple Silicon Mac)."
      ) from error
    _apply_power_spectrum_fix()
    self._model = from_pretrained(model_key)
    self._loaded_key = model_key
    return self._model


_power_spectrum_fix_applied = False


def _apply_power_spectrum_fix() -> None:
  """Make parakeet-mlx compute the true power spectrum NeMo trained on.

  parakeet_mlx.audio.get_logmel approximates the spectral magnitude as
  |Re| + |Im| instead of sqrt(Re^2 + Im^2) (up to 3 dB of phase-dependent
  error per bin). NVIDIA's NeMo preprocessor - the features these weights were
  trained on - uses the true magnitude. Replacing the approximation measurably
  corrects words on real audio ("center line" -> "centerline").

  The replacement is built from parakeet-mlx's own public helpers and is
  validated on a dummy input first; if the library's internals ever change
  shape, we silently keep the stock implementation instead of breaking.
  """
  global _power_spectrum_fix_applied
  if _power_spectrum_fix_applied:
    return
  try:
    import mlx.core as mx
    import parakeet_mlx
    from parakeet_mlx.audio import (
      PreprocessArgs,
      bartlett,
      blackman,
      hamming,
      hanning,
      stft,
    )

    def get_logmel(x: "mx.array", args: "PreprocessArgs") -> "mx.array":
      original_dtype = x.dtype
      if args.pad_to > 0 and x.shape[-1] < args.pad_to:
        x = mx.pad(x, ((0, args.pad_to - x.shape[-1]),), constant_values=args.pad_value)
      if args.preemph is not None:
        x = mx.concat([x[:1], x[1:] - args.preemph * x[:-1]], axis=0)
      window = (
        hanning(args.win_length).astype(x.dtype)
        if args.window in ("hann", "hanning")
        else hamming(args.win_length).astype(x.dtype)
        if args.window == "hamming"
        else blackman(args.win_length).astype(x.dtype)
        if args.window == "blackman"
        else bartlett(args.win_length).astype(x.dtype)
        if args.window == "bartlett"
        else None
      )
      x = stft(x, args.n_fft, args.hop_length, args.win_length, window)
      x = mx.abs(x)  # true |z|, not |Re| + |Im|
      if args.mag_power != 1.0:
        x = mx.power(x, args.mag_power)
      x = mx.matmul(args._filterbanks.astype(x.dtype), x.T)
      x = mx.log(x + 1e-5)
      if args.normalize == "per_feature":
        mean = mx.mean(x, axis=1, keepdims=True)
        std = mx.std(x, axis=1, keepdims=True)
        normalized = (x - mean) / (std + 1e-5)
      else:
        normalized = (x - mx.mean(x)) / (mx.std(x) + 1e-5)
      return mx.expand_dims(normalized.T, axis=0).astype(original_dtype)

    # Smoke-test against a real preprocessor config before installing it.
    args = PreprocessArgs(
      sample_rate=16000, normalize="per_feature", window_size=0.025,
      window_stride=0.01, window="hann", features=80, n_fft=512, dither=1e-5,
    )
    out = get_logmel(mx.zeros(3200), args)
    assert out.shape[0] == 1 and out.shape[2] == 80

    parakeet_mlx.audio.get_logmel = get_logmel
    parakeet_mlx.parakeet.get_logmel = get_logmel
    _power_spectrum_fix_applied = True
  except Exception:
    # Upstream internals changed; the stock feature path still works.
    _power_spectrum_fix_applied = True


def _words_from_sentence(sentence: object) -> tuple[Word, ...]:
  """Group a sentence's sub-word tokens into whole words with timing.

  parakeet-mlx emits sub-word tokens whose `.text` carries a leading space at
  the start of each new word (e.g. " hello", "ing"). We start a new word at any
  token whose text begins with a space, so punctuation that hangs off the end of
  a word ("you?", "do.") stays attached, and each word spans the time from its
  first token's start to its last token's end.
  """
  words: list[Word] = []
  current: list[object] = []
  for token in sentence.tokens:
    if current and token.text.startswith(" "):
      words.append(_word_from_tokens(current))
      current = [token]
    else:
      current.append(token)
  if current:
    words.append(_word_from_tokens(current))
  return tuple(word for word in words if word.text)


def _word_from_tokens(tokens: list[object]) -> Word:
  text = "".join(token.text for token in tokens).strip()
  return Word(start=tokens[0].start, end=tokens[-1].end, text=text)
