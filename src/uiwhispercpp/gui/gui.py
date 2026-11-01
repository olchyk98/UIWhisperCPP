# XXX: Transcription runs on worker threads. Qt widgets may only be touched from
# the main thread, so workers talk to the UI exclusively through the Qt signals
# on `_JobSignals`; Qt queues those onto the main thread automatically.

import os
import queue
import subprocess
import sys
import threading

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import QObject, Qt, Signal

from uiwhispercpp.audio_formats import expand_paths, file_dialog_filter
from uiwhispercpp.gui.file_queue import (
  DONE,
  FAILED,
  INDETERMINATE,
  QUEUED,
  RUNNING,
  FileQueue,
  Job,
)
from uiwhispercpp.gui.settings_dialog import SettingsDialog
from uiwhispercpp.gui.theme import DANGER, apply_theme
from uiwhispercpp.gui.widgets import SHORT_TEXT_ROLE, CompactComboBox, ElidedLabel
from uiwhispercpp.models import Diarizer, ModelManager, Segment, label_segments
from uiwhispercpp.models.diarization import AUTO_SPEAKERS
from uiwhispercpp.settings import Settings
from uiwhispercpp.transcript import (
  FILE_SRT,
  Line,
  OutputOptions,
  lines_from_segments,
  lines_from_turns,
  render_line,
  render_transcript,
  save_transcript,
)

LANGUAGES = [
  ("Auto-detect language", ""),
  ("English", "en"),
  ("Ukrainian", "uk"),
  ("Swedish", "sv"),
  ("Russian", "ru"),
]
# 0 in the spin box means "I don't know how many speakers" -> auto-detect.
_AUTO_SPEAKERS_SPIN = 0
_MAX_SPEAKERS = 20
# Most recordings people separate are two-person conversations, and pinning
# the count is far more reliable than auto-detect.
_DEFAULT_SPEAKERS_SPIN = 2


class _JobSignals(QObject):
  started = Signal(int)
  status = Signal(int, str)
  progress = Signal(int, int)          # job id, percent or INDETERMINATE
  line = Signal(int, object)           # job id, Line
  lines_replaced = Signal(int, object)  # job id, list[Line]
  finished = Signal(int)
  failed = Signal(int, str)


class _BottomBar(QtWidgets.QWidget):
  """Model, language and speaker controls, plus the Settings button."""

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
        label = f"{label} - not supported on this Mac"
      self.model.addItem(label, option.key)
      self.model.setItemData(index, option.short_label or option.label, SHORT_TEXT_ROLE)
      if option.unavailable_reason:
        item = model_items.item(index)
        item.setEnabled(False)
        item.setToolTip(option.unavailable_reason)
    saved = str(settings.value("ui/model", ""))
    usable = {o.key for o in self.models if not o.unavailable_reason}
    chosen = saved if saved in usable else manager.default_model_key()
    self.model.setCurrentIndex(max(0, self.model.findData(chosen)))
    self.model.setToolTip("Transcription model")

    self.language = QtWidgets.QComboBox()
    for label, code in LANGUAGES:
      self.language.addItem(label, code)
    self.language.setCurrentIndex(
      max(0, self.language.findData(str(settings.value("ui/language", ""))))
    )
    self.language.setToolTip("Spoken language (Parakeet detects it automatically)")

    self.diarize = QtWidgets.QCheckBox("Separate speakers")
    self.diarize.setChecked(str(settings.value("ui/diarize", "false")).lower() == "true")
    self.speakers = QtWidgets.QSpinBox()
    self.speakers.setRange(_AUTO_SPEAKERS_SPIN, _MAX_SPEAKERS)
    self.speakers.setSpecialValueText("Auto")
    self.speakers.setValue(int(settings.value("ui/speakers", _DEFAULT_SPEAKERS_SPIN)))
    self.speakers.setToolTip(
      "How many people are in the recording. The exact count gives the cleanest "
      "separation; 'Auto' guesses but is less reliable. Voices that sound "
      "identical are not split apart."
    )
    self.speakers.setEnabled(self.diarize.isChecked())
    self.diarize.toggled.connect(self.speakers.setEnabled)

    self.settings_button = QtWidgets.QToolButton()
    self.settings_button.setObjectName("Flat")
    self.settings_button.setText("Settings")
    self.settings_button.setToolTip("Output format and updates (⌘,)")

    layout = QtWidgets.QHBoxLayout(self)
    layout.setContentsMargins(12, 8, 12, 8)
    layout.setSpacing(10)
    layout.addWidget(self.model)
    layout.addWidget(self.language)
    layout.addSpacing(6)
    layout.addWidget(self.diarize)
    layout.addWidget(self.speakers)
    layout.addStretch()
    layout.addWidget(self.settings_button)

    self.model.currentIndexChanged.connect(self._persist)
    self.language.currentIndexChanged.connect(self._persist)
    self.diarize.toggled.connect(self._persist)
    self.speakers.valueChanged.connect(self._persist)

  def _persist(self) -> None:
    self._settings.set_value("ui/model", self.model_key())
    self._settings.set_value("ui/language", self.language_code())
    self._settings.set_value("ui/diarize", "true" if self.diarize.isChecked() else "false")
    self._settings.set_value("ui/speakers", self.speakers.value())

  def model_key(self) -> str:
    return self.model.currentData()

  def language_code(self) -> str:
    return self.language.currentData()

  def num_speakers(self) -> int | None:
    """None when not separating speakers; AUTO_SPEAKERS for auto-detect."""
    if not self.diarize.isChecked():
      return None
    value = self.speakers.value()
    return AUTO_SPEAKERS if value == _AUTO_SPEAKERS_SPIN else value


