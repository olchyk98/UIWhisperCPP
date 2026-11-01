# XXX: Transcription runs on one worker thread. Qt widgets may only be touched
# from the main thread, so the worker talks to the UI exclusively through the
# Qt signals on `_JobSignals`; Qt queues those onto the main thread.

import os
import queue
import sys
import threading
import time

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import QObject, Qt, Signal

from uiwhispercpp import app_log
from uiwhispercpp.audio_formats import expand_paths, file_dialog_filter
from uiwhispercpp.gui.bottom_bar import BottomBar
from uiwhispercpp.gui.debug_log import show_debug_log
from uiwhispercpp.gui.file_queue import (
  DONE,
  FAILED,
  FINISHED_STATES,
  INDETERMINATE,
  QUEUED,
  RUNNING,
  STOPPED,
  FileQueue,
  Job,
)
from uiwhispercpp.gui.settings_dialog import SettingsDialog
from uiwhispercpp.gui.theme import ACCENT, apply_theme
from uiwhispercpp.gui.transcript_pane import TranscriptPane, open_file, reveal_in_finder
from uiwhispercpp.models import (
  Diarizer,
  JobStopped,
  ModelManager,
  Segment,
  StopToken,
  UserFacingError,
  label_segments,
)
from uiwhispercpp.settings import Settings
from uiwhispercpp.transcript import (
  Line,
  OutputOptions,
  lines_from_segments,
  lines_from_turns,
  numbered_path,
  transcript_path_for,
  write_transcript,
)

log = app_log.get("jobs")

# Partial transcripts are rewritten at most this often while a file runs.
_PARTIAL_SAVE_SECONDS = 2.0
_GENERIC_ERROR = (
  "Something went wrong with this file.\n"
  "Details are in Help > Debug Log. Try again, or try another model."
)


class _JobSignals(QObject):
  status = Signal(int, str)
  progress = Signal(int, int)          # job id, percent or INDETERMINATE
  line = Signal(int, object)           # job id, Line
  lines_replaced = Signal(int, object)  # job id, list[Line]
  warning = Signal(int, str)
  finished = Signal(int)
  failed = Signal(int, str)
  stopped = Signal(int)


class _DropOverlay(QtWidgets.QWidget):
  """Accent frame shown over the window while files are dragged onto it."""

  def __init__(self, parent: QtWidgets.QWidget) -> None:
    super().__init__(parent)
    self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    self.hide()

  def paintEvent(self, event) -> None:
    painter = QtGui.QPainter(self)
    painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
    accent = QtGui.QColor(ACCENT)
    fill = QtGui.QColor(accent)
    fill.setAlphaF(0.07)
    painter.setBrush(fill)
    pen = QtGui.QPen(accent, 2)
    painter.setPen(pen)
    painter.drawRoundedRect(self.rect().adjusted(6, 6, -6, -6), 12, 12)
    font = painter.font()
    font.setPointSizeF(font.pointSizeF() + 4)
    font.setWeight(QtGui.QFont.Weight.DemiBold)
    painter.setFont(font)
    painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Drop to transcribe")


