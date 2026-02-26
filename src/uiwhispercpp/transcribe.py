from collections.abc import Callable
from pywhispercpp.model import Model, Segment
import time
import os
from pydub import AudioSegment

# TODO: Make it a select
MODEL_KEY = "large-v3"

def resample_audio (p: str, frame_rate: int) -> str:
  base = "/tmp/uiwhispercpp"
  if not os.path.exists(base):
    os.makedirs(base)
  unix = time.time() * 1e6
  file_path = f"{base}/{unix}__resampled.wav"
  audio: AudioSegment = AudioSegment.from_file(p)
  with_16khz = audio.set_frame_rate(frame_rate)
  with_16khz.export(file_path, format="wav")
  return file_path

_cached_model: Model | None = None
def _get_model () -> Model:
  global _cached_model
  if _cached_model is None:
    _cached_model = Model(MODEL_KEY)
  return _cached_model

def transcribe (
  file_path: str,
  /,
  on_chunk: Callable[[Segment], None]
) -> list[Segment]:
  model = _get_model()
  return model.transcribe(
    # NOTE: Whisper requires audio in 16kHZ, 
    # therefore we have to downsize it first
    media=resample_audio(file_path, 16000),
    new_segment_callback=on_chunk
  )

