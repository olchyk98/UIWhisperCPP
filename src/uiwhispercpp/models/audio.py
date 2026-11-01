"""Audio helpers shared by the backends.

Both backends need ffmpeg: Whisper because we hand whisper.cpp a pre-resampled
16 kHz mono WAV, and Parakeet because parakeet-mlx loads audio by shelling out
to a bare `ffmpeg` on PATH. We ship ffmpeg through imageio-ffmpeg so the app
stays self-contained, and `ensure_ffmpeg_on_path` exposes it under the plain
name `ffmpeg` so parakeet-mlx can find it even when the OS has none installed.
"""
import os
import subprocess
import time

import imageio_ffmpeg

from uiwhispercpp import app_log
from uiwhispercpp.models.base import UserFacingError

_log = app_log.get("audio")

_TMP_DIR = "/tmp/uiwhispercpp"


class AudioReadError(UserFacingError):
  """A file ffmpeg can't decode, with a short user-facing message."""


# Opening a file normally takes well under a second; a long stall means a
# cloud placeholder that isn't downloaded or an unreachable network share.
_CHECK_TIMEOUT = 120
# Full decodes of very long recordings can take a while, but never this long.
_DECODE_TIMEOUT = 60 * 60


def _ffmpeg_failure(audio_path: str, stderr: str) -> AudioReadError:
  name = os.path.basename(audio_path)
  text = stderr.lower()
  _log.warning("ffmpeg could not read %s: %s", audio_path, stderr.strip()[-500:])
  if "does not contain any stream" in text or "matches no streams" in text:
    return AudioReadError(f"\u201c{name}\u201d has no sound to transcribe.")
  if "permission denied" in text or "operation not permitted" in text:
    return AudioReadError(
      f"UIWhisperCPP isn't allowed to open \u201c{name}\u201d. Allow access in System "
      "Settings > Privacy & Security > Files and Folders, then try again."
    )
  return AudioReadError(
    f"\u201c{name}\u201d can't be opened. It may be damaged or in an unusual format. "
    "If it won't play in QuickTime either, it can't be transcribed."
  )


def _run(audio_path: str, command: list[str], timeout: int) -> subprocess.CompletedProcess:
  try:
    return subprocess.run(command, capture_output=True, timeout=timeout)
  except subprocess.TimeoutExpired as error:
    raise AudioReadError(
      f"\u201c{os.path.basename(audio_path)}\u201d took too long to open. If it's stored "
      "in iCloud or on a network drive, make sure it's fully downloaded, then try again."
    ) from error


def sweep_temp_files(max_age_hours: float = 6) -> None:
  """Delete decoded audio left behind by crashes or forced quits."""
  import glob
  cutoff = time.time() - max_age_hours * 3600
  for path in glob.glob(f"{_TMP_DIR}/*.wav"):
    try:
      if os.path.getmtime(path) < cutoff:
        os.remove(path)
    except OSError:
      pass


def check_audio(audio_path: str) -> None:
  """Fail fast, with a readable message, when `audio_path` has no decodable audio.

  Decodes a fraction of a second of the first audio stream; cheap, and it keeps
  raw ffmpeg dumps out of the UI.
  """
  name = os.path.basename(audio_path)
  if not os.path.isfile(audio_path):
    raise AudioReadError(
      f"\u201c{name}\u201d was moved or deleted. Add it again from its new location."
    )
  result = _run(audio_path, [
    imageio_ffmpeg.get_ffmpeg_exe(), "-nostdin", "-hide_banner", "-v", "error",
    "-i", audio_path, "-map", "0:a:0", "-t", "1", "-ac", "1", "-ar", "8000",
    "-f", "s16le", "-",
  ], _CHECK_TIMEOUT)
  if result.returncode != 0:
    raise _ffmpeg_failure(audio_path, result.stderr.decode("utf-8", "replace"))
  if not result.stdout:
    raise AudioReadError(f"\u201c{name}\u201d is empty: it contains no sound.")


def _run_ffmpeg(audio_path: str, command: list[str]) -> None:
  result = _run(audio_path, command, _DECODE_TIMEOUT)
  if result.returncode != 0:
    raise _ffmpeg_failure(audio_path, result.stderr.decode("utf-8", "replace"))


def resample_to_wav(audio_path: str, frame_rate: int) -> str:
  """Resample `audio_path` to a mono WAV at `frame_rate` and return its path."""
  os.makedirs(_TMP_DIR, exist_ok=True)
  unix = time.time() * 1e6
  out_path = f"{_TMP_DIR}/{unix}__resampled.wav"
  _run_ffmpeg(audio_path, [
    # -vn: video files are accepted too; only their audio track is decoded.
    imageio_ffmpeg.get_ffmpeg_exe(), "-nostdin", "-hide_banner", "-v", "error",
    "-i", audio_path, "-vn", "-ar", str(frame_rate), "-ac", "1", out_path, "-y",
  ])
  return out_path


def decode_to_mono_wav(audio_path: str) -> str:
  """Decode `audio_path` to a mono WAV at its native sample rate (no resampling).

  ffmpeg's default sample-rate converter degrades the signal enough to wreck
  speaker-embedding quality (it collapsed diarization to one speaker). Callers
  that care about spectral fidelity decode at the native rate here and do the
  rate conversion themselves with a high-quality resampler (soxr).
  """
  os.makedirs(_TMP_DIR, exist_ok=True)
  unix = time.time() * 1e6
  out_path = f"{_TMP_DIR}/{unix}__mono.wav"
  _run_ffmpeg(audio_path, [
    imageio_ffmpeg.get_ffmpeg_exe(), "-nostdin", "-hide_banner", "-v", "error",
    "-i", audio_path, "-vn", "-ac", "1", out_path, "-y",
  ])
  return out_path


def ensure_ffmpeg_on_path() -> None:
  """Make the bundled ffmpeg discoverable as a bare `ffmpeg` command.

  parakeet-mlx refuses to start if `ffmpeg` is not on PATH. imageio-ffmpeg ships
  a binary under its own versioned name, so we symlink it to `<tmp>/bin/ffmpeg`
  and prepend that directory to PATH. Idempotent, so it is safe to call before
  every transcription.
  """
  bin_dir = f"{_TMP_DIR}/bin"
  link_path = f"{bin_dir}/ffmpeg"
  ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()

  os.makedirs(bin_dir, exist_ok=True)
  if os.path.realpath(link_path) != os.path.realpath(ffmpeg_exe):
    if os.path.islink(link_path) or os.path.exists(link_path):
      os.remove(link_path)
    os.symlink(ffmpeg_exe, link_path)

  if bin_dir not in os.environ.get("PATH", "").split(os.pathsep):
    os.environ["PATH"] = bin_dir + os.pathsep + os.environ.get("PATH", "")
