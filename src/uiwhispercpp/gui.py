# XXX: In this file you'll see a lot of use of signals.'
# This is because signals are thread safe. Without them the
# program would crash if we'd try to update any UI elements
# from any sub thread (which we use for transcription).

from PySide6.QtWidgets import QHBoxLayout, QLabel, QProgressBar, QVBoxLayout, QPushButton, QFileDialog
import threading

from pywhispercpp.model import Segment
from uiwhispercpp.transcript import project_and_save_transcript_for_file, project_segment, project_transcript
import sys
from uiwhispercpp.transcribe import transcribe
from PySide6 import QtCore, QtWidgets
from PySide6.QtCore import Signal

DS_STORE = '.DS_Store'

class LoggerWidgetSignalBase(object):
  line_updated = Signal(str)

class LoggerWidget(LoggerWidgetSignalBase, QtWidgets.QTextBrowser):
  lines: list[str]

  def __init__(self, *args, **kwargs) -> None:
    super().__init__(*args, **kwargs)
    self.lines = []
    self.line_updated.connect(self._log)

  def log(self, message: str) -> None:
    # XXX: Can be run from any thread.
    self.line_updated.emit(message)

  @QtCore.Slot(str)
  def _log(self, message: str) -> None:
    # Scrolling to the bottom, if not 
    # viewing some older logs right now
    scrollbar_y = self.verticalScrollBar()
    at_the_bottom = scrollbar_y.maximum() - 30 < scrollbar_y.value()
    # Updating the content
    self.lines.append(message)
    self.setPlainText('\n\n'.join(self.lines))
    if at_the_bottom:
      scrollbar_y.setValue(999999)

class TranscriptionProgressSignalBase(object):
  progress_updated = Signal(int)
  indeterminate_set = Signal(bool)
  visibility_set = Signal(bool)
  jobs_counter_set = Signal(int, int)

class TranscriptionProgress(TranscriptionProgressSignalBase, QHBoxLayout):
  jobs_count_label: QLabel
  progress_bar: QProgressBar
  progress_label: QLabel
  indeterminate: bool

  def __init__(self, *args, **kwargs):
    super().__init__(*args, **kwargs)

    self.progress_bar = QProgressBar()
    self.progress_label = QLabel()
    self.jobs_count_label = QLabel()

    self.addWidget(self.jobs_count_label)
    self.addWidget(self.progress_bar)
    self.addWidget(self.progress_label)

    self.indeterminate = True
    self.set_value(0)
    self.set_indeterminate(False)

    self.progress_updated.connect(self._set_value)
    self.indeterminate_set.connect(self._set_indeterminate)
    self.visibility_set.connect(self._set_visibility)
    self.jobs_counter_set.connect(self._set_jobs_counter)

  def set_jobs_counter(self, running_or_done: int, total: int) -> None:
    self.jobs_counter_set.emit(running_or_done, total)

  def set_value(self, value: int) -> None:
    self.progress_updated.emit(value)
    self.progress_label.setText(f"{value:01}%")

  def set_indeterminate(self, value: bool = True) -> None:
    self.indeterminate_set.emit(value)

  @QtCore.Slot(bool)
  def _set_jobs_counter(self, running_or_done: int, total: int) -> None:
    self.jobs_count_label.setText(f"{running_or_done}/{total}")

  @QtCore.Slot(bool)
  def set_visibility(self, value: bool) -> None:
    self.visibility_set.emit(value)

  @QtCore.Slot(int)
  def _set_value(self, value: int) -> None:
    self.progress_bar.setValue(value)

  @QtCore.Slot(bool)
  def _set_indeterminate(self, value: bool = True) -> None:
    if value:
      if not self.indeterminate:
        # Qt reads 0,0 this as indeterminate
        self.progress_bar.setRange(0, 0)
        self.progress_label.setText("...")
        self.indeterminate = True
    else:
      if self.indeterminate:
        self.progress_bar.setRange(0, 100)
        self.progress_label.setText("?")
        self.indeterminate = False

  def _set_visibility(self, value: bool) -> None:
    if value:
      self.progress_bar.show()
      self.progress_label.show()
      self.jobs_count_label.show()
    else:
      self.progress_bar.hide()
      self.progress_label.hide()
      self.jobs_count_label.hide()

class UploadFileButtonSignalBase(object):
  disabled_value_set = Signal(bool)

class UploadFileButton (UploadFileButtonSignalBase, QPushButton):
  def __init__(self, *args, **kwargs):
    super().__init__(*args, **kwargs)
    self.disabled_value_set.connect(self._set_disabled)

  def set_disabled(self, value: bool) -> None:
    self.disabled_value_set.emit(value)

  def _set_disabled (self, value: bool) -> None:
    self.setDisabled(value)

class View(QtWidgets.QWidget):
  button: UploadFileButton
  layout: QVBoxLayout
  logger: LoggerWidget
  progress: TranscriptionProgress

  def __init__ (self):
    super().__init__()

    self.button = UploadFileButton("Select file/folder")
    self.button.clicked.connect(self.handleSelectFileClick)
    self.logger = LoggerWidget()
    self.progress = TranscriptionProgress()

    self.layout = QtWidgets.QVBoxLayout(self)
    self.layout.addWidget(self.button)
    self.layout.addLayout(self.progress)
    self.layout.addWidget(self.logger)

    self.progress.set_visibility(False)
    self.logger.log("Please, select a file or a folder to start transcribing.")

  @QtCore.Slot()
  def handleSelectFileClick(self):
    audio_paths, _ = QFileDialog.getOpenFileNames(
      self, 
      filter="Audio Files (*.wav *.mp3)"
    )
    self.logger.log(f"Selected {len(audio_paths)} files!")
    def _thread () -> None:
      self.button.set_disabled(True)
      index = 0
      for audio_path in audio_paths:
        try:
          # In case actual transcription dies mid execution,
          # we can at least have the file transcribed partially.
          live_segments: list[Segment] = []
          self.progress.set_jobs_counter(index + 1, len(audio_paths))
          self.logger.log("------ WORKING ------")
          self.logger.log(f"Processing file '{audio_path}'...")
          self.logger.log("Note, this may take a few minutes, as we are warming up the Machine Learning model")
          self.progress.set_visibility(True)
          self.progress.set_indeterminate(True)
          if DS_STORE in audio_path:
            continue
          def handle_chunk(segment: Segment) -> None:
            self.logger.log(project_segment(segment))
            live_segments.append(segment)
          def handle_progress(progress: int):
            self.progress.set_indeterminate(False)
            self.progress.set_value(progress)
          all_segments = transcribe(
            audio_path, 
            on_chunk=handle_chunk,
            on_progress=handle_progress
          )
          transcript_path = project_and_save_transcript_for_file(audio_path, all_segments)
          self.logger.log(f"DONE, WRITTEN TO '{transcript_path}'")
        except Exception as e:
          self.logger.log(f"THE FILE '{audio_path}' FAILED TO PROCESS")
          self.logger.log(str(e))
        finally:
          index += 1
      self.button.set_disabled(False)
    threading.Thread(target=_thread).start()

def run_program () -> None:
  app = QtWidgets.QApplication()
  app.setApplicationName("Transcriber by Oles")

  widget = View()
  widget.resize(400, 400)
  widget.show()

  sys.exit(app.exec())
