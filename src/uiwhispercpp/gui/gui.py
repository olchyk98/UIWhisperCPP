# XXX: In this folder (gui) you'll see a lot of use of signals.'
# This is because signals allow us to call GUI redraw from non GUI thread (which is main thread in this case). Signals forward requests from sub threads to main thread to repaint something. Without them the program would crash if we'd try to update any UI elements from any sub thread (which we use for transcription).

import threading
from uiwhispercpp.gui.logger_widget import LoggerWidget
from uiwhispercpp.gui.settings_selectors_widget import SettingsSelectorsWidget
from uiwhispercpp.gui.transcription_progress import TranscriptionProgress
from uiwhispercpp.gui.upload_file_button import UploadFileButton
from uiwhispercpp.models import Diarizer, ModelManager, Segment, label_segments
from uiwhispercpp.transcript import (
  project_and_save_diarized_transcript_for_file,
  project_and_save_transcript_for_file,
  project_segment,
  project_turn,
)
import sys
from PySide6 import QtCore, QtWidgets

DS_STORE = '.DS_Store'

class View(QtWidgets.QWidget):
  button: UploadFileButton
  settings_selectors: SettingsSelectorsWidget
  root_layout: QtWidgets.QVBoxLayout
  logger: LoggerWidget
  progress: TranscriptionProgress
  model_manager: ModelManager
  diarizer: Diarizer

  def __init__ (self):
    super().__init__()

    self.model_manager = ModelManager()
    self.diarizer = Diarizer()
    self.button = UploadFileButton(callback=self.handle_files_selected)
    self.logger = LoggerWidget()
    self.progress = TranscriptionProgress()
    self.settings_selectors = SettingsSelectorsWidget(self.model_manager.available_models())

    self.root_layout = QtWidgets.QVBoxLayout(self)
    self.root_layout.addWidget(self.settings_selectors)
    self.root_layout.addLayout(self.button)
    self.root_layout.addLayout(self.progress)
    self.root_layout.addWidget(self.logger)

    self.progress.set_visibility(False)
    self.logger.log("Please, select a file or a folder to start transcribing.")

  @QtCore.Slot(list)
  def handle_files_selected(self, audio_paths: list[str]) -> None:
    self.logger.log(f"Selected {len(audio_paths)} files!")
    for audio_path in audio_paths:
      self.logger.log(f"Queued: '{audio_path}'")
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
            project_and_save_transcript_for_file(audio_path, live_segments)
          def handle_progress(progress: int):
            self.progress.set_indeterminate(False)
            self.progress.set_value(progress)
          all_segments = self.model_manager.transcribe(
            audio_path,
            on_segment=handle_chunk,
            on_progress=handle_progress,
            language=self.settings_selectors.get_language(),
            model_key=self.settings_selectors.get_model(),
          )
          if self.settings_selectors.get_diarize():
            transcript_path = self._diarize_and_save(audio_path, all_segments)
          else:
            transcript_path = project_and_save_transcript_for_file(audio_path, all_segments)
          self.logger.log(f"DONE, WRITTEN TO '{transcript_path}'")
        except Exception as e:
          print(e)
          self.logger.log(f"THE FILE '{audio_path}' FAILED TO PROCESS")
          self.logger.log(str(e))
        finally:
          index += 1
      self.button.set_disabled(False)
    threading.Thread(target=_thread).start()

  def _diarize_and_save(self, audio_path: str, segments: list[Segment]) -> str:
    """Run the diarization pass, merge it onto `segments`, and save the result.

    Diarization is a whole-file pass that only runs once transcription has
    finished, so it cannot stream; we show indeterminate progress while the
    models load, then a percentage as the segmentation works through the audio.
    """
    self.logger.log("------ SEPARATING SPEAKERS ------")
    self.progress.set_indeterminate(True)
    def handle_progress(progress: int) -> None:
      self.progress.set_indeterminate(False)
      self.progress.set_value(progress)
    speaker_segments = self.diarizer.diarize(
      audio_path,
      num_speakers=self.settings_selectors.get_num_speakers(),
      on_progress=handle_progress,
      on_log=self.logger.log,
    )
    labeled = label_segments(segments, speaker_segments)
    transcript_path = project_and_save_diarized_transcript_for_file(audio_path, labeled)
    for line in labeled:
      self.logger.log(project_turn(line))
    return transcript_path

def run_program () -> None:
  app = QtWidgets.QApplication()
  app.setApplicationName("Transcriber by Oles")

  widget = View()
  widget.resize(400, 400)
  widget.show()

  sys.exit(app.exec())
