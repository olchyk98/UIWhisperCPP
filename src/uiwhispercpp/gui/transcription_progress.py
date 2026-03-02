from PySide6.QtCore import Signal, Slot
from PySide6.QtWidgets import QHBoxLayout, QLabel, QProgressBar


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

  @Slot(bool)
  def _set_jobs_counter(self, running_or_done: int, total: int) -> None:
    self.jobs_count_label.setText(f"{running_or_done}/{total}")

  @Slot(bool)
  def set_visibility(self, value: bool) -> None:
    self.visibility_set.emit(value)

  @Slot(int)
  def _set_value(self, value: int) -> None:
    self.progress_bar.setValue(value)

  @Slot(bool)
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

