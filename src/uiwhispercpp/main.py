from PySide6.QtWidgets import QProgressBar, QVBoxLayout, QPushButton, QFileDialog
from uiwhispercpp.transcript import project_and_save_transcript_for_file
import sys
from uiwhispercpp.transcribe import transcribe
from PySide6 import QtCore, QtWidgets

# TODO: Split into gui
# TODO: Block non wav/mp3 files
# TODO: Provide display/logging/progress/error handling
# TODO: Provide support for folders
# TODO: Allow to select model size
# TODO: Allow to specify language


DS_STORE = '.DS_Store'

class View(QtWidgets.QWidget):
  button: QPushButton
  layout: QVBoxLayout
  progress: QProgressBar

  def __init__ (self):
    super().__init__()

    self.button = QtWidgets.QPushButton("Select file/folder")
    self.button.clicked.connect(self.handleSelectFileClick)

    self.layout = QtWidgets.QVBoxLayout(self)
    self.layout.addWidget(self.button)

    self.setAcceptDrops(True)

  @QtCore.Slot()
  def handleSelectFileClick(self):
    audio_paths, _ = QFileDialog.getOpenFileNames(self)
    # TODO: In case actual transcription dies mid execution,
    # we can at least have the file transcribed partially.
    # Introduce "progressive_segments" 
    for audio_path in audio_paths:
      if DS_STORE in audio_path:
        continue
      all_segments = transcribe(
        audio_path, 
        on_chunk=lambda segment: print(f"'{audio_path}': {segment.text}")
      )
      project_and_save_transcript_for_file(audio_path, all_segments)


def main() -> None:
  app = QtWidgets.QApplication()

  widget = View()
  widget.resize(400, 400)
  widget.show()

  sys.exit(app.exec())
