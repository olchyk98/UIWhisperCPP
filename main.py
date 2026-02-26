from pywhispercpp.model import Model
import time
import os
from pydub import AudioSegment
import PySide6.QtCore

print(PySide6.__version__)

FILE_PATH = "output.wav"


# TODO: Make it a select
model = Model("large-v3")

def handle_new_segment (*args, **kwargs) -> None:
  print("---------------")
  print("args", args)
  print("kwargs", kwargs)

  print("Segment RECEIVED!")

def downgrade_audio (p: str) -> str:
  # NOTE: Whisper requires audio in 16kHZ, 
  # therefore we have to downsize it first
  base = "/tmp/uiwhispercc"
  if not os.path.exists(base):
    os.makedirs(base)
  unix = time.time() * 1e6
  file_path = f"{base}/{unix}.wav"
  audio: AudioSegment = AudioSegment.from_file(p)
  with_16khz = audio.set_frame_rate(16000)
  with_16khz.export(file_path, format="wav")
  return file_path


# TODO: Make it a file (folder) picker 
# Maybe a split section tab on the top of the screen
# allowing to select between having a folder or 
# a file selector
segments = model.transcribe(
  media=downgrade_audio(FILE_PATH),
  new_segment_callback=handle_new_segment
)
