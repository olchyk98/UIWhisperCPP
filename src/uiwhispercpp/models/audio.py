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


def resample_to_wav(audio_path: str, frame_rate: int) -> str:
  """Resample `audio_path` to a mono WAV at `frame_rate` and return its path."""
  os.makedirs(_TMP_DIR, exist_ok=True)
  unix = time.time() * 1e6
  out_path = f"{_TMP_DIR}/{unix}__resampled.wav"
  subprocess.run(
    [imageio_ffmpeg.get_ffmpeg_exe(),
     "-i", audio_path, "-ar", str(frame_rate), "-ac", "1", out_path, "-y"],
    check=True,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
  )
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
