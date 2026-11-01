"""Whisper backend, powered by whisper.cpp through pywhispercpp."""
from uiwhispercpp.models.audio import resample_to_wav
from uiwhispercpp.models.base import (
  Model,
  ModelOption,
  OnProgress,
  OnSegment,
  OnStatus,
  Segment,
)

# whisper.cpp expects 16 kHz mono audio.
SAMPLE_RATE = 16000


class Whisper(Model):
  """whisper.cpp, offering the standard model sizes.

  Only one size is held in memory at a time; switching sizes frees the previous
  one before loading the next.
  """

  _OPTIONS = [
    ModelOption("large-v3", "XLarge - Even more accurate, slowest (3GB)",
                short_label="Whisper XLarge"),
    ModelOption("medium", "Large - More accurate, slower (1.5GB)",
                short_label="Whisper Large"),
    ModelOption("small", "Base - Balance between accuracy and speed (500MB)",
                short_label="Whisper Base"),
    ModelOption("base", "Small - Terrible, but fast (140MB)",
                short_label="Whisper Small"),
  ]

  def __init__(self) -> None:
    # pywhispercpp is imported lazily in _load so the app window opens fast.
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
    on_status: OnStatus | None = None,
  ) -> list[Segment]:
    if on_status is not None:
      on_status("Loading the Whisper model...")
    model = self._load(model_key)
    if on_status is not None:
      on_status("Transcribing...")
    raw_segments = model.transcribe(
      media=resample_to_wav(audio_path, SAMPLE_RATE),
      new_segment_callback=lambda segment: on_segment(_to_segment(segment)),
      extract_probability=False,
      progress_callback=on_progress,
      language=language,
      # Anti-hallucination settings from whisper.cpp recommendations:
      # https://github.com/ggerganov/whisper.cpp/issues/897#issuecomment-1521due
      no_context=False,      # allow context (C API defaults to True / off)
      n_max_text_ctx=64,     # but cap it at 64 tokens so loops can't self-reinforce
      temperature_inc=0.1,   # finer fallback steps (default 0.2) per original whisper
      entropy_thold=2.8,     # catch low-entropy repetitive decodes earlier
    )
    return [_to_segment(segment) for segment in raw_segments]

  def unload(self) -> None:
    # Dropping the last reference triggers __del__ -> whisper_free in the
    # bindings, which releases the model's memory.
    self._model = None
    self._loaded_key = None

  def _load(self, model_key: str) -> object:
    if self._model is not None and self._loaded_key == model_key:
      return self._model
    self.unload()
    from pywhispercpp.model import Model as WhisperCppModel
    # params_sampling_strategy=1 -> BEAM_SEARCH (beam_size=5). The default is
    # greedy, which gets stuck in repetition loops on large-v3 (classic symptom:
    # uniform 1-second segments repeating the same phrase at end-of-audio). Beam
    # search explores multiple hypotheses per step and escapes those local
    # minima. Costs ~2-3x inference time but it is the single biggest quality
    # lever whisper.cpp exposes.
    self._model = WhisperCppModel(model_key, params_sampling_strategy=1)
    self._loaded_key = model_key
    return self._model


def _to_segment(segment: object) -> Segment:
  # whisper.cpp reports t0/t1 in centiseconds.
  return Segment(start=segment.t0 / 100, end=segment.t1 / 100, text=segment.text)
