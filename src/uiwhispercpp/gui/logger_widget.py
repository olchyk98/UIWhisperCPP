from PySide6 import QtWidgets
from PySide6.QtCore import Signal, Slot


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

  @Slot(str)
  def _log(self, message: str) -> None:
    scrollbar_y = self.verticalScrollBar()
    at_the_bottom = scrollbar_y.value() >= scrollbar_y.maximum() - 30
    old_value = scrollbar_y.value()
    self.lines.append(message)
    self.setPlainText('\n\n'.join(self.lines))
    if at_the_bottom:
      scrollbar_y.setValue(scrollbar_y.maximum())
    else:
      scrollbar_y.setValue(old_value)