class _TranscriptPane(QtWidgets.QWidget):
  """Right pane: the selected file's transcript, live while it is produced."""

  def __init__(self) -> None:
    super().__init__()
    self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    self.job: Job | None = None
    self.options = OutputOptions()

    self.title = ElidedLabel()
    self.title.setObjectName("FileTitle")
    self.status = ElidedLabel(mode=Qt.TextElideMode.ElideRight)
    self.status.setObjectName("FileStatus")
    self.error = QtWidgets.QLabel()
    self.error.setStyleSheet(f"color: {DANGER};")
    self.error.setWordWrap(True)
    self.error.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

    self.copy_button = self._flat_button("Copy", self._copy)
    self.open_button = self._flat_button("Open", self._open)
    self.reveal_button = self._flat_button("Show in Finder", self._reveal)

    title_row = QtWidgets.QHBoxLayout()
    title_row.addWidget(self.title, 1)
    for button in (self.copy_button, self.open_button, self.reveal_button):
      title_row.addWidget(button)
    # The status gets the full pane width on its own line, so it stays
    # readable even at the smallest window size.
    header = QtWidgets.QVBoxLayout()
    header.setSpacing(2)
    header.addLayout(title_row)
    header.addWidget(self.status)

    self.text = QtWidgets.QPlainTextEdit()
    self.text.setObjectName("Transcript")
    self.text.setReadOnly(True)
    self.text.setUndoRedoEnabled(False)
    self.text.document().setDocumentMargin(2)
    self.text.setLineWrapMode(QtWidgets.QPlainTextEdit.LineWrapMode.WidgetWidth)

    self.empty = QtWidgets.QLabel("Select a file to see its transcript")
    self.empty.setObjectName("FileStatus")
    self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)

    content = QtWidgets.QWidget()
    content_layout = QtWidgets.QVBoxLayout(content)
    content_layout.setContentsMargins(20, 16, 16, 8)
    content_layout.setSpacing(10)
    content_layout.addLayout(header)
    content_layout.addWidget(self.error)
    content_layout.addWidget(self.text, 1)

    self.stack = QtWidgets.QStackedLayout(self)
    self.stack.addWidget(self.empty)
    self.stack.addWidget(content)
    self.show_job(None)

  def _flat_button(self, text: str, slot) -> QtWidgets.QPushButton:
    button = QtWidgets.QPushButton(text)
    button.setObjectName("Flat")
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    button.clicked.connect(slot)
    return button

  def _display_options(self) -> OutputOptions:
    # A saved file is shown exactly as it was written.
    if self.job is not None and self.job.saved_options is not None:
      return self.job.saved_options
    return self.options

  def set_options(self, options: OutputOptions) -> None:
    self.options = options
    self.show_job(self.job)

  def show_job(self, job: Job | None) -> None:
    # Re-showing the same file (e.g. speaker labels replacing the live lines)
    # keeps the reader at the bottom if that's where they were.
    bar = self.text.verticalScrollBar()
    stay_at_bottom = job is not None and job is self.job and bar.value() >= bar.maximum() - 30
    self.job = job
    if job is None:
      self.stack.setCurrentIndex(0)
      return
    self.stack.setCurrentIndex(1)
    self.title.setText(job.name)
    self.title.setToolTip(job.path)
    self.text.setPlainText(render_transcript(job.lines, self._display_options()))
    self.refresh_status()
    if job.state == RUNNING or stay_at_bottom:
      self._scroll_to_bottom()

  def refresh_status(self) -> None:
    job = self.job
    if job is None:
      return
    status = job.status
    if job.state == RUNNING and job.progress != INDETERMINATE:
      status = f"{status}  {job.progress}%"
    if job.state == DONE and job.output_path:
      status = f"Saved as {os.path.basename(job.output_path)}"
    self.status.setText(status)
    if job.output_path:
      self.status.setToolTip(job.output_path)
    self.error.setVisible(bool(job.error))
    self.error.setText(job.error or "")
    has_text = bool(job.lines)
    self.copy_button.setEnabled(has_text)
    has_file = bool(job.output_path) and os.path.exists(job.output_path or "")
    self.open_button.setEnabled(has_file)
    self.reveal_button.setEnabled(True)
    if job.state == QUEUED and not job.lines:
      self.text.setPlaceholderText("Waiting for its turn...")
    elif job.state == RUNNING and not job.lines:
      self.text.setPlaceholderText("The transcript appears here as it's produced...")
    else:
      self.text.setPlaceholderText("")

  def append_line(self, line: Line) -> None:
    """Append one live line without re-rendering the whole transcript."""
    options = self._display_options()
    bar = self.text.verticalScrollBar()
    at_bottom = bar.value() >= bar.maximum() - 30
    if options.file_type == FILE_SRT:
      # Cheap enough to re-render: SRT numbering depends on position.
      self.text.setPlainText(render_transcript(self.job.lines, options))
    else:
      self.text.appendPlainText(render_line(line, options))
    if at_bottom:
      self._scroll_to_bottom()

  def _scroll_to_bottom(self) -> None:
    bar = self.text.verticalScrollBar()
    bar.setValue(bar.maximum())

  def _copy(self) -> None:
    QtWidgets.QApplication.clipboard().setText(self.text.toPlainText())
    self.copy_button.setText("Copied")
    QtCore.QTimer.singleShot(1200, lambda: self.copy_button.setText("Copy"))

  def _open(self) -> None:
    if self.job and self.job.output_path:
      QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(self.job.output_path))

  def _reveal(self) -> None:
    if self.job is None:
      return
    target = self.job.output_path if self.job.output_path else self.job.path
    reveal_in_finder(target)


