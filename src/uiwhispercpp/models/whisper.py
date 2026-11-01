"""Whisper backend, powered by whisper.cpp through pywhispercpp."""
import os
import re

from uiwhispercpp import app_log
from uiwhispercpp.models.audio import resample_to_wav
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
from uiwhispercpp.models.downloads import DownloadError, download, format_size

# whisper.cpp expects 16 kHz mono audio.
SAMPLE_RATE = 16000
_MODEL_URL = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-{key}.bin"
# Every ggml model file starts with the bytes "lmgg" (the magic 0x67676d6c).
_GGML_MAGIC = b"lmgg"
# whisper.cpp emits these for non-speech; they are not transcript text.
_NON_SPEECH = re.compile(
  r"^\s*[\[(](blank_audio|silence|music|no speech|inaudible|applause|laughter)[\])]\s*$",
  re.IGNORECASE,
)

_log = app_log.get("whisper")

# Exact sizes of the official ggml files. whisper.cpp crashes the whole process
# on a truncated model (its magic bytes still look fine), so a file of any
# other size is treated as damaged and downloaded again.
_EXPECTED_SIZES = {
  "base": 147_951_465,
  "small": 487_601_967,
  "medium": 1_533_763_059,
  "large-v3": 3_095_033_483,
}


def _models_dir() -> str:
  # The folder pywhispercpp itself uses, so already downloaded models are reused.
  from pywhispercpp.constants import MODELS_DIR
  return str(MODELS_DIR)


class Whisper(Model):
  """whisper.cpp, offering the standard model sizes.

  Only one size is held in memory at a time; switching sizes frees the previous
  one before loading the next. Labels name the real whisper model each entry
  loads, with its true download size.
  """

  _OPTIONS = [
    ModelOption("large-v3", "Whisper Large v3 - most accurate, slowest (3.1 GB)",
                short_label="Whisper Large v3"),
    ModelOption("medium", "Whisper Medium - accurate, slower (1.5 GB)",
                short_label="Whisper Medium"),
    ModelOption("small", "Whisper Small - balanced speed and accuracy (490 MB)",
                short_label="Whisper Small"),
    ModelOption("base", "Whisper Base - quick draft, least accurate (150 MB)",
                short_label="Whisper Base"),
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
    stop: StopToken | None = None,
  ) -> list[Segment]:
    status = on_status or (lambda _message: None)
    model = self._load(model_key, status, stop)
    status("Transcribing...")

    def segment_callback(raw: object) -> None:
      # Raising here aborts whisper_full cleanly, which is how Stop works.
      if stop is not None:
        stop.check()
      segment = _to_segment(raw)
      if segment is not None:
        on_segment(segment)

    def progress_callback(percent: int) -> None:
      if stop is not None:
        stop.check()
      on_progress(percent)

    wav_path = resample_to_wav(audio_path, SAMPLE_RATE)
    try:
      raw_segments = model.transcribe(
        media=wav_path,
        new_segment_callback=segment_callback,
        extract_probability=False,
        progress_callback=progress_callback,
        language=language,
        # Anti-hallucination settings from whisper.cpp recommendations:
        # https://github.com/ggerganov/whisper.cpp/issues/897
        # no_context=True only clears the carried-over prompt at the start of
        # each file (so one file's words can't leak into the next); within a
        # file whisper.cpp still conditions on the previous text, capped below.
        no_context=True,
        n_max_text_ctx=64,     # cap context at 64 tokens so loops can't self-reinforce
        temperature_inc=0.1,   # finer fallback steps (default 0.2) per original whisper
        entropy_thold=2.8,     # catch low-entropy repetitive decodes earlier
      )
    finally:
      try:
        os.remove(wav_path)
      except OSError:
        pass
    return [s for s in (_to_segment(raw) for raw in raw_segments) if s is not None]

  def unload(self) -> None:
    # Dropping the last reference triggers __del__ -> whisper_free in the
    # bindings, which releases the model's memory.
    self._model = None
    self._loaded_key = None

  def _load(self, model_key: str, status: OnStatus, stop: StopToken | None) -> object:
    if self._model is not None and self._loaded_key == model_key:
      return self._model
    self.unload()
    path = self._ensure_model_file(model_key, status, stop)
    status("Loading the model...")
    from pywhispercpp.model import Model as WhisperCppModel
    # params_sampling_strategy=1 -> BEAM_SEARCH (beam_size=5). The default is
    # greedy, which gets stuck in repetition loops on large-v3 (classic symptom:
    # uniform 1-second segments repeating the same phrase at end-of-audio). Beam
    # search explores multiple hypotheses per step and escapes those local
    # minima. Costs ~2-3x inference time but it is the single biggest quality
    # lever whisper.cpp exposes.
    model = WhisperCppModel(path, params_sampling_strategy=1)
    if getattr(model, "_ctx", None) is None:
      _remove(path)
      raise UserFacingError(
        "The Whisper model file was damaged and has been removed. "
        "Try again to download it fresh."
      )
    self._model = model
    self._loaded_key = model_key
    return self._model

  def _ensure_model_file(self, model_key: str, status: OnStatus, stop: StopToken | None) -> str:
    """Return a verified local model file, downloading it safely if needed."""
    path = os.path.join(_models_dir(), f"ggml-{model_key}.bin")
    if os.path.exists(path) and not _is_complete(path, model_key):
      _log.warning("Removing damaged Whisper model file %s", path)
      _remove(path)
    if os.path.exists(path):
      return path
    label = next(o.short_label for o in self._OPTIONS if o.key == model_key)

    def on_bytes(done: int, total: int) -> None:
      if not done and not total:
        return  # still connecting; the generic status covers it
      if total:
        status(
          f"Downloading {label} ({format_size(done)} of {format_size(total)}, "
          f"one-time) {done * 100 // total}%"
        )
      else:
        status(f"Downloading {label} ({format_size(done)}, one-time)...")

    download(
      _MODEL_URL.format(key=model_key), path, what=f"{label} model",
      on_bytes=on_bytes, stop=stop, min_size=10_000_000,
    )
    if not _is_complete(path, model_key):
      _remove(path)
      raise DownloadError(
        f"The {label} download wasn't a valid model file. Check your connection "
        "(a Wi-Fi login page can cause this) and try again."
      )
    return path


def _is_complete(path: str, model_key: str) -> bool:
  expected = _EXPECTED_SIZES.get(model_key)
  try:
    size_ok = expected is None or os.path.getsize(path) == expected
  except OSError:
    return False
  return size_ok and _looks_like_ggml(path)


def _looks_like_ggml(path: str) -> bool:
  try:
    with open(path, "rb") as f:
      return f.read(4) == _GGML_MAGIC
  except OSError:
    return False


def _remove(path: str) -> None:
  try:
    os.remove(path)
  except OSError:
    pass


def _to_segment(segment: object) -> Segment | None:
  # whisper.cpp reports t0/t1 in centiseconds; drop non-speech markers.
  text = segment.text
  if not text.strip() or _NON_SPEECH.match(text):
    return None
  return Segment(start=segment.t0 / 100, end=segment.t1 / 100, text=text)
