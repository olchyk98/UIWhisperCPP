"""Parakeet backend, powered by parakeet-mlx (Apple Silicon / MLX)."""
from uiwhispercpp.models.audio import ensure_ffmpeg_on_path
from uiwhispercpp.models.base import (
  Model,
  ModelOption,
  OnProgress,
  OnSegment,
  Segment,
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
    # streaming callback the rest of the app expects.
    segments = [
      Segment(start=sentence.start, end=sentence.end, text=sentence.text.strip())
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
