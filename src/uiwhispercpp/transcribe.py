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
    # Anti-repetition-loop settings. large-v3 is prone to locking into
    # a repeat ("Crystal glass and velvet haze..." on loop) at
    # end-of-audio, on music, or over long silences.
    #
    # Note: no_context is NOT listed here because its default is
    # already True in whisper.cpp's default params. Setting it here
    # would be a no-op. It only governs handoff between 30s chunks
    # anyway; the loop we care about is WITHIN a chunk, which is
    # why we switched to beam search in _get_model.
    #
    # Threshold tweaks below make whisper.cpp more aggressive about
    # detecting a bad decode and retrying at a higher temperature:
    #   - entropy_thold: raise so high-confidence loops still trigger fallback
    #   - logprob_thold: tighten "this decode looks bad" bar
    #   - no_speech_thold: lower so silent/instrumental segments get
    #     skipped instead of hallucinated into
    entropy_thold=2.8,
    logprob_thold=-0.5,
    no_speech_thold=0.3,
  )

