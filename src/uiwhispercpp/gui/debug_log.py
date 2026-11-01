"""Debug Log window: the raw, unabstracted output of the app, live.

Meant for troubleshooting, so it is reachable but out of the way: a small
muted "Debug log" link in Settings and Help > Debug Log (Option-Cmd-L).
"""
import os
import subprocess

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import (
  QApplication,
  QCheckBox,
  QHBoxLayout,
  QLabel,
  QPlainTextEdit,
  QPushButton,
  QVBoxLayout,
  QWidget,
)

from uiwhispercpp import app_log


class _Bridge(QObject):
  line = Signal(str)


class DebugLogWindow(QWidget):
  def __init__(self) -> None:
    super().__init__(None, Qt.WindowType.Window)
    self.setWindowTitle("Debug Log")
    self.resize(860, 520)

    self.text = QPlainTextEdit()
    self.text.setReadOnly(True)
    self.text.setUndoRedoEnabled(False)
    self.text.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
    self.text.setMaximumBlockCount(5000)
    self.text.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))

    self.follow = QCheckBox("Follow new lines")
    self.follow.setChecked(True)
    copy_button = QPushButton("Copy all")
    copy_button.clicked.connect(self._copy)
    reveal_button = QPushButton("Show log file")
    reveal_button.clicked.connect(self._reveal)
    clear_button = QPushButton("Clear view")
    clear_button.clicked.connect(self.text.clear)
    path = QLabel(app_log.LOG_FILE)
    path.setObjectName("FileStatus")
    path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

    bar = QHBoxLayout()
    bar.addWidget(self.follow)
    bar.addStretch()
    bar.addWidget(clear_button)
    bar.addWidget(copy_button)
    bar.addWidget(reveal_button)

    layout = QVBoxLayout(self)
    layout.addWidget(self.text, 1)
    layout.addLayout(bar)
    layout.addWidget(path)

    self.text.setPlainText("\n".join(app_log.recent_lines()))
    self._scroll_to_end()
    self._bridge = _Bridge()
    self._bridge.line.connect(self._append)
    app_log.add_listener(self._bridge.line.emit)

  def _append(self, line: str) -> None:
    self.text.appendPlainText(line)
    if self.follow.isChecked():
      self._scroll_to_end()

  def _scroll_to_end(self) -> None:
    bar = self.text.verticalScrollBar()
    bar.setValue(bar.maximum())

  def _copy(self) -> None:
    QApplication.clipboard().setText(self.text.toPlainText())

  def _reveal(self) -> None:
    if os.path.exists(app_log.LOG_FILE):
      subprocess.Popen(["open", "-R", app_log.LOG_FILE])

  def closeEvent(self, event) -> None:
    app_log.remove_listener(self._bridge.line.emit)
    super().closeEvent(event)


_window: DebugLogWindow | None = None


def show_debug_log() -> None:
  global _window
  if _window is None or not _window.isVisible():
    _window = DebugLogWindow()
  _window.show()
  _window.raise_()
  _window.activateWindow()
