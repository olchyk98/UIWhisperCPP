"""Turn transcribed segments into the text the user saves.

`OutputOptions` holds the user's output preferences (set in Settings > Output):
timestamp style, precision, speaker labels and file type. Everything that
renders a transcript - the saved file, the live preview pane and the sample in
Settings - goes through `render_transcript`, so they always match.
"""
from dataclasses import dataclass
import os
import stat
import tempfile

from uiwhispercpp.models import Segment, Turn

TIMESTAMPS_RANGE = "range"
TIMESTAMPS_START = "start"
TIMESTAMPS_OFF = "off"

PRECISION_MS = "ms"
PRECISION_SECONDS = "s"

FILE_TXT = "txt"
FILE_SRT = "srt"


@dataclass(frozen=True)
class OutputOptions:
  timestamps: str = TIMESTAMPS_RANGE
  precision: str = PRECISION_MS
  speaker_labels: bool = True
  file_type: str = FILE_TXT


@dataclass(frozen=True)
class Line:
  """One transcript line; `speaker` is None when speakers were not separated."""
  start: float
  end: float
  text: str
  speaker: int | None = None


def lines_from_segments(segments: list[Segment]) -> list[Line]:
  return [Line(s.start, s.end, s.text.strip()) for s in segments]


def lines_from_turns(turns: list[Turn]) -> list[Line]:
  return [Line(t.start, t.end, t.text.strip(), t.speaker) for t in turns]


def format_timestamp(
  timestamp: float,
  precision: str = PRECISION_MS,
  decimal_mark: str = ".",
) -> str:
  total_ms = int(round(max(0.0, float(timestamp)) * 1000))
  hours, rest = divmod(total_ms, 3_600_000)
  minutes, rest = divmod(rest, 60_000)
  seconds, milliseconds = divmod(rest, 1000)
  base = f"{hours:02}:{minutes:02}:{seconds:02}"
  if precision == PRECISION_SECONDS:
    return base
  return f"{base}{decimal_mark}{milliseconds:03}"


def render_line(line: Line, options: OutputOptions) -> str:
  """One line of a .txt transcript."""
  parts: list[str] = []
  if options.timestamps == TIMESTAMPS_RANGE:
    start = format_timestamp(line.start, options.precision)
    end = format_timestamp(line.end, options.precision)
    parts.append(f"[{start} --> {end}]")
  elif options.timestamps == TIMESTAMPS_START:
    parts.append(f"[{format_timestamp(line.start, options.precision)}]")
  if line.speaker is not None and options.speaker_labels:
    parts.append(f"[Speaker {line.speaker}]")
  if not parts:
    return line.text
  return f"{' '.join(parts)}: {line.text}"


def _render_srt(lines: list[Line], options: OutputOptions) -> str:
  # SRT always carries full millisecond ranges: players need them.
  blocks: list[str] = []
  for index, line in enumerate(lines, start=1):
    start = format_timestamp(line.start, PRECISION_MS, decimal_mark=",")
    end = format_timestamp(line.end, PRECISION_MS, decimal_mark=",")
    text = line.text
    if line.speaker is not None and options.speaker_labels:
      text = f"[Speaker {line.speaker}] {text}"
    blocks.append(f"{index}\n{start} --> {end}\n{text}")
  return "\n\n".join(blocks) + ("\n" if blocks else "")


def render_transcript(lines: list[Line], options: OutputOptions) -> str:
  if options.file_type == FILE_SRT:
    return _render_srt(lines, options)
  return "\n".join(render_line(line, options) for line in lines)


def transcript_path_for(audio_path: str, options: OutputOptions) -> str:
  """The default transcript path: `talk.mp3` -> `talk.txt`.

  When another audio/video file with the same name sits in the folder
  (`talk.mp4`, also differing only in letter case), the extension is kept
  (`talk.mp3.txt`) so the two transcripts never land on the same file.
  The result is never the audio file itself or another media file.
  """
  from uiwhispercpp.audio_formats import is_audio_file

  file_type = options.file_type if options.file_type in (FILE_TXT, FILE_SRT) else FILE_TXT
  stem, _extension = os.path.splitext(audio_path)
  folder, name = os.path.split(stem)
  own = _normalized(os.path.basename(audio_path))
  try:
    siblings = [
      entry for entry in os.listdir(folder or ".")
      if _normalized(os.path.splitext(entry)[0]) == _normalized(name)
      and is_audio_file(entry) and _normalized(entry) != own
    ]
  except OSError:
    siblings = []
  return (audio_path if siblings else stem) + "." + file_type


def numbered_path(path: str, taken: set[str] | None = None) -> str:
  """`talk.txt` -> the first free `talk (2).txt`, `talk (3).txt`, ...

  `taken` holds paths already promised to other files, which also count as used.
  """
  stem, extension = os.path.splitext(path)
  number = 2
  while True:
    candidate = f"{stem} ({number}){extension}"
    if not os.path.exists(candidate) and candidate not in (taken or set()):
      return candidate
    number += 1


def _normalized(name: str) -> str:
  import unicodedata
  return unicodedata.normalize("NFC", name).casefold()


def write_transcript(path: str, lines: list[Line], options: OutputOptions) -> str:
  """Write the transcript to exactly `path` and return it."""
  text = render_transcript(lines, options)
  # End text files with a newline, as POSIX tools expect (SRT already does).
  if text and not text.endswith("\n"):
    text += "\n"
  # Write next to the target and swap it in, so a failed write (disk full)
  # never leaves the existing file empty or half-written.
  # mkstemp picks an unpredictable name and refuses to follow a planted
  # symlink, so the write can't be redirected onto another file.
  folder = os.path.dirname(path) or "."
  fd, temporary = tempfile.mkstemp(dir=folder, prefix=f".{os.path.basename(path)}.",
                                   suffix=".tmp")
  try:
    with os.fdopen(fd, "w", encoding="utf-8") as f:
      f.write(text)
    # mkstemp creates the file owner-only; keep the usual readable mode.
    os.chmod(temporary, _mode_for(path))
    os.replace(temporary, path)
  except BaseException:
    try:
      os.remove(temporary)
    except OSError:
      pass
    raise
  return path


def _mode_for(path: str) -> int:
  """Keep an existing file's permissions; new files get rw-r--r--."""
  try:
    return stat.S_IMODE(os.stat(path).st_mode)
  except OSError:
    return 0o644


def save_transcript(audio_path: str, lines: list[Line], options: OutputOptions) -> str:
  """Write the transcript next to the audio file (default name) and return its path."""
  return write_transcript(transcript_path_for(audio_path, options), lines, options)


SAMPLE_LINES = [
  Line(1.2, 4.85, "Hi, thanks for joining the call today.", 0),
  Line(5.1, 7.4, "Happy to be here.", 1),
]
