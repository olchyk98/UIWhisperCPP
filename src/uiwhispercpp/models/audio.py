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

_TMP_DIR = "/tmp/uiwhispercpp"


class AudioReadError(RuntimeError):
  """A file ffmpeg can't decode, with a short user-facing message."""


def _ffmpeg_failure(audio_path: str, stderr: str) -> AudioReadError:
  name = os.path.basename(audio_path)
  text = stderr.lower()
  if "does not contain any stream" in text or "matches no streams" in text:
    return AudioReadError(f"'{name}' has no audio track to transcribe.")
  lines = [line.strip() for line in stderr.strip().splitlines() if line.strip()]
  reason = lines[-1] if lines else "unknown error"
  # ffmpeg prefixes the reason with the input path; keep only the reason.
  if ": " in reason and audio_path in reason:
    reason = reason.split(": ", 1)[1] if reason.startswith(audio_path) else reason
  return AudioReadError(f"'{name}' couldn't be read as audio ({reason}).")


def check_audio(audio_path: str) -> None:
  """Fail fast, with a readable message, when `audio_path` has no decodable audio.

  Decodes a fraction of a second of the first audio stream; cheap, and it keeps
  raw ffmpeg dumps out of the UI.
  """
  if not os.path.isfile(audio_path):
    raise AudioReadError(f"'{os.path.basename(audio_path)}' no longer exists.")
  result = subprocess.run(
    [imageio_ffmpeg.get_ffmpeg_exe(), "-nostdin", "-hide_banner", "-v", "error",
     "-i", audio_path, "-map", "0:a:0", "-t", "0.5", "-f", "null", "-"],
    capture_output=True,
    text=True,
  )
  if result.returncode != 0:
    raise _ffmpeg_failure(audio_path, result.stderr)


def _run_ffmpeg(audio_path: str, command: list[str]) -> None:
  result = subprocess.run(command, capture_output=True, text=True)
  if result.returncode != 0:
    raise _ffmpeg_failure(audio_path, result.stderr)


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