class _Toast(QtWidgets.QLabel):
  """A short message floating at the bottom of the window, hard to miss."""

  def __init__(self, parent: QtWidgets.QWidget) -> None:
    super().__init__(parent)
    self.setObjectName("Toast")
    self.setWordWrap(True)
    self.setAlignment(Qt.AlignmentFlag.AlignCenter)
    self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    self.hide()
    self._timer = QtCore.QTimer(self)
    self._timer.setSingleShot(True)
    self._timer.timeout.connect(self.hide)

  def show_text(self, text: str, seconds: float = 6) -> None:
    self.setText(text)
    self.reposition()
    self.show()
    self.raise_()
    self._timer.start(int(seconds * 1000))

  def reposition(self) -> None:
    parent = self.parentWidget()
    width = min(520, parent.width() - 40)
    self.setFixedWidth(width)
    self.adjustSize()
    self.move((parent.width() - width) // 2, parent.height() - self.height() - 70)


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
    self.jobs: dict[int, Job] = {}
    self._next_id = 1
    self._running: Job | None = None
    self._follow_running = True
    self._settings_dialog: SettingsDialog | None = None
    self._choosing_target = False
    # "replace" / "keep" once the user ticks "do the same for the rest".
    self._clash_choice: str | None = None
    self._pending_update: tuple[str, str] | None = None

    self.signals = _JobSignals()
    self.signals.status.connect(self._on_status)
    self.signals.progress.connect(self._on_progress)
    self.signals.line.connect(self._on_line)
    self.signals.lines_replaced.connect(self._on_lines_replaced)
    self.signals.warning.connect(self._on_warning)
    self.signals.finished.connect(self._on_finished)
    self.signals.failed.connect(self._on_failed)
    self.signals.stopped.connect(self._on_stopped)

    self._work_queue: queue.Queue = queue.Queue()
    self._worker: threading.Thread | None = None
    self._ensure_worker()

    self._build_ui()
    self._build_menu()
    self._refresh_empty_state()

  # ---------- layout ----------

  def _build_ui(self) -> None:
    self.queue = FileQueue()
    self.queue.itemSelectionChanged.connect(self._on_selection_changed)
    self.queue.customContextMenuRequested.connect(self._show_queue_menu)
    self.queue.itemDoubleClicked.connect(self._on_double_click)

    files_title = QtWidgets.QLabel("Files")
    files_title.setObjectName("PaneTitle")
    self.clear_button = QtWidgets.QPushButton("Clear")
    self.clear_button.setObjectName("Quiet")
    self.clear_button.setToolTip("Remove finished files from the list (not from disk)")
    self.clear_button.clicked.connect(self.clear_finished)
    self.clear_button.hide()
    add_menu = QtWidgets.QMenu(self)
    add_menu.addAction("Add Files…", self.add_files_dialog)
    add_menu.addAction("Add Folder…", self.add_folder_dialog)
    add_button = QtWidgets.QToolButton()
    add_button.setObjectName("Plus")
    add_button.setText("+")
    add_button.setToolTip("Add files or a folder (⌘O)")
    add_button.setPopupMode(QtWidgets.QToolButton.ToolButtonPopupMode.InstantPopup)
    add_button.setMenu(add_menu)

    title_row = QtWidgets.QHBoxLayout()
    title_row.setContentsMargins(10, 0, 2, 0)
    title_row.addWidget(files_title)
    title_row.addStretch()
    title_row.addWidget(self.clear_button)
    title_row.addWidget(add_button)
    title_bar = QtWidgets.QWidget()
    title_bar.setFixedHeight(30)
    title_bar.setLayout(title_row)


    sidebar = QtWidgets.QWidget()
    sidebar.setObjectName("Sidebar")
    sidebar_layout = QtWidgets.QVBoxLayout(sidebar)
    sidebar_layout.setContentsMargins(8, 10, 8, 10)
    sidebar_layout.setSpacing(6)
    sidebar_layout.addWidget(title_bar)
    sidebar_layout.addWidget(self.queue, 1)
    sidebar.setMinimumWidth(200)

    self.pane = TranscriptPane()
    self.pane.setObjectName("Pane")
    self.pane.set_options(self.settings.output_options())
    self.pane.welcome.choose_files.connect(self.add_files_dialog)
    self.pane.welcome.choose_folder.connect(self.add_folder_dialog)
    self.pane.stop_requested.connect(self.stop_job)
    self.pane.retry_requested.connect(self._retry)
    self.pane.remove_requested.connect(self._remove)
    self.pane.save_as_requested.connect(self._save_as)

    splitter = QtWidgets.QSplitter()
    splitter.setHandleWidth(1)
    splitter.setChildrenCollapsible(False)
    splitter.addWidget(sidebar)
    splitter.addWidget(self.pane)
    splitter.setStretchFactor(1, 1)
    splitter.setSizes([250, 690])

    self.bottom = BottomBar(self.model_manager, self.settings)
    self.bottom.settings_button.clicked.connect(lambda: self.open_settings(0))
    self.bottom.changed.connect(self._on_bar_changed)

    central = QtWidgets.QWidget()
    layout = QtWidgets.QVBoxLayout(central)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)
    layout.addWidget(splitter, 1)
    layout.addWidget(self.bottom)
    self.setCentralWidget(central)
    self.overlay = _DropOverlay(central)
    self.toast = _Toast(central)

  def _build_menu(self) -> None:
    menu_bar = self.menuBar()
    file_menu = menu_bar.addMenu("File")
    add_files = file_menu.addAction("Add Files…", self.add_files_dialog)
    add_files.setShortcut(QtGui.QKeySequence.StandardKey.Open)
    add_folder = file_menu.addAction("Add Folder…", self.add_folder_dialog)
    add_folder.setShortcut(QtGui.QKeySequence("Ctrl+Shift+O"))
    file_menu.addSeparator()
    file_menu.addAction("Clear Finished", self.clear_finished)
    # macOS moves these into the application menu (UIWhisperCPP > Settings…).
    settings_action = file_menu.addAction("Settings…", lambda: self.open_settings(0))
    settings_action.setShortcut(QtGui.QKeySequence.StandardKey.Preferences)
    settings_action.setMenuRole(QtGui.QAction.MenuRole.PreferencesRole)
    updates_action = file_menu.addAction("Check for Updates…", lambda: self.open_settings(1))
    updates_action.setMenuRole(QtGui.QAction.MenuRole.ApplicationSpecificRole)
    help_menu = menu_bar.addMenu("Help")
    debug_action = help_menu.addAction("Debug Log", show_debug_log)
    debug_action.setShortcut(QtGui.QKeySequence("Ctrl+Alt+L"))

  def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
    super().resizeEvent(event)
    self.overlay.setGeometry(self.centralWidget().rect())
    if self.toast.isVisible():
      self.toast.reposition()

  # ---------- adding files ----------

  def add_files_dialog(self) -> None:
    paths, _ = QtWidgets.QFileDialog.getOpenFileNames(
      self, "Choose Audio or Video Files", self._last_dir(), file_dialog_filter()
    )
    self.add_paths(paths)

  def add_folder_dialog(self) -> None:
    folder = QtWidgets.QFileDialog.getExistingDirectory(self, "Choose a Folder", self._last_dir())
    if folder:
      self.add_paths([folder])

  def _last_dir(self) -> str:
    last = self.settings.text("ui/last_dir")
    return last if last and os.path.isdir(last) else os.path.expanduser("~")

  def add_paths(self, paths: list[str]) -> int:
    """Queue every audio file among `paths` (folders expand). Returns how many."""
    if not paths:
      return 0
    self.settings.set_value("ui/last_dir", os.path.dirname(paths[0].rstrip("/")))
    files, unreadable = expand_paths(paths)
    for folder in unreadable:
      log.warning("Can't read folder %s", folder)
    if not files:
      if unreadable:
        self.flash(
          f"Couldn't open “{os.path.basename(unreadable[0].rstrip('/'))}”. "
          "Allow access in System Settings > Privacy & Security, then try again."
        )
      else:
        self.flash("Nothing to add: those aren't audio or video files.")
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
    log.info("Added %d file(s)", added)
    if added and self.queue.selected_job() is None:
      self._follow_running = True
    self._refresh_empty_state()
    self._start_next()
    return added

  def flash(self, text: str) -> None:
    self.toast.show_text(text)

  def dragEnterEvent(self, event: QtGui.QDragEnterEvent) -> None:
    if event.mimeData().hasUrls():
      event.acceptProposedAction()
      self.overlay.setGeometry(self.centralWidget().rect())
      self.overlay.show()
      self.overlay.raise_()

  def dragLeaveEvent(self, event: QtGui.QDragLeaveEvent) -> None:
    self.overlay.hide()

  def dropEvent(self, event: QtGui.QDropEvent) -> None:
    self.overlay.hide()
    urls = event.mimeData().urls()
    paths = [url.toLocalFile() for url in urls if url.isLocalFile()]
    if urls and not paths:
      self.flash("Only files on this Mac can be added. Download them first.")
    self.add_paths(paths)
    event.acceptProposedAction()

  # ---------- queue actions ----------

  def clear_finished(self) -> None:
    for job in [j for j in self.jobs.values() if j.state in FINISHED_STATES]:
      self._remove(job)

  def _remove(self, job: Job) -> None:
    if job is None or job.state == RUNNING:
      return
    self.jobs.pop(job.id, None)
    self.queue.remove_job(job.id)
    if self.pane.job is job:
      self.pane.job = None
    self._refresh_empty_state()
    if not self.is_busy() and not any(j.state == QUEUED for j in self.jobs.values()):
      self._queue_idle()

  def stop_job(self, job: Job) -> None:
    if job is None or job.state != RUNNING or job.stop_token is None:
      return
    log.info("Job %s: stop requested", job.id)
    job.stop_token.stop()
    job.status = "Stopping…"
    self._refresh(job)

  def _show_queue_menu(self, position: QtCore.QPoint) -> None:
    item = self.queue.itemAt(position)
    if item is None:
      return
    job = self.queue.job_at(self.queue.row(item))
    menu = QtWidgets.QMenu(self)
    if job.state == RUNNING:
      menu.addAction("Stop", lambda: self.stop_job(job))
    if job.output_path and os.path.exists(job.output_path):
      menu.addAction("Open Transcript", lambda: open_file(job.output_path))
    menu.addAction("Show in Finder", lambda: reveal_in_finder(job.output_path or job.path))
    if job.state in FINISHED_STATES:
      menu.addAction("Transcribe Again", lambda: self._retry(job))
    if job.state != RUNNING:
      menu.addSeparator()
      menu.addAction("Remove from List", lambda: self._remove(job))
    menu.exec(self.queue.viewport().mapToGlobal(position))

  def _on_double_click(self, item: QtWidgets.QListWidgetItem) -> None:
    job = self.queue.job_at(self.queue.row(item))
    if job and job.output_path and os.path.exists(job.output_path):
      open_file(job.output_path)

  def _retry(self, job: Job) -> None:
    if job is None or job.state not in FINISHED_STATES:
      return
    job.state, job.status, job.error, job.warning = QUEUED, "Waiting", None, None
    job.lines, job.progress = [], INDETERMINATE
    job.output_path, job.saved_options = None, None
    self._refresh(job)
    if self.pane.job is job:
      self.pane.show_job(job)
    self._start_next()

  def _save_as(self, job: Job) -> None:
    if job is None or not job.lines:
      return
    options = self.settings.output_options()
    suggested = os.path.join(
      os.path.expanduser("~/Documents"),
      os.path.basename(transcript_path_for(job.path, options)),
    )
    path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Save Transcript", suggested)
    if not path:
      return
    try:
      job.output_path = write_transcript(path, job.lines, options)
      job.saved_options = options
      job.state, job.error = DONE, None
      job.status = "Done"
    except OSError as error:
      job.error = f"Couldn't save there either.\n{error.strerror or error}"
    self._refresh(job)
    if self.pane.job is job:
      self.pane.show_job(job)
    if not self.is_busy():
      self._queue_idle()

  def _on_selection_changed(self) -> None:
    job = self.queue.selected_job()
    self._follow_running = job is None or job is self._running
    if job is None:
      self.pane.show_empty(bool(self.jobs))
    else:
      self.pane.show_job(job)

  def _refresh_empty_state(self) -> None:
    self.clear_button.setVisible(any(j.state in FINISHED_STATES for j in self.jobs.values()))
    self.pane.set_first_run(not self.settings.flag("ui/onboarded", False))
    if self.queue.selected_job() is None:
      self.pane.show_empty(bool(self.jobs))
    self._update_title()

  def _update_title(self) -> None:
    active = [j for j in self.jobs.values() if j.state in (QUEUED, RUNNING)]
    if self._running is not None:
      done = sum(1 for j in self.jobs.values() if j.state in FINISHED_STATES)
      total = done + len(active)
      self.setWindowTitle(f"Transcribing {done + 1} of {total} - UIWhisperCPP")
    else:
      self.setWindowTitle("UIWhisperCPP")

  def _on_bar_changed(self) -> None:
    if self._running is not None:
      self.flash("Changes apply to the next file. Use Transcribe Again to redo this one.")

  # ---------- running jobs ----------

  def is_busy(self) -> bool:
    return self._running is not None

  def _ensure_worker(self) -> None:
    """(Re)start the single worker thread if it isn't running."""
    if self._worker is None or not self._worker.is_alive():
      self._worker = threading.Thread(target=self._worker_loop, name="transcriber", daemon=True)
      self._worker.start()

  def _worker_loop(self) -> None:
    # One long-lived thread runs every job: MLX (Parakeet) keeps its GPU stream
    # per thread, so a fresh thread per file breaks after a failure.
    while True:
      args = self._work_queue.get()
      try:
        self._work(*args)
      except BaseException:  # noqa: BLE001 - never let the worker die silently
        log.exception("Worker crashed while running job %s", args[0])
        self.signals.failed.emit(args[0], _GENERIC_ERROR)

  def _start_next(self) -> None:
    if self._running is not None or self._choosing_target:
      return
    job = next((j for j in self.jobs.values() if j.state == QUEUED), None)
    if job is None:
      self._queue_idle()
      return
    model_key = self.bottom.model_key()
    if not model_key:
      return
    options = self.settings.output_options()
    if job.target_path is None:
      self._choosing_target = True
      try:
        target = self._choose_target(job, options)
      finally:
        self._choosing_target = False
      if target is None:
        job.state, job.status = STOPPED, "Skipped"
        job.error = None
        self._refresh(job)
        self._refresh_empty_state()
        QtCore.QTimer.singleShot(0, self._start_next)
        return
      job.target_path = target
    elif os.path.splitext(job.target_path)[1] != "." + options.file_type:
      # The output format changed since the name was chosen: the new name may
      # belong to someone else's file, so it goes through the clash check again.
      job.target_path = None
      self._start_next()
      return
    self._ensure_worker()
    self._running = job
    job.state, job.status, job.progress = RUNNING, "Getting ready…", INDETERMINATE
    job.lines, job.error, job.warning = [], None, None
    job.output_path, job.saved_options = None, options
    job.stop_token = StopToken()
    job.summary = self.bottom.summary()
    self._refresh(job)
    if self._follow_running or self.queue.selected_job() is None:
      self.queue.select_job(job.id)
    self._update_title()
    snapshot = dict(
      model_key=model_key,
      language=self.bottom.language_code(),
      num_speakers=self.bottom.num_speakers(),
      options=options,
      target=job.target_path,
      stop=job.stop_token,
    )
    self._work_queue.put((job.id, job.path, snapshot))

  def _choose_target(self, job: Job, options: OutputOptions) -> str | None:
    """Where to save; asks when a file with that name already exists.

    Returns None when the user chose to skip this file.
    """
    target = transcript_path_for(job.path, options)
    # Another file in this list already writes there: never share a target.
    claimed = {
      j.target_path for j in self.jobs.values() if j is not job and j.target_path
    }
    while target in claimed:
      target = numbered_path(target, taken=claimed)
    if not os.path.exists(target):
      return target
    if self._clash_choice == "replace":
      return target
    if self._clash_choice == "keep":
      return numbered_path(target, taken=claimed)
    if self._clash_choice == "skip":
      return None
    alternative = numbered_path(target, taken=claimed)
    box = QtWidgets.QMessageBox(self)
    box.setIcon(QtWidgets.QMessageBox.Icon.Question)
    box.setWindowTitle("A transcript already exists")
    box.setText("A transcript of this recording already exists.")
    box.setInformativeText(
      f"Replace “{os.path.basename(target)}”, or keep both (the new one is "
      f"saved as “{os.path.basename(alternative)}”)?"
    )
    keep = box.addButton("Keep Both", QtWidgets.QMessageBox.ButtonRole.AcceptRole)
    replace = box.addButton("Replace", QtWidgets.QMessageBox.ButtonRole.DestructiveRole)
    skip = box.addButton("Skip This File", QtWidgets.QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(keep)
    box.setEscapeButton(skip)
    others = sum(1 for j in self.jobs.values() if j.state == QUEUED and j is not job)
    remember = None
    if others:
      remember = QtWidgets.QCheckBox("Do the same for the other files")
      box.setCheckBox(remember)
    box.exec()
    clicked = box.clickedButton()
    choice = "skip" if clicked is skip else "replace" if clicked is replace else "keep"
    if remember is not None and remember.isChecked():
      self._clash_choice = choice
    if choice == "skip":
      return None
    log.info("Job %s: name clash on %s -> %s", job.id, target, choice)
    return target if choice == "replace" else alternative

  def _work(self, job_id: int, audio_path: str, snapshot: dict) -> None:
    """Worker thread: transcribe one file. Talks to the UI only via signals."""
    signals = self.signals
    options: OutputOptions = snapshot["options"]
    target: str = snapshot["target"]
    stop: StopToken = snapshot["stop"]
    log.info(
      "Job %s start: %s | model=%s language=%s speakers=%s output=%s -> %s",
      job_id, audio_path, snapshot["model_key"], snapshot["language"] or "auto",
      snapshot["num_speakers"], options, target,
    )
    live: list[Segment] = []
    last_save = 0.0
    save_failed = False

    def save_partial(force: bool = False) -> None:
      # Partial saves protect against crashes; a failure here must never
      # abort the transcription itself (the text is still kept in the UI).
      nonlocal last_save, save_failed
      if save_failed or (not force and time.monotonic() - last_save < _PARTIAL_SAVE_SECONDS):
        return
      try:
        write_transcript(target, lines_from_segments(live), options)
        last_save = time.monotonic()
      except OSError as error:
        save_failed = True
        log.warning("Job %s: can't write partial transcript: %s", job_id, error)

    try:
      def on_segment(segment: Segment) -> None:
        live.append(segment)
        signals.line.emit(job_id, lines_from_segments([segment])[0])
        save_partial()

      def on_progress(percent: int) -> None:
        signals.progress.emit(job_id, int(percent))

      segments = self.model_manager.transcribe(
        audio_path,
        model_key=snapshot["model_key"],
        language=snapshot["language"],
        on_segment=on_segment,
        on_progress=on_progress,
        on_status=lambda text: signals.status.emit(job_id, text),
        stop=stop,
      )
      lines = lines_from_segments(segments)
      if lines:
        # Everything transcribed is on disk before the (slower) speaker step.
        live[:] = segments
        save_partial(force=True)
      if snapshot["num_speakers"] is not None and lines:
        signals.status.emit(job_id, "Separating speakers…")
        signals.progress.emit(job_id, INDETERMINATE)
        try:
          speaker_segments = self.diarizer.diarize(
            audio_path,
            num_speakers=snapshot["num_speakers"],
            on_progress=on_progress,
            on_log=lambda text: signals.status.emit(job_id, text),
            stop=stop,
          )
          lines = lines_from_turns(label_segments(segments, speaker_segments))
        except JobStopped:
          raise
        except Exception as error:  # noqa: BLE001 - the transcript itself is fine
          if stop.stopped:
            raise JobStopped() from error
          log.exception("Job %s: speaker separation failed", job_id)
          reason = str(error) if isinstance(error, UserFacingError) else "an unexpected error"
          signals.warning.emit(
            job_id, f"Saved without speaker labels: separating speakers failed ({reason})."
          )
      signals.lines_replaced.emit(job_id, lines)
      signals.finished.emit(job_id)
    except JobStopped:
      log.info("Job %s stopped by the user", job_id)
      live_lines = lines_from_segments(live)
      signals.lines_replaced.emit(job_id, live_lines)
      signals.stopped.emit(job_id)
    except Exception as error:  # noqa: BLE001 - routed below
      if stop.stopped:
        log.info("Job %s stopped by the user (%s)", job_id, type(error).__name__)
        signals.lines_replaced.emit(job_id, lines_from_segments(live))
        signals.stopped.emit(job_id)
      elif isinstance(error, UserFacingError):
        log.warning("Job %s failed: %s", job_id, error)
        signals.failed.emit(job_id, str(error))
      elif isinstance(error, MemoryError):
        log.exception("Job %s ran out of memory", job_id)
        signals.failed.emit(
          job_id, "Your Mac ran out of memory for this model.\n"
          "Close other apps or choose a smaller model, then try again.",
        )
      else:
        log.exception("Job %s failed: %s", job_id, audio_path)
        signals.failed.emit(job_id, _GENERIC_ERROR)

  def _job(self, job_id: int) -> Job | None:
    return self.jobs.get(job_id)

  def _refresh(self, job: Job) -> None:
    self.queue.refresh(job)
    if self.pane.job is job:
      self.pane.refresh_status()

  def _on_status(self, job_id: int, text: str) -> None:
    text = text.strip().replace("...", "…")
    log.info("Job %s: %s", job_id, text)
    if (job := self._job(job_id)) and job.status != "Stopping…":
      job.status = text
      # Download messages carry their own percentage.
      if "%" in text:
        job.progress = INDETERMINATE
      self._refresh(job)

  def _on_progress(self, job_id: int, percent: int) -> None:
    if job := self._job(job_id):
      if percent != INDETERMINATE and percent // 25 != max(job.progress, 0) // 25:
        log.debug("Job %s: %s%%", job_id, percent)
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

  def _on_warning(self, job_id: int, text: str) -> None:
    if job := self._job(job_id):
      job.warning = text

  def _save_final(self, job: Job) -> bool:
    """Write the job's transcript to its chosen path; record any failure."""
    options = job.saved_options or self.settings.output_options()
    try:
      job.output_path = write_transcript(job.target_path, job.lines, options)
      job.saved_options = options
      return True
    except OSError as error:
      log.warning("Job %s: can't save %s: %s", job.id, job.target_path, error)
      job.output_path = None
      reason = error.strerror or str(error)
      job.error = (
        "Couldn't save the transcript next to the recording.\n"
        f"The folder may be read-only or full ({reason}). Use Save As… to choose "
        "another place, or Copy Text."
      )
      return False

  def _on_finished(self, job_id: int) -> None:
    job = self._job(job_id)
    if job is None:
      return
    try:
      if not job.lines:
        job.state, job.status = DONE, "No speech found"
        job.warning = "No speech was found in this file, so nothing was saved."
      elif self._save_final(job):
        job.state, job.status = DONE, "Done"
        self.settings.set_value("ui/onboarded", "true")
      else:
        job.state, job.status = FAILED, "Couldn't save"
    finally:
      self._job_ended(job)

  def _on_failed(self, job_id: int, message: str) -> None:
    job = self._job(job_id)
    if job is None:
      return
    try:
      job.state, job.status, job.error = FAILED, "Didn't finish", message
      if job.lines and self._save_final(job):
        job.error = message  # keep the original reason; the status names the file
    finally:
      self._job_ended(job)

  def _on_stopped(self, job_id: int) -> None:
    job = self._job(job_id)
    if job is None:
      return
    try:
      job.state, job.status = STOPPED, "Stopped"
      if job.lines:
        self._save_final(job)
    finally:
      self._job_ended(job)

  def _job_ended(self, job: Job) -> None:
    log.info(
      "Job %s %s: %d lines -> %s%s", job.id, job.state, len(job.lines),
      job.output_path or "(nothing saved)",
      f" | {job.error.splitlines()[0]}" if job.error else "",
    )
    if self._running is job:
      self._running = None
    job.stop_token = None
    try:
      self._refresh(job)
      if self.pane.job is job:
        self.pane.show_job(job)
      self._refresh_empty_state()
    except Exception:  # noqa: BLE001 - a display glitch must never stall the queue
      log.exception("Couldn't refresh the view after job %s", job.id)
    finally:
      QtCore.QTimer.singleShot(0, self._start_next)

  def _unsaved_jobs(self) -> list[Job]:
    """Finished files whose text exists only in the app (saving failed)."""
    return [
      j for j in self.jobs.values()
      if j.state in FINISHED_STATES and j.lines and not j.output_path
    ]

  def _queue_idle(self) -> None:
    self._clash_choice = None
    self._update_title()
    if self._pending_update is not None and not self._unsaved_jobs():
      self._install_update(*self._pending_update)

  # ---------- settings / updates / lifecycle ----------

  def open_settings(self, tab: int) -> None:
    if self._settings_dialog is None:
      self._settings_dialog = SettingsDialog(
        self, self.settings, self.pane.set_options, self.is_busy, self.request_update_install,
      )
    self._settings_dialog.show_tab(tab)

  def request_update_install(self, new_bundle: str, target: str) -> str:
    """Install now ("now"), once the queue is done and saved ("later"), or "failed"."""
    busy = self.is_busy() or any(j.state == QUEUED for j in self.jobs.values())
    if busy or self._unsaved_jobs():
      self._pending_update = (new_bundle, target)
      log.info("Update downloaded; it installs once files are finished and saved")
      return "later"
    return "now" if self._install_update(new_bundle, target) else "failed"

  def _install_update(self, new_bundle: str, target: str) -> bool:
    import shutil

    from uiwhispercpp import updater
    self._pending_update = None
    try:
      updater.install_and_relaunch(new_bundle, target)
    except OSError as error:
      log.exception("Couldn't start the update installer")
      shutil.rmtree(os.path.dirname(os.path.dirname(new_bundle)), ignore_errors=True)
      QtWidgets.QMessageBox.warning(
        self, "Update not installed",
        f"The update couldn't be installed ({error.strerror or error}). Try again later.",
      )
      return False
    QtWidgets.QApplication.instance().setProperty("updating", True)
    QtWidgets.QApplication.quit()
    return True

  def closeEvent(self, event: QtGui.QCloseEvent) -> None:
    updating = QtWidgets.QApplication.instance().property("updating")
    unsaved = self._unsaved_jobs()
    if unsaved and not self.is_busy() and not updating:
      box = QtWidgets.QMessageBox(self)
      box.setIcon(QtWidgets.QMessageBox.Icon.Warning)
      box.setWindowTitle("Quit without saving?")
      names = ", ".join(f"\u201c{j.name}\u201d" for j in unsaved[:3])
      box.setText(f"The text of {names} couldn't be saved yet.")
      box.setInformativeText(
        "If you quit now, it is lost. Go back and use Save As… or Copy Text first."
      )
      back = box.addButton("Go Back", QtWidgets.QMessageBox.ButtonRole.RejectRole)
      quit_button = box.addButton(
        "Quit Without Saving", QtWidgets.QMessageBox.ButtonRole.DestructiveRole
      )
      box.setDefaultButton(back)
      box.exec()
      if box.clickedButton() is not quit_button:
        self.queue.select_job(unsaved[0].id)
        event.ignore()
        return
    if self.is_busy() and not updating:
      waiting = sum(1 for j in self.jobs.values() if j.state == QUEUED)
      more = f" ({waiting} more waiting)" if waiting else ""
      box = QtWidgets.QMessageBox(self)
      box.setIcon(QtWidgets.QMessageBox.Icon.Warning)
      box.setWindowTitle("Quit while transcribing?")
      box.setText(f"“{self._running.name}” is still being transcribed{more}.")
      box.setInformativeText(
        "If you quit now, this file won't be finished and the waiting files won't "
        "be transcribed. Text already saved stays on disk."
      )
      keep = box.addButton("Keep Transcribing", QtWidgets.QMessageBox.ButtonRole.RejectRole)
      quit_button = box.addButton("Quit Anyway", QtWidgets.QMessageBox.ButtonRole.DestructiveRole)
      box.setDefaultButton(keep)
      box.exec()
      if box.clickedButton() is not quit_button:
        event.ignore()
        return
      if self._running is not None and self._running.stop_token is not None:
        self._running.stop_token.stop()
    event.accept()


def _sweep_leftovers() -> None:
  # Runs in the background so startup stays instant.
  from uiwhispercpp import updater
  from uiwhispercpp.models.audio import sweep_temp_files
  try:
    sweep_temp_files()
    updater.sweep_leftovers()
  except Exception:  # noqa: BLE001 - housekeeping must never matter
    log.exception("Cleaning up old temporary files failed")


def run_program() -> None:
  threading.Thread(target=_sweep_leftovers, name="cleanup", daemon=True).start()
  app = QtWidgets.QApplication(sys.argv)
  app.setApplicationName("UIWhisperCPP")
  app.setOrganizationName("Camphouse")
  apply_theme(app)

  try:
    window = MainWindow()
  except Exception:  # noqa: BLE001 - a corrupt preference must not brick the app
    log.exception("Main window failed to start; resetting window settings")
    Settings().reset_ui()
    window = MainWindow()
  window.show()

  sys.exit(app.exec())
