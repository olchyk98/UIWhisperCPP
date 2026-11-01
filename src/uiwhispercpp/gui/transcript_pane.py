"""Right pane: a welcome screen when empty, else the selected file's transcript."""
from collections.abc import Callable
import html
import os
import subprocess
import sys

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import Qt, Signal

from uiwhispercpp.gui.file_queue import (
  DONE,
  FAILED,
  INDETERMINATE,
  QUEUED,
  RUNNING,
  STOPPED,
  Job,
)
from uiwhispercpp.gui.theme import secondary_text
from uiwhispercpp.gui.widgets import ElidedLabel
from uiwhispercpp.transcript import (
  FILE_SRT,
  Line,
  OutputOptions,
  render_line,
  render_transcript,
)

# Keep lines a comfortable reading length on wide windows.
_MAX_TEXT_WIDTH = 760


def reveal_in_finder(path: str) -> None:
  if sys.platform == "darwin" and os.path.exists(path):
    subprocess.Popen(["open", "-R", path])
  else:
    QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(os.path.dirname(path)))


def open_file(path: str) -> None:
  QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(path))


class _Welcome(QtWidgets.QWidget):
  """First thing people see: what the app does and the one action to take."""

  choose_files = Signal()
  choose_folder = Signal()

  def __init__(self) -> None:
    super().__init__()
    title = QtWidgets.QLabel("Drop a recording here")
    title.setObjectName("WelcomeTitle")
    title.setAlignment(Qt.AlignmentFlag.AlignCenter)
    text = QtWidgets.QLabel(
      "Audio or video: MP3, M4A, WAV, MP4 and more.\n"
      "The text appears here and is saved next to the original file.\n"
      "Everything stays on this Mac."
    )
    text.setObjectName("WelcomeText")
    text.setAlignment(Qt.AlignmentFlag.AlignCenter)
    button = QtWidgets.QPushButton("Choose Files…")
    button.setObjectName("Primary")
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    button.clicked.connect(self.choose_files)
    folder = QtWidgets.QPushButton("or choose a folder")
    folder.setObjectName("Link")
    folder.setCursor(Qt.CursorShape.PointingHandCursor)
    folder.clicked.connect(self.choose_folder)
    self.first_run = QtWidgets.QLabel(
      "The first time, the speech model downloads once (a few minutes).\n"
      "After that, everything works offline."
    )
    self.first_run.setObjectName("Hint")
    self.first_run.setAlignment(Qt.AlignmentFlag.AlignCenter)

    layout = QtWidgets.QVBoxLayout(self)
    layout.addStretch(3)
    layout.addWidget(title)
    layout.addSpacing(10)
    layout.addWidget(text)
    layout.addSpacing(22)
    layout.addWidget(button, 0, Qt.AlignmentFlag.AlignCenter)
    layout.addWidget(folder, 0, Qt.AlignmentFlag.AlignCenter)
    layout.addStretch(2)
    layout.addWidget(self.first_run)
    layout.addSpacing(18)


