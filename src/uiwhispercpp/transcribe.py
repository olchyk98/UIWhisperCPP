from collections.abc import Callable
from pywhispercpp.model import Model, Segment
import time
import os
import imageio_ffmpeg
from pydub import AudioSegment

# Point pydub at the ffmpeg binary that ships with imageio-ffmpeg,
# so the packaged .app doesn't depend on a system-installed ffmpeg.
AudioSegment.converter = imageio_ffmpeg.get_ffmpeg_exe()

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
_cached_model_key: str | None = None
def _get_model (key: str) -> Model:
  global _cached_model
  global _cached_model_key
  # To not take too much memory, we're going to
  # hard reset previous model and torch cache, before
  # loading a new model. This ensures, that we only
  # have a single model loaded at any given time.
  #
  # -> __del__ in the bindings calls 
  # .dispose() (whisper_free) on the internal C object.
  if _cached_model is not None:
    if _cached_model_key == key:
      return _cached_model
    del _cached_model
    _cached_model_key = None
  # params_sampling_strategy=1 -> BEAM_SEARCH (beam_size=5).
  # Default is greedy, which gets stuck in repetition loops on large-v3
  # (classic symptom: uniform 1-second segments repeating the same phrase
  # at end-of-audio). Beam search explores multiple hypotheses per step
  # and escapes those local minima. Costs ~2-3x inference time but it's
  # the single biggest quality lever whisper.cpp exposes.
  _cached_model = Model(key, params_sampling_strategy=1)
  _cached_model_key = key
  return _cached_model

def transcribe (
  file_path: str,
  /,
  on_chunk: Callable[[Segment], None],
  on_progress: Callable[[int], None],
  language: str,
  model_key: str
) -> list[Segment]:
  model = _get_model(model_key)
  return model.transcribe(
    # NOTE: Whisper requires audio in 16kHZ,
    # therefore we have to downsize it first
    media=resample_audio(file_path, 16000),
    new_segment_callback=on_chunk,
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

