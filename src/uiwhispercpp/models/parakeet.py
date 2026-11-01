"""Parakeet backend, powered by parakeet-mlx (Apple Silicon / MLX)."""
from collections.abc import Callable
import importlib
import os
import importlib.util
import shutil
import subprocess
import sys

from uiwhispercpp.models.audio import ensure_ffmpeg_on_path
from uiwhispercpp.models.base import (
  Model,
  ModelOption,
  OnProgress,
  OnSegment,
  OnStatus,
  Segment,
  StopToken,
  UserFacingError,
  Word,
)
from uiwhispercpp.models.downloads import download, format_size
from uiwhispercpp import app_log
from uiwhispercpp.system_check import is_apple_silicon

_log = app_log.get("parakeet")

# Kept in sync with pyproject.toml; used when a source checkout lacks the
# package and we install it on first use.
PARAKEET_REQUIREMENT = "parakeet-mlx>=0.5.1,<0.6"

# Long audio is transcribed in overlapping windows (see _transcribe_streaming),
# which also gives us progress and lets finished sentences stream to the UI.
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

  _KEY = "mlx-community/parakeet-tdt-0.6b-v3"
  _LABEL = "Parakeet v3 - fast and accurate, recommended (2.5 GB)"

  def __init__(self) -> None:
    # Typed as object because parakeet_mlx is imported lazily in _load.
    self._model: object | None = None
    self._loaded_key: str | None = None

  @property
  def options(self) -> list[ModelOption]:
    return [ModelOption(
      self._KEY, self._LABEL, parakeet_unavailable_reason(), short_label="Parakeet v3"
    )]

  def transcribe(
    self,
    audio_path: str,
    *,
    model_key: str,
    language: str,
    on_segment: OnSegment,
    on_progress: OnProgress,
    on_status: OnStatus | None = None,
    stop: StopToken | None = None,
  ) -> list[Segment]:
    status = on_status or (lambda _message: None)
    ensure_ffmpeg_on_path()
    model = self._load(model_key, status, stop)
    status("Transcribing...")

    from parakeet_mlx.parakeet import Beam, DecodingConfig

    def to_segment(sentence: object) -> Segment:
      # Per-word timing (rebuilt from sub-word tokens) lets speaker diarization
      # attribute speakers word-by-word.
      return Segment(
        start=sentence.start,
        end=sentence.end,
        text=sentence.text.strip(),
        words=_words_from_sentence(sentence),
      )

    # Sentences are streamed to on_segment as soon as they are final, so the
    # UI shows the transcript live; the returned list is the exact final result.
    result = _transcribe_streaming(
      model,
      audio_path,
      decoding_config=DecodingConfig(decoding=Beam(beam_size=BEAM_SIZE)),
      on_progress=on_progress,
      on_sentence=lambda sentence: on_segment(to_segment(sentence)),
      stop=stop,
    )
    return [to_segment(sentence) for sentence in result.sentences]

  def unload(self) -> None:
    self._model = None
    self._loaded_key = None

  def _load(self, model_key: str, status: OnStatus, stop: StopToken | None) -> object:
    if self._model is not None and self._loaded_key == model_key:
      return self._model
    self.unload()
    if importlib.util.find_spec("parakeet_mlx") is None and not _is_frozen():
      _install_parakeet(status)
    try:
      from parakeet_mlx import from_pretrained
    except (ImportError, OSError) as error:
      raise UserFacingError(
        "Parakeet couldn't start on this Mac. Choose a Whisper model in the "
        "menu at the bottom of the window, then try again."
      ) from error
    _apply_power_spectrum_fix()
    model_dir = _ensure_model_files(model_key, status, stop)
    status("Loading the model...")
    self._model = from_pretrained(model_dir)
    self._loaded_key = model_key
    return self._model