class _Callout(QtWidgets.QFrame):
  """A calm boxed message with optional action buttons."""

  def __init__(self) -> None:
    super().__init__()
    self.title = QtWidgets.QLabel()
    self.title.setObjectName("CalloutTitle")
    self.title.setWordWrap(True)
    self.text = QtWidgets.QLabel()
    self.text.setObjectName("CalloutText")
    self.text.setWordWrap(True)
    self.text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    self.buttons = QtWidgets.QHBoxLayout()
    self.buttons.setSpacing(8)
    layout = QtWidgets.QVBoxLayout(self)
    layout.setContentsMargins(14, 10, 14, 12)
    layout.setSpacing(4)
    layout.addWidget(self.title)
    layout.addWidget(self.text)
    layout.addSpacing(4)
    layout.addLayout(self.buttons)

  def show_message(
    self,
    kind: str,
    title: str,
    text: str,
    actions: list[tuple[str, Callable[[], None]]],
  ) -> None:
    key = (kind, title, text, tuple(label for label, _ in actions))
    if key == getattr(self, "_key", None):
      self._actions = actions  # same look; just point buttons at fresh slots
      self.show()
      return
    self._key = key
    self._actions = actions
    self.setObjectName("Warning" if kind == "warning" else "Callout")
    self.style().unpolish(self)
    self.style().polish(self)
    self.title.setText(title)
    self.title.setVisible(bool(title))
    self.text.setText(text)
    self.text.setVisible(bool(text))
    while self.buttons.count():
      widget = self.buttons.takeAt(0).widget()
      if widget is not None:
        # Hide and detach right away; deleteLater alone leaves it painted.
        widget.hide()
        widget.setParent(None)
        widget.deleteLater()
    for index, (label, _slot) in enumerate(actions):
      button = QtWidgets.QPushButton(label)
      button.setObjectName("Bordered")
      button.clicked.connect(lambda _checked=False, i=index: self._actions[i][1]())
      self.buttons.addWidget(button)
    self.buttons.addStretch()
    self.show()


