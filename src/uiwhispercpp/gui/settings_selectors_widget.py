from dataclasses import dataclass
from PySide6.QtWidgets import (
  QCheckBox,
  QComboBox,
  QHBoxLayout,
  QLabel,
  QSpinBox,
  QVBoxLayout,
  QWidget,
)
from uiwhispercpp.models import ModelOption
from uiwhispercpp.models.diarization import AUTO_SPEAKERS

@dataclass
class Option:
  label: str
  value: str

languages = [
  Option("Auto-detect", ""),
  Option("English", "en"),
  Option("Ukrainian", "uk"),
  Option("Swedish", "sv"),
  Option("Russian", "ru"),
]

# A QSpinBox showing 0 means "I don't know how many speakers" -> auto-detect.
_AUTO_SPEAKERS_SPIN = 0
_MAX_SPEAKERS = 20
# Default when separation is on: most recordings people separate are two-person
# conversations, and pinning the count is far more reliable than auto-detect.
_DEFAULT_SPEAKERS_SPIN = 2


class SettingsSelectorsWidget(QWidget):
  root_layout: QVBoxLayout
  language_select: QComboBox
  model_select: QComboBox
  diarize_checkbox: QCheckBox
  speakers_spin: QSpinBox
  models: list[ModelOption]

  def __init__(self, models: list[ModelOption]):
    super().__init__()
    self.models = models

    self.language_select = QComboBox()
    self.language_select.addItems([ f.label for f in languages ])

    self.model_select = QComboBox()
    self.model_select.addItems([ m.label for m in models ])

    # Speaker diarization is a separate, optional pass: when on, the transcript
    # is split into "Speaker N:" turns. The speaker count is the biggest quality
    # lever, so let the user pin it when known; 0 means auto-detect.
    self.diarize_checkbox = QCheckBox("Separate speakers")
    self.diarize_checkbox.toggled.connect(self._on_diarize_toggled)

    self.speakers_spin = QSpinBox()
    self.speakers_spin.setRange(_AUTO_SPEAKERS_SPIN, _MAX_SPEAKERS)
    self.speakers_spin.setSpecialValueText("Auto")  # shown at the minimum (0)
    self.speakers_spin.setValue(_DEFAULT_SPEAKERS_SPIN)
    self.speakers_spin.setToolTip(
      "How many people are in the recording. Setting the exact count gives the "
      "cleanest separation; 'Auto' (0) guesses but is less reliable. The count "
      "acts as a maximum: voices that sound identical are not split apart."
    )
    self.speakers_spin.setEnabled(False)

    selectors_row = QHBoxLayout()
    selectors_row.addWidget(self.language_select)
    selectors_row.addWidget(self.model_select)

    diarize_row = QHBoxLayout()
    diarize_row.addWidget(self.diarize_checkbox)
    diarize_row.addWidget(QLabel("Speakers:"))
    diarize_row.addWidget(self.speakers_spin)
    diarize_row.addStretch()

    self.root_layout = QVBoxLayout(self)
    self.root_layout.addLayout(selectors_row)
    self.root_layout.addLayout(diarize_row)

  def _on_diarize_toggled(self, checked: bool) -> None:
    # The speaker count only matters when we are actually diarizing.
    self.speakers_spin.setEnabled(checked)

  def get_model (self) -> str:
    index = self.model_select.currentIndex()
    return self.models[index].key

  def get_language (self) -> str:
    index = self.language_select.currentIndex()
    option = languages[index]
    return option.value

  def get_diarize (self) -> bool:
    return self.diarize_checkbox.isChecked()

  def get_num_speakers (self) -> int:
    value = self.speakers_spin.value()
    return AUTO_SPEAKERS if value == _AUTO_SPEAKERS_SPIN else value
