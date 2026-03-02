from collections.abc import Callable
import os
from PySide6.QtCore import QDir, Signal, Slot
from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QPushButton


class UploadFileButtonSignalBase(object):
  disabled_value_set = Signal(bool)

class UploadFileButton (UploadFileButtonSignalBase, QHBoxLayout):
  select_files_button: QPushButton
  select_folder_button: QPushButton
  callback: Callable[[list[str]], None]

  def __init__(
    self,
    callback: Callable[[list[str]], None]
  ):
    super().__init__()
    self.disabled_value_set.connect(self._set_disabled)
    self.callback = callback

    self.select_files_button = QPushButton("Select file(s)")
    self.select_files_button.clicked.connect(self.handle_select_files_click)
    self.addWidget(self.select_files_button)

    self.select_folder_button = QPushButton("Select folder")
    self.select_folder_button.clicked.connect(self.handle_select_folder_click)
    self.addWidget(self.select_folder_button)


  @Slot()
  def handle_select_files_click (self) -> None:
    audio_paths, _ = QFileDialog.getOpenFileNames(
      self.parentWidget(), 
      filter="Audio Files (*.wav *.mp3)",
    )
    self.callback(audio_paths)

  @Slot()
  def handle_select_folder_click (self) -> None:
    dir_path = QFileDialog.getExistingDirectory(
      self.parentWidget(),
    )
    qdir = QDir(dir_path)
    relative_audio_paths = qdir.entryList(["*.wav", "*.mp3"])
    audio_paths = [ os.path.join(dir_path, p) for p in relative_audio_paths ]
    self.callback(audio_paths)

  def set_disabled(self, value: bool) -> None:
    self.disabled_value_set.emit(value)

  def _set_disabled (self, value: bool) -> None:
    self.select_files_button.setDisabled(value)
    self.select_folder_button.setDisabled(value)