def reveal_in_finder(path: str) -> None:
  if sys.platform == "darwin" and os.path.exists(path):
    subprocess.Popen(["open", "-R", path])
  else:
    QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(os.path.dirname(path)))


class MainWindow(QtWidgets.QMainWindow):
  def __init__(self) -> None:
    super().__init__()
    self.setWindowTitle("UIWhisperCPP")
    self.setAcceptDrops(True)
    self.resize(940, 580)
    self.setMinimumSize(680, 420)

    self.settings = Settings()
    self.model_manager = ModelManager()
    self.diarizer = Diarizer()
    # One long-lived worker thread runs every job. MLX (Parakeet) keeps its GPU
    # stream per thread, so a fresh thread per file breaks after a failure.
    self._work_queue: queue.Queue = queue.Queue()
    threading.Thread(target=self._worker_loop, daemon=True).start()
    self.jobs: dict[int, Job] = {}
    self._next_id = 1
    self._running: Job | None = None
    # Follow the running file in the right pane until the user picks another.
    self._follow_running = True
    self._settings_dialog: SettingsDialog | None = None

    self.signals = _JobSignals()
    self.signals.started.connect(self._on_started)
    self.signals.status.connect(self._on_status)
    self.signals.progress.connect(self._on_progress)
    self.signals.line.connect(self._on_line)
    self.signals.lines_replaced.connect(self._on_lines_replaced)
    self.signals.finished.connect(self._on_finished)
    self.signals.failed.connect(self._on_failed)

    self._build_ui()
    self._build_menu()

  # ---------- layout ----------

  def _build_ui(self) -> None:
    self.queue = FileQueue()
    self.queue.itemSelectionChanged.connect(self._on_selection_changed)
    self.queue.customContextMenuRequested.connect(self._show_queue_menu)

    files_title = QtWidgets.QLabel("FILES")
    files_title.setObjectName("PaneTitle")
    add_files = QtWidgets.QPushButton("Add files...")
    add_files.setObjectName("Add")
    add_files.clicked.connect(self.add_files_dialog)
    add_folder = QtWidgets.QPushButton("Add folder...")
    add_folder.setObjectName("Add")
    add_folder.clicked.connect(self.add_folder_dialog)
    add_row = QtWidgets.QHBoxLayout()
    add_row.setSpacing(6)
    add_row.addWidget(add_files)
    add_row.addWidget(add_folder)

    sidebar = QtWidgets.QWidget()
    sidebar.setObjectName("Sidebar")
    sidebar_layout = QtWidgets.QVBoxLayout(sidebar)
    sidebar_layout.setContentsMargins(8, 14, 8, 10)
    sidebar_layout.setSpacing(8)
    title_row = QtWidgets.QHBoxLayout()
    title_row.setContentsMargins(10, 0, 4, 0)
    title_row.addWidget(files_title)
    title_row.addStretch()
    self.clear_button = QtWidgets.QPushButton("Clear finished")
    self.clear_button.setObjectName("Flat")
    self.clear_button.clicked.connect(self.clear_finished)
    self.clear_button.hide()
    title_row.addWidget(self.clear_button)
    sidebar_layout.addLayout(title_row)
    sidebar_layout.addWidget(self.queue, 1)
    # Short-lived notes ("No supported audio files...") appear right where the
    # user just added files; hidden otherwise so nothing else moves around.
    self.note = QtWidgets.QLabel()
    self.note.setObjectName("FileStatus")
    self.note.setWordWrap(True)
    self.note.setAlignment(Qt.AlignmentFlag.AlignCenter)
    self.note.hide()
    sidebar_layout.addWidget(self.note)
    sidebar_layout.addLayout(add_row)

    self.pane = _TranscriptPane()
    self.pane.setObjectName("Pane")
    self.pane.set_options(self.settings.output_options())

    splitter = QtWidgets.QSplitter()
    splitter.setHandleWidth(1)
    splitter.setChildrenCollapsible(False)
    splitter.addWidget(sidebar)
    splitter.addWidget(self.pane)
    splitter.setStretchFactor(1, 1)
    splitter.setSizes([290, 650])
    sidebar.setMinimumWidth(220)

    self.bottom = _BottomBar(self.model_manager, self.settings)
    self.bottom.settings_button.clicked.connect(lambda: self.open_settings(0))

    central = QtWidgets.QWidget()
    layout = QtWidgets.QVBoxLayout(central)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)
    layout.addWidget(splitter, 1)
    layout.addWidget(self.bottom)
    self.setCentralWidget(central)

  def _build_menu(self) -> None:
    menu_bar = self.menuBar()
    file_menu = menu_bar.addMenu("File")
    add_files = file_menu.addAction("Add Files...", self.add_files_dialog)
    add_files.setShortcut(QtGui.QKeySequence.StandardKey.Open)
    add_folder = file_menu.addAction("Add Folder...", self.add_folder_dialog)
    add_folder.setShortcut(QtGui.QKeySequence("Ctrl+Shift+O"))
    file_menu.addSeparator()
    file_menu.addAction("Clear Finished", self.clear_finished)
    # macOS moves these into the application menu (UIWhisperCPP > Settings...).
    settings_action = file_menu.addAction("Settings...", lambda: self.open_settings(0))
    settings_action.setShortcut(QtGui.QKeySequence.StandardKey.Preferences)
    settings_action.setMenuRole(QtGui.QAction.MenuRole.PreferencesRole)
    updates_action = file_menu.addAction("Check for Updates...", lambda: self.open_settings(1))
    updates_action.setMenuRole(QtGui.QAction.MenuRole.ApplicationSpecificRole)

  # ---------- adding files ----------

  def add_files_dialog(self) -> None:
    paths, _ = QtWidgets.QFileDialog.getOpenFileNames(
      self, "Add audio files", self._last_dir(), file_dialog_filter()
    )
    self.add_paths(paths)

  def add_folder_dialog(self) -> None:
    folder = QtWidgets.QFileDialog.getExistingDirectory(self, "Add folder", self._last_dir())
    if folder:
      self.add_paths([folder])

  def _last_dir(self) -> str:
    return str(self.settings.value("ui/last_dir", os.path.expanduser("~")))

  def add_paths(self, paths: list[str]) -> int:
    """Queue every audio file among `paths` (folders expand). Returns how many."""
    if not paths:
      return 0
    self.settings.set_value("ui/last_dir", os.path.dirname(paths[0].rstrip("/")))
    files = expand_paths(paths)
    if not files:
      self.flash("No supported audio files found there.")
      return 0
    already = {job.path for job in self.jobs.values() if job.state in (QUEUED, RUNNING)}
    added = 0
    for path in files:
      if path in already:
        continue
      job = Job(id=self._next_id, path=path)
      self._next_id += 1
      self.jobs[job.id] = job
      self.queue.add_job(job)
      added += 1
    if added and self.queue.selected_job() is None:
      self._follow_running = True
    self._start_next()
    return added

  def flash(self, text: str) -> None:
    self.note.setText(text)
    self.note.show()
    QtCore.QTimer.singleShot(4000, self.note.hide)

  def dragEnterEvent(self, event: QtGui.QDragEnterEvent) -> None:
    if event.mimeData().hasUrls():
      event.acceptProposedAction()

  def dropEvent(self, event: QtGui.QDropEvent) -> None:
    paths = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
    self.add_paths(paths)
    event.acceptProposedAction()

  # ---------- queue actions ----------

  def clear_finished(self) -> None:
    for job in [j for j in self.jobs.values() if j.state in (DONE, FAILED)]:
      self._remove(job)

  def _remove(self, job: Job) -> None:
    if job.state == RUNNING:
      return
    self.jobs.pop(job.id, None)
    self.queue.remove_job(job.id)
    if self.pane.job is job:
      self.pane.show_job(self.queue.selected_job())
    self._update_clear_button()

  def _show_queue_menu(self, position: QtCore.QPoint) -> None:
    item = self.queue.itemAt(position)
    if item is None:
      return
    job = self.queue.job_at(self.queue.row(item))
    menu = QtWidgets.QMenu(self)
    if job.output_path:
      menu.addAction("Open Transcript", lambda: QtGui.QDesktopServices.openUrl(
        QtCore.QUrl.fromLocalFile(job.output_path)))
    menu.addAction("Show in Finder", lambda: reveal_in_finder(job.output_path or job.path))
    if job.state == FAILED:
      menu.addAction("Try Again", lambda: self._retry(job))
    if job.state != RUNNING:
      menu.addSeparator()
      menu.addAction("Remove from List", lambda: self._remove(job))
    menu.exec(self.queue.viewport().mapToGlobal(position))

  def _retry(self, job: Job) -> None:
    job.state, job.status, job.error, job.lines = QUEUED, "Waiting", None, []
    job.progress = INDETERMINATE
    self._refresh(job)
    self._start_next()

  def _update_clear_button(self) -> None:
    self.clear_button.setVisible(any(j.state in (DONE, FAILED) for j in self.jobs.values()))

  def _on_selection_changed(self) -> None:
    job = self.queue.selected_job()
    self._follow_running = job is None or job is self._running
    self.pane.show_job(job)

  # ---------- running jobs ----------

  def is_busy(self) -> bool:
    return self._running is not None

  def _start_next(self) -> None:
    if self._running is not None:
      return
    job = next((j for j in self.jobs.values() if j.state == QUEUED), None)
    if job is None:
      return
    model_key = self.bottom.model_key()
    if not model_key:
      return
    self._running = job
    job.state, job.status, job.progress = RUNNING, "Starting...", INDETERMINATE
    job.lines, job.error, job.output_path, job.saved_options = [], None, None, None
    self._refresh(job)
    if self._follow_running:
      self.queue.select_job(job.id)
    snapshot = dict(
      model_key=model_key,
      language=self.bottom.language_code(),
      num_speakers=self.bottom.num_speakers(),
      options=self.settings.output_options(),
    )
    self._work_queue.put((job.id, job.path, snapshot))

  def _worker_loop(self) -> None:
    while True:
      self._work(*self._work_queue.get())

  def _work(self, job_id: int, audio_path: str, snapshot: dict) -> None:
    """Worker thread: transcribe one file. Talks to the UI only via signals."""
    signals = self.signals
    options: OutputOptions = snapshot["options"]
    live: list[Segment] = []
    try:
      signals.started.emit(job_id)

      def on_segment(segment: Segment) -> None:
        live.append(segment)
        signals.line.emit(job_id, lines_from_segments([segment])[0])
        # Save as we go: if the run dies midway, the partial transcript survives.
        save_transcript(audio_path, lines_from_segments(live), options)

      def on_progress(percent: int) -> None:
        signals.progress.emit(job_id, int(percent))

      segments = self.model_manager.transcribe(
        audio_path,
        model_key=snapshot["model_key"],
        language=snapshot["language"],
        on_segment=on_segment,
        on_progress=on_progress,
        on_status=lambda text: signals.status.emit(job_id, text),
      )
      if snapshot["num_speakers"] is not None:
        signals.status.emit(job_id, "Separating speakers...")
        signals.progress.emit(job_id, INDETERMINATE)
        speaker_segments = self.diarizer.diarize(
          audio_path,
          num_speakers=snapshot["num_speakers"],
          on_progress=on_progress,
          on_log=lambda text: signals.status.emit(job_id, text),
        )
        signals.lines_replaced.emit(
          job_id, lines_from_turns(label_segments(segments, speaker_segments))
        )
      else:
        signals.lines_replaced.emit(job_id, lines_from_segments(segments))
      signals.finished.emit(job_id)
    except Exception as error:  # noqa: BLE001 - every failure is shown per file
      print(error, file=sys.stderr)
      signals.failed.emit(job_id, str(error) or error.__class__.__name__)

  def _job(self, job_id: int) -> Job | None:
    return self.jobs.get(job_id)

  def _refresh(self, job: Job) -> None:
    self.queue.refresh(job)
    if self.pane.job is job:
      self.pane.refresh_status()

  def _on_started(self, job_id: int) -> None:
    if job := self._job(job_id):
      job.status = "Preparing..."
      self._refresh(job)

  def _on_status(self, job_id: int, text: str) -> None:
    if job := self._job(job_id):
      job.status = text.strip()
      self._refresh(job)

  def _on_progress(self, job_id: int, percent: int) -> None:
    if job := self._job(job_id):
      job.progress = percent if percent == INDETERMINATE else max(0, min(100, percent))
      self._refresh(job)

  def _on_line(self, job_id: int, line: Line) -> None:
    if job := self._job(job_id):
      job.lines.append(line)
      if self.pane.job is job:
        self.pane.append_line(line)
        self.pane.refresh_status()

  def _on_lines_replaced(self, job_id: int, lines: list[Line]) -> None:
    if job := self._job(job_id):
      job.lines = list(lines)

  def _on_finished(self, job_id: int) -> None:
    job = self._job(job_id)
    if job is None:
      return
    options = self.settings.output_options()
    try:
      job.output_path = save_transcript(job.path, job.lines, options)
      job.saved_options = options
      job.state, job.status = DONE, "Done"
    except OSError as error:
      job.state, job.status, job.error = FAILED, "Couldn't save", str(error)
    self._job_ended(job)

  def _on_failed(self, job_id: int, message: str) -> None:
    job = self._job(job_id)
    if job is None:
      return
    job.state, job.status, job.error = FAILED, "Failed", message
    if job.lines:
      job.output_path = save_transcript(job.path, job.lines, self.settings.output_options())
      job.saved_options = self.settings.output_options()
      job.error = f"{message}\nThe partial transcript was saved."
    self._job_ended(job)

  def _job_ended(self, job: Job) -> None:
    self._running = None
    self._refresh(job)
    if self.pane.job is job:
      self.pane.show_job(job)
    self._update_clear_button()
    self._start_next()

  # ---------- settings / lifecycle ----------

  def open_settings(self, tab: int) -> None:
    if self._settings_dialog is None:
      self._settings_dialog = SettingsDialog(
        self, self.settings, self.pane.set_options, self.is_busy
      )
    self._settings_dialog.show_tab(tab)

  def closeEvent(self, event: QtGui.QCloseEvent) -> None:
    if self.is_busy() and not QtWidgets.QApplication.instance().property("updating"):
      answer = QtWidgets.QMessageBox.question(
        self,
        "Quit while transcribing?",
        "A file is still being transcribed. If you quit now, only the part "
        "transcribed so far is saved.",
        QtWidgets.QMessageBox.StandardButton.Cancel | QtWidgets.QMessageBox.StandardButton.Yes,
        QtWidgets.QMessageBox.StandardButton.Cancel,
      )
      if answer != QtWidgets.QMessageBox.StandardButton.Yes:
        event.ignore()
        return
    event.accept()


def run_program() -> None:
  app = QtWidgets.QApplication(sys.argv)
  app.setApplicationName("UIWhisperCPP")
  app.setOrganizationName("Camphouse")
  apply_theme(app)

  window = MainWindow()
  window.show()

  sys.exit(app.exec())