def _transcribe_streaming(
  model: object,
  audio_path: str,
  *,
  decoding_config: object,
  on_progress: OnProgress,
  on_sentence: "Callable[[object], None]",
  stop: StopToken | None = None,
) -> object:
  """parakeet-mlx's chunked `transcribe`, emitting sentences as they settle.

  Same algorithm as `BaseParakeet.transcribe` (0.5.x): overlapping windows
  whose token streams are merged in the overlap. After merging a window,
  everything that ends before the next window starts can no longer change, so
  those sentences are emitted right away instead of after the whole file. The
  last sentence before that point is held back, since it might still grow.
  """
  from parakeet_mlx import parakeet as pk

  config = model.preprocessor_config
  audio = pk.load_audio(audio_path, config.sample_rate, pk.mx.bfloat16)
  total = len(audio)
  chunk = int(CHUNK_DURATION * config.sample_rate)
  overlap = int(OVERLAP_DURATION * config.sample_rate)
  emitted = 0

  def emit(sentences: list, stable_until: float, final: bool) -> None:
    nonlocal emitted
    ready = sentences if final else [s for s in sentences[:-1] if s.end <= stable_until]
    for sentence in ready[emitted:]:
      on_sentence(sentence)
    emitted = max(emitted, len(ready))

  if total <= chunk:
    on_progress(0)
    result = model.generate(pk.get_logmel(audio, config), decoding_config=decoding_config)[0]
    emit(result.sentences, 0.0, final=True)
    on_progress(100)
    return result

  tokens: list = []
  step = chunk - overlap
  for start in range(0, total, step):
    if stop is not None:
      stop.check()
    end = min(start + chunk, total)
    on_progress(int(start / total * 100))
    if end - start < config.hop_length:
      break
    window = model.generate(
      pk.get_logmel(audio[start:end], config), decoding_config=decoding_config
    )[0]
    offset = start / config.sample_rate
    for sentence in window.sentences:
      for token in sentence.tokens:
        token.start += offset
        token.end = token.start + token.duration
    if tokens:
      try:
        tokens = pk.merge_longest_contiguous(
          tokens, window.tokens, overlap_duration=OVERLAP_DURATION
        )
      except RuntimeError:
        tokens = pk.merge_longest_common_subsequence(
          tokens, window.tokens, overlap_duration=OVERLAP_DURATION
        )
    else:
      tokens = window.tokens
    sentences = pk.tokens_to_sentences(tokens, decoding_config.sentence)
    emit(sentences, (start + step) / config.sample_rate, final=False)

  result = pk.sentences_to_result(pk.tokens_to_sentences(tokens, decoding_config.sentence))
  emit(result.sentences, 0.0, final=True)
  on_progress(100)
  return result


_MODEL_FILES = ("config.json", "model.safetensors")


def _ensure_model_files(model_key: str, status: OnStatus, stop: StopToken | None) -> str:
  """Return a local folder holding the model, downloading it safely if needed.

  A copy already in the Hugging Face cache (from earlier versions of the app)
  is reused. Otherwise both files are fetched into the app's own model folder
  with progress, timeouts and atomic renames, so an interrupted download is
  never mistaken for a complete one.
  """
  try:
    from huggingface_hub import try_to_load_from_cache
    cached = [try_to_load_from_cache(model_key, name) for name in _MODEL_FILES]
    if all(isinstance(path, str) and os.path.exists(path) for path in cached):
      return os.path.dirname(cached[0])
  except Exception:  # noqa: BLE001 - the cache is only an optimisation
    pass
  folder = os.path.expanduser(
    f"~/Library/Application Support/UIWhisperCPP/models/{model_key.replace('/', '--')}"
  )
  for name in _MODEL_FILES:
    path = os.path.join(folder, name)
    if os.path.exists(path):
      continue

    def on_bytes(done: int, total: int, name: str = name) -> None:
      if name == "config.json":
        return
      if not done and not total:
        return  # still connecting; the generic status covers it
      if total:
        status(
          f"Downloading Parakeet v3 ({format_size(done)} of {format_size(total)}, "
          f"one-time) {done * 100 // total}%"
        )
      else:
        status(f"Downloading Parakeet v3 ({format_size(done)}, one-time)...")

    download(
      f"https://huggingface.co/{model_key}/resolve/main/{name}", path,
      what="Parakeet v3 model", on_bytes=on_bytes, stop=stop,
      min_size=100 if name == "config.json" else 100_000_000,
    )
  return folder


def _is_frozen() -> bool:
  return bool(getattr(sys, "frozen", False))


def parakeet_unavailable_reason() -> str | None:
  """Why this Mac can't run Parakeet at all, or None when it can.

  A source checkout that merely lacks parakeet-mlx is not "unavailable": the
  package is installed automatically on first use (see `_install_parakeet`).
  """
  if not is_apple_silicon():
    return "Parakeet needs a Mac with Apple Silicon (M1 or newer)."
  if _is_frozen() and importlib.util.find_spec("parakeet_mlx") is None:
    return "Parakeet isn't included in this build of the app."
  return None


def _install_parakeet(status: OnStatus) -> None:
  """Install parakeet-mlx into the running environment (source checkouts only).

  uv-managed virtualenvs ship without pip, so prefer `uv pip install` aimed at
  this interpreter, and fall back to bootstrapping pip via ensurepip.
  """
  status("Setting up Parakeet (one-time, about a minute)...")
  uv = shutil.which("uv")
  if uv:
    commands = [[uv, "pip", "install", "--python", sys.executable, PARAKEET_REQUIREMENT]]
  else:
    commands = [
      [sys.executable, "-m", "ensurepip", "--upgrade"],
      [sys.executable, "-m", "pip", "install", PARAKEET_REQUIREMENT],
    ]
  for command in commands:
    _log.info("Installing Parakeet: %s", " ".join(command))
    result = subprocess.run(command, capture_output=True, text=True)
    for line in (result.stdout + result.stderr).splitlines():
      _log.info("  %s", line)
    if result.returncode != 0:
      tail = (result.stderr or result.stdout).strip().splitlines()[-3:]
      # Tools indent wrapped lines; collapse all runs of whitespace.
      _log.warning("Parakeet install failed: %s", " ".join(" ".join(tail).split()))
      raise UserFacingError(
        "Couldn't set up Parakeet. Check your internet connection and try "
        "again, or choose a Whisper model at the bottom of the window."
      )
  importlib.invalidate_caches()


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
