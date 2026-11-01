"""The single entry point the app uses to list and run transcription models."""
from uiwhispercpp.models.base import (
  Model,
  ModelOption,
  OnProgress,
  OnSegment,
  OnStatus,
  Segment,
  StopToken,
  UserFacingError,
)
from uiwhispercpp.models.audio import check_audio
from uiwhispercpp.models.parakeet import Parakeet
from uiwhispercpp.models.whisper import Whisper


class ModelManager:
  """Owns every transcription backend and routes work to the right one.

  The rest of the app only talks to a ModelManager: it asks for the list of
  available models to populate the UI, then asks it to transcribe with the
  chosen model. Which backend (Whisper, Parakeet, ...) does the work is an
  internal detail. To bound memory, only one model is kept loaded at a time.
  """

  def __init__(self) -> None:
    self._backends: list[Model] = [Parakeet(), Whisper()]
    self._backend_by_key = {
      option.key: backend
      for backend in self._backends
      for option in backend.options
    }
    self._active_backend: Model | None = None

  def available_models(self) -> list[ModelOption]:
    """Every model, in display order (unsupported ones carry a reason)."""
    return [option for backend in self._backends for option in backend.options]

  def default_model_key(self) -> str:
    """The first model this Mac can actually run."""
    models = self.available_models()
    usable = [m for m in models if not m.unavailable_reason]
    return (usable or models)[0].key

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
    """Transcribe `audio_path` with the model identified by `model_key`."""
    backend = self._backend_by_key.get(model_key)
    if backend is None:
      raise ValueError(f"Unknown model '{model_key}'.")
    option = next(o for o in backend.options if o.key == model_key)
    if option.unavailable_reason:
      raise UserFacingError(option.unavailable_reason)
    check_audio(audio_path)

    # Keep a single model in memory: release the previous backend's weights
    # before a different backend loads its own.
    if self._active_backend is not None and self._active_backend is not backend:
      self._active_backend.unload()
    self._active_backend = backend

    return backend.transcribe(
      audio_path,
      model_key=model_key,
      language=language,
      on_segment=on_segment,
      on_progress=on_progress,
      on_status=on_status,
      stop=stop,
    )
