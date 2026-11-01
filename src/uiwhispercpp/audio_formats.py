"""Audio (and video) container types the app accepts.

Every backend decodes through the bundled ffmpeg, so anything ffmpeg reads
works. Video files are accepted too: only their audio track is used.
"""
import os

AUDIO_EXTENSIONS: tuple[str, ...] = (
  "wav", "mp3", "m4a", "wma", "flac", "ogg", "oga", "opus", "aac", "aif",
  "aiff", "aifc", "caf", "amr", "ac3", "m4b", "mka", "wv", "webm", "mp4",
  "mov", "mkv", "3gp", "avi",
)


def is_audio_file(path: str) -> bool:
  extension = os.path.splitext(path)[1].lower().lstrip(".")
  return extension in AUDIO_EXTENSIONS and not os.path.basename(path).startswith(".")


def file_dialog_filter() -> str:
  patterns = " ".join(
    f"*.{ext} *.{ext.upper()}" for ext in AUDIO_EXTENSIONS
  )
  return f"Audio & video files ({patterns});;All files (*)"


def expand_paths(paths: list[str]) -> tuple[list[str], list[str]]:
  """Turn dropped/selected files and folders into a sorted list of audio files.

  Folders contribute the audio files directly inside them (not sub-folders),
  matching the "Add folder" button. Returns (audio files, folders that could
  not be opened).
  """
  found: list[str] = []
  unreadable: list[str] = []
  for path in paths:
    if os.path.isdir(path):
      try:
        entries = sorted(os.listdir(path), key=str.lower)
      except OSError:
        unreadable.append(path)
        continue
      found += [
        os.path.join(path, name) for name in entries
        if os.path.isfile(os.path.join(path, name)) and is_audio_file(name)
      ]
    elif os.path.isfile(path) and is_audio_file(path):
      found.append(path)
  return found, unreadable
