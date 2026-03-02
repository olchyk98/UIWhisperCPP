from dataclasses import dataclass
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QWidget

@dataclass
class Option:
  label: str
  value: str

languages = [
  Option("English", "en"),
  Option("Ukrainian", "uk"),
  Option("Swedish", "sv"),
  Option("Russian", "ru"),
]

models = [
  Option("XLarge - Even more accurate, slowest (3GB)", "large-v3"),
  Option("Large - More accurate, slower (1.5GB)", "medium"),
  Option("Base - Balance between accuracy and speed (500MB)", "small"),
  Option("Small - Terrible, but fast (140MB)", "base"),
]


class SettingsSelectorsWidget(QWidget):
  root_layout: QHBoxLayout
  language_select: QComboBox
  model_select: QComboBox

  def __init__(self):
    super().__init__()

    self.language_select = QComboBox()
    self.language_select.addItems([ f.label for f in languages ])

    self.model_select = QComboBox()
    self.model_select.addItems([ f.label for f in models ])

    self.root_layout = QHBoxLayout(self)
    self.root_layout.addWidget(self.language_select)
    self.root_layout.addWidget(self.model_select)

  def get_model (self) -> str:
    index = self.model_select.currentIndex()
    option = models[index]
    return option.value

  def get_language (self) -> str:
    index = self.language_select.currentIndex()
    option = languages[index]
    return option.value
