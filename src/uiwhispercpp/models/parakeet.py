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


class Parakeet(Model):
  """NVIDIA Parakeet models running locally through MLX.

  parakeet-mlx and mlx are imported lazily, so the app starts fast and only
  pulls in the heavy MLX runtime once a Parakeet model is actually used. The v3
  model is multilingual and detects the language itself, so the `language` hint
  is ignored.
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

    # parakeet-mlx loads and resamples the audio itself, so we hand it the
    # original file untouched.
    result = model.transcribe(
      audio_path,
      chunk_duration=CHUNK_DURATION,
      overlap_duration=OVERLAP_DURATION,
      chunk_callback=report_progress,
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
    self._model = from_pretrained(model_key)
    self._loaded_key = model_key
    return self._model


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