class TranscriptPane(QtWidgets.QWidget):
  """Shows one job; the window wires the action signals to its handlers."""

  stop_requested = Signal(object)
  retry_requested = Signal(object)
  remove_requested = Signal(object)
  save_as_requested = Signal(object)

  def __init__(self) -> None:
    super().__init__()
    self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    self.job: Job | None = None
    self.options = OutputOptions()

    self.welcome = _Welcome()
    self.pick = QtWidgets.QLabel("Click a file on the left to see its text")
    self.pick.setObjectName("FileStatus")
    self.pick.setAlignment(Qt.AlignmentFlag.AlignCenter)

    self.title = ElidedLabel()
    self.title.setObjectName("FileTitle")
    self.stop_button = self._button("Stop", lambda: self.stop_requested.emit(self.job))
    self.stop_button.setToolTip("Stop this file. The text so far is kept.")
    self.copy_button = self._button("Copy Text", self._copy)
    self.reveal_button = self._button("Show in Finder", self._reveal)
    self.status = QtWidgets.QLabel()
    self.status.setObjectName("FileStatus")
    self.status.setTextFormat(Qt.TextFormat.RichText)
    self.status.linkActivated.connect(self._on_link)
    self.status.setWordWrap(True)
    self.status.setSizePolicy(
      QtWidgets.QSizePolicy.Policy.Ignored, QtWidgets.QSizePolicy.Policy.Minimum
    )
    self.summary = ElidedLabel(mode=Qt.TextElideMode.ElideRight)
    self.summary.setObjectName("Hint")
    self.callout = _Callout()
    self.callout.hide()

    title_row = QtWidgets.QHBoxLayout()
    title_row.addWidget(self.title, 1)
    for button in (self.stop_button, self.copy_button, self.reveal_button):
      title_row.addWidget(button)

    self.text = QtWidgets.QPlainTextEdit()
    self.text.setObjectName("Transcript")
    self.text.setReadOnly(True)
    self.text.setUndoRedoEnabled(False)
    self.text.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)

    # Header and callout share the transcript's centered column on wide windows.
    self.header = QtWidgets.QWidget()
    header_layout = QtWidgets.QVBoxLayout(self.header)
    header_layout.setContentsMargins(0, 0, 0, 0)
    header_layout.setSpacing(4)
    header_layout.addLayout(title_row)
    header_layout.addWidget(self.status)
    header_layout.addWidget(self.summary)
    header_layout.addSpacing(6)
    header_layout.addWidget(self.callout)

    content = QtWidgets.QWidget()
    layout = QtWidgets.QVBoxLayout(content)
    layout.setContentsMargins(20, 16, 16, 8)
    layout.setSpacing(4)
    layout.addWidget(self.header)
    layout.addWidget(self.text, 1)

    # Keep following the newest line when the view is resized or text arrives.
    self._at_bottom = True
    bar = self.text.verticalScrollBar()
    bar.valueChanged.connect(
      lambda value: setattr(self, "_at_bottom", value >= bar.maximum() - 30)
    )
    bar.rangeChanged.connect(self._keep_bottom)

    self.stack = QtWidgets.QStackedLayout(self)
    self.stack.addWidget(self.welcome)
    self.stack.addWidget(self.pick)
    self.stack.addWidget(content)

  def _button(self, text: str, slot: Callable[[], None]) -> QtWidgets.QPushButton:
    button = QtWidgets.QPushButton(text)
    button.setObjectName("Bordered")
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    button.clicked.connect(slot)
    return button

  # ---------- showing ----------

  def show_empty(self, has_jobs: bool) -> None:
    self.job = None
    self.stack.setCurrentIndex(1 if has_jobs else 0)

  def set_first_run(self, first_run: bool) -> None:
    self.welcome.first_run.setVisible(first_run)

  def set_options(self, options: OutputOptions) -> None:
    self.options = options
    if self.job is not None:
      self.show_job(self.job)

  def _display_options(self) -> OutputOptions:
    # A saved file is shown exactly as it was written.
    if self.job is not None and self.job.saved_options is not None:
      return self.job.saved_options
    return self.options

  def show_job(self, job: Job) -> None:
    bar = self.text.verticalScrollBar()
    stay_at_bottom = job is self.job and bar.value() >= bar.maximum() - 30
    self.job = job
    self.stack.setCurrentIndex(2)
    self.title.setText(job.name)
    self.title.setToolTip(job.path)
    self._render_all()
    self.refresh_status()
    if job.state == RUNNING or stay_at_bottom:
      self._scroll_to_bottom()

  def refresh_status(self) -> None:
    job = self.job
    if job is None:
      return
    self.summary.setText(job.summary)
    self.summary.setVisible(bool(job.summary))
    self.status.setText(self._status_html(job))
    self.status.setToolTip(job.output_path or "")
    self.stop_button.setVisible(job.state == RUNNING)
    self.stop_button.setEnabled(job.status != "Stopping…")
    self.copy_button.setVisible(bool(job.lines))
    self.reveal_button.setVisible(job.state != QUEUED)
    self._refresh_callout(job)
    if job.state == QUEUED:
      self.text.setPlaceholderText("Waiting. This file starts when the one above it finishes.")
    elif job.state == RUNNING and job.summary.startswith("Parakeet"):
      self.text.setPlaceholderText(
        "Parakeet adds the text in batches, about every two minutes of audio, "
        "so the first lines can take a little while…"
      )
    elif job.state == RUNNING:
      self.text.setPlaceholderText("The text appears here as it's transcribed…")
    else:
      self.text.setPlaceholderText("")

  def _status_html(self, job: Job) -> str:
    if job.state == RUNNING:
      status = job.status
      if job.progress != INDETERMINATE and "%" not in status:
        status = f"{status}  {job.progress}%"
      return html.escape(status)
    if job.output_path and job.state in (DONE, STOPPED, FAILED):
      name = self.status.fontMetrics().elidedText(
        os.path.basename(job.output_path), Qt.TextElideMode.ElideMiddle, 320
      )
      prefix = {DONE: "Saved as", STOPPED: "Stopped. The text so far is saved as",
                FAILED: "The text so far is saved as"}[job.state]
      if os.path.dirname(job.output_path) == os.path.dirname(job.path):
        where = "next to the original file"
      else:
        where = f"in \u201c{html.escape(os.path.basename(os.path.dirname(job.output_path)))}\u201d"
      return f"{prefix} <a href='open'>{html.escape(name)}</a> {where}"
    if job.state == STOPPED:
      return "Stopped before any text was produced."
    if job.state == FAILED:
      return "Didn't finish"
    return html.escape(job.status)

  def _refresh_callout(self, job: Job) -> None:
    def emit(signal: Signal) -> Callable[[], None]:
      return lambda: signal.emit(job)

    if job.state == FAILED and job.error:
      title, _, details = job.error.partition("\n")
      actions = [("Try Again", emit(self.retry_requested)),
                 ("Remove", emit(self.remove_requested))]
      if job.lines and not job.output_path:
        actions.insert(0, ("Save As…", emit(self.save_as_requested)))
      self.callout.show_message("error", title, details, actions)
    elif job.state == STOPPED:
      self.callout.show_message(
        "warning", "", "",
        [("Transcribe Again", emit(self.retry_requested)),
         ("Remove", emit(self.remove_requested))],
      )
    elif job.state == DONE and job.warning:
      self.callout.show_message("warning", job.warning, "", [])
    else:
      self.callout.hide()
      self.callout._key = None

  # ---------- transcript text ----------

  def _render_all(self) -> None:
    self.text.clear()
    options = self._display_options()
    if options.file_type == FILE_SRT:
      self._append_text(render_transcript(self.job.lines, options), dim_numbers=True)
    else:
      for line in self.job.lines:
        self._append_line(line, options)

  def append_line(self, line: Line) -> None:
    """Append one live line without re-rendering the whole transcript."""
    bar = self.text.verticalScrollBar()
    at_bottom = bar.value() >= bar.maximum() - 30
    options = self._display_options()
    if options.file_type == FILE_SRT:
      self._render_all()
    else:
      self._append_line(line, options)
    if at_bottom:
      self._scroll_to_bottom()

  def _formats(self) -> tuple[QtGui.QTextCharFormat, QtGui.QTextCharFormat]:
    plain = QtGui.QTextCharFormat()
    dim = QtGui.QTextCharFormat()
    dim.setForeground(secondary_text(self.palette()))
    return plain, dim

  def _append_line(self, line: Line, options: OutputOptions) -> None:
    """Timestamps and speaker tags in a muted color, the words in full color."""
    rendered = render_line(line, options)
    prefix, separator, words = rendered.partition("]: ")
    plain, dim = self._formats()
    cursor = self.text.textCursor()
    cursor.movePosition(QtGui.QTextCursor.MoveOperation.End)
    if self.text.document().characterCount() > 1:
      cursor.insertBlock()
    if separator:
      cursor.insertText(prefix + "]", dim)
      cursor.insertText(" " + words, plain)
    else:
      cursor.insertText(rendered, plain)

  def _append_text(self, text: str, dim_numbers: bool) -> None:
    plain, dim = self._formats()
    cursor = self.text.textCursor()
    for index, row in enumerate(text.rstrip("\n").split("\n")):
      if index:
        cursor.insertBlock()
      is_meta = dim_numbers and (row.isdigit() or " --> " in row)
      cursor.insertText(row, dim if is_meta else plain)

  def _scroll_to_bottom(self) -> None:
    bar = self.text.verticalScrollBar()
    bar.setValue(bar.maximum())

  def _keep_bottom(self, _minimum: int, maximum: int) -> None:
    if self._at_bottom and self.job is not None and self.job.state == RUNNING:
      self.text.verticalScrollBar().setValue(maximum)

  def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
    super().resizeEvent(event)
    # Center a readable column on wide windows; 12px keeps text off the scrollbar.
    spare = max(0, self.text.width() - _MAX_TEXT_WIDTH)
    self.text.setViewportMargins(spare // 2, 0, spare // 2 + 12, 0)
    self.header.setContentsMargins(spare // 2, 0, spare // 2, 0)

  # ---------- actions ----------

  def _copy(self) -> None:
    QtWidgets.QApplication.clipboard().setText(self.text.toPlainText())
    self.copy_button.setText("Copied ✓")
    QtCore.QTimer.singleShot(1200, lambda: self.copy_button.setText("Copy Text"))

  def _reveal(self) -> None:
    if self.job is not None:
      reveal_in_finder(self.job.output_path or self.job.path)

  def _on_link(self, _link: str) -> None:
    if self.job is not None and self.job.output_path:
      open_file(self.job.output_path)
