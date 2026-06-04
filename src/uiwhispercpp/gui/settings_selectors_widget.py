from dataclasses import dataclass
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QWidget
from uiwhispercpp.models import ModelOption

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


class SettingsSelectorsWidget(QWidget):
  root_layout: QHBoxLayout
  language_select: QComboBox
  model_select: QComboBox
  models: list[ModelOption]

  def __init__(self, models: list[ModelOption]):
    super().__init__()
    self.models = models

    self.language_select = QComboBox()
    self.language_select.addItems([ f.label for f in languages ])

    self.model_select = QComboBox()
    self.model_select.addItems([ m.label for m in models ])

    self.root_layout = QHBoxLayout(self)
    self.root_layout.addWidget(self.language_select)
    self.root_layout.addWidget(self.model_select)

  def get_model (self) -> str:
    index = self.model_select.currentIndex()
    return self.models[index].key

  def get_language (self) -> str:
    index = self.language_select.currentIndex()
    option = languages[index]
    return option.value
