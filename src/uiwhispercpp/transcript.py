from pywhispercpp.model import Segment
import os

def format_timestamp (timestamp: float) -> str:
  seconds_float = float(timestamp)

  hours = int(seconds_float // 3600)
  minutes = int((seconds_float % 3600) // 60)
  seconds_int = int(seconds_float % 60)

  milliseconds = int(round((seconds_float % 1) * 1000))

  return f"{hours:02}:{minutes:02}:{seconds_int:02}.{milliseconds:03}"

def project_segment(segment: Segment) -> str:
    # t0 and t1 are stored as CENTISECONDS. 
    # Needs to be divided by 100 to get seconds.
    start = format_timestamp(segment.t0 / 100)
    end = format_timestamp(segment.t1 / 100)
    text = segment.text

    return f"[{start} --> {end}]: {text}"

def project_transcript (segments: list[Segment]) -> str:
  lines: list[str] = []
  for segment in segments:
    line = project_segment(segment)
    lines.append(line)


  return '\n'.join(lines)

def project_and_save_transcript_for_file (
  audio_file_path: str,
  segments: list[Segment]
) -> str:
  transcript = project_transcript(segments)
  transcript_path = os.path.splitext(audio_file_path)[0] + '.txt'
  with open(transcript_path, "w") as f:
    f.write(transcript)
  return transcript_path
