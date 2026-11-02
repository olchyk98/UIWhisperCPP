"""The bar under the panes: what to transcribe with, plus Settings."""
from PySide6 import QtWidgets
from PySide6.QtCore import Qt, Signal

from uiwhispercpp.gui.widgets import SHORT_TEXT_ROLE, CompactComboBox
from uiwhispercpp.models import ModelManager
from uiwhispercpp.models.diarization import AUTO_SPEAKERS
from uiwhispercpp.settings import Settings

LANGUAGES = [
  ("Auto-detect language", ""),
  ("English", "en"),
  ("Ukrainian", "uk"),
  ("Swedish", "sv"),
  ("Russian", "ru"),
  ("German", "de"),
  ("French", "fr"),
  ("Spanish", "es"),
  ("Italian", "it"),
  ("Polish", "pl"),
  ("Dutch", "nl"),
  ("Portuguese", "pt"),
]
LANGUAGE_NAMES = {code: label for label, code in LANGUAGES}
# 0 in the spin box means "not sure how many people" -> auto-detect.
_AUTO_SPEAKERS_SPIN = 0
_MAX_SPEAKERS = 20
# Most recordings people separate are two-person conversations, and pinning
# the count is far more reliable than auto-detect.
_DEFAULT_SPEAKERS_SPIN = 2
_PARAKEET_PREFIX = "mlx-community/parakeet"


class BottomBar(QtWidgets.QWidget):
  """Model, language and speaker controls, plus the Settings button."""

  changed = Signal()

  def __init__(self, manager: ModelManager, settings: Settings) -> None:
    super().__init__()
    self.setObjectName("BottomBar")
    self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    self._settings = settings
    self.models = manager.available_models()

    self.model = CompactComboBox()
    model_items = self.model.model()
    for index, option in enumerate(self.models):
      label = option.label
      if option.unavailable_reason:
        label = f"{label} - needs a Mac with Apple Silicon"
      self.model.addItem(label, option.key)
      self.model.setItemData(index, option.short_label or option.label, SHORT_TEXT_ROLE)
      if option.unavailable_reason:
        item = model_items.item(index)
        item.setEnabled(False)
        item.setToolTip(option.unavailable_reason)
    saved = settings.text("ui/model")
    usable = {o.key for o in self.models if not o.unavailable_reason}
    chosen = saved if saved in usable else manager.default_model_key()
    self.model.setCurrentIndex(max(0, self.model.findData(chosen)))
    self.model.setToolTip(
      "Which speech model turns the audio into text. Each one downloads once, "
      "the first time you use it, then works offline."
    )

    self.language = CompactComboBox()
    for label, code in LANGUAGES:
      self.language.addItem(label, code)
    self.language.setCurrentIndex(max(0, self.language.findData(settings.text("ui/language"))))

    self.diarize = QtWidgets.QCheckBox("Separate speakers")
    self.diarize.setToolTip(
      "Marks each line with who said it (Speaker 0, Speaker 1, ...). "
      "Adds a short extra step after transcribing."
    )
    self.diarize.setChecked(settings.flag("ui/diarize", False))
    self.people_label = QtWidgets.QLabel("People:")
    self.speakers = QtWidgets.QSpinBox()
    self.speakers.setRange(_AUTO_SPEAKERS_SPIN, _MAX_SPEAKERS)
    self.speakers.setSpecialValueText("Not sure")
    self.speakers.setFixedWidth(86)
    self.speakers.setValue(
      settings.integer("ui/speakers", _DEFAULT_SPEAKERS_SPIN, _AUTO_SPEAKERS_SPIN, _MAX_SPEAKERS)
    )
    self.speakers.setToolTip(
      "How many people talk in the recording. The exact number works best; "
      "\"Not sure\" lets the app guess."
    )

    self.settings_button = QtWidgets.QPushButton("Settings")
    self.settings_button.setObjectName("Bordered")
    self.settings_button.setToolTip("Transcript format and updates (⌘,)")

    layout = QtWidgets.QHBoxLayout(self)
    layout.setContentsMargins(14, 8, 14, 8)
    layout.setSpacing(8)
    layout.addWidget(self.model)
    layout.addWidget(self.language)
    layout.addSpacing(10)
    layout.addWidget(self.diarize)
    layout.addSpacing(6)
    layout.addWidget(self.people_label)
    layout.addWidget(self.speakers)
    layout.addStretch()
    layout.addWidget(self.settings_button)

    self.model.currentIndexChanged.connect(self._on_change)
    self.language.currentIndexChanged.connect(self._on_change)
    self.diarize.toggled.connect(self._on_change)
    self.speakers.valueChanged.connect(self._on_change)
    self._refresh()

  def _refresh(self) -> None:
    show_people = self.diarize.isChecked()
    self.people_label.setVisible(show_people)
    self.speakers.setVisible(show_people)
    # Parakeet detects the language itself and takes no hint, so the menu stays
    # in place but is greyed out (the saved choice is kept for Whisper).
    automatic = self.model_key().startswith(_PARAKEET_PREFIX)
    self.language.setEnabled(not automatic)
    self.language.setToolTip(
      "Parakeet recognises the language by itself. Choose a Whisper model to set it."
      if automatic else
      "The language spoken in the recording. Leave on Auto-detect if unsure."
    )

  def _on_change(self) -> None:
    self._refresh()
    self._settings.set_value("ui/model", self.model_key())
    self._settings.set_value("ui/language", self.language_code())
    self._settings.set_value("ui/diarize", "true" if self.diarize.isChecked() else "false")
    self._settings.set_value("ui/speakers", self.speakers.value())
    self.changed.emit()

  def model_key(self) -> str:
    return self.model.currentData() or ""

  def model_name(self) -> str:
    return self.model.currentData(SHORT_TEXT_ROLE) or self.model.currentText()

  def language_code(self) -> str:
    return self.language.currentData() or ""

  def num_speakers(self) -> int | None:
    """None when not separating speakers; AUTO_SPEAKERS for auto-detect."""
    if not self.diarize.isChecked():
      return None
    value = self.speakers.value()
    return AUTO_SPEAKERS if value == _AUTO_SPEAKERS_SPIN else value

  def summary(self) -> str:
    """One muted line describing what a file will be transcribed with."""
    parts = [self.model_name()]
    if self.model_key().startswith(_PARAKEET_PREFIX):
      parts.append("language detected automatically")
    else:
      parts.append(LANGUAGE_NAMES.get(self.language_code(), "Auto-detect language"))
    speakers = self.num_speakers()
    if speakers is not None:
      parts.append(
        "separating speakers" if speakers == AUTO_SPEAKERS else f"{speakers} speakers"
      )
    return " · ".join(parts)


