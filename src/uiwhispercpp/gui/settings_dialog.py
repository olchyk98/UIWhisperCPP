"""Settings window (Cmd+,): output format and app updates.

Changes apply immediately, like native macOS preferences; there is no OK button.
"""
from collections.abc import Callable
import threading

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import (
  QCheckBox,
  QComboBox,
  QDialog,
  QFormLayout,
  QHBoxLayout,
  QLabel,
  QLineEdit,
  QPlainTextEdit,
  QProgressBar,
  QPushButton,
  QTabWidget,
  QVBoxLayout,
  QWidget,
)

from uiwhispercpp import app_log, updater
from uiwhispercpp.gui.debug_log import show_debug_log
from uiwhispercpp.gui.theme import DANGER
from uiwhispercpp.settings import Settings
from uiwhispercpp.transcript import (
  FILE_SRT,
  FILE_TXT,
  PRECISION_MS,
  PRECISION_SECONDS,
  SAMPLE_LINES,
  TIMESTAMPS_OFF,
  TIMESTAMPS_RANGE,
  TIMESTAMPS_START,
  OutputOptions,
  render_transcript,
)
from uiwhispercpp.version import DEFAULT_UPDATE_REPO

_log = app_log.get("updater")

_TIMESTAMP_CHOICES = [
  ("Start and end", TIMESTAMPS_RANGE),
  ("Start only", TIMESTAMPS_START),
  ("No timestamps", TIMESTAMPS_OFF),
]
_PRECISION_CHOICES = [
  ("With milliseconds (00:01:05.250)", PRECISION_MS),
  ("To the second (00:01:05)", PRECISION_SECONDS),
]
_FILE_CHOICES = [
  ("Plain text (.txt)", FILE_TXT),
  ("Subtitles for video players (.srt)", FILE_SRT),
]


def _combo(choices: list[tuple[str, str]], current: str) -> QComboBox:
  combo = QComboBox()
  for label, value in choices:
    combo.addItem(label, value)
  index = combo.findData(current)
  combo.setCurrentIndex(max(0, index))
  return combo


class _OutputTab(QWidget):
  def __init__(self, settings: Settings, on_change: Callable[[OutputOptions], None]) -> None:
    super().__init__()
    self._settings = settings
    self._on_change = on_change
    options = settings.output_options()

    self.timestamps = _combo(_TIMESTAMP_CHOICES, options.timestamps)
    self.precision = _combo(_PRECISION_CHOICES, options.precision)
    self.speaker_labels = QCheckBox("Label speakers (when Separate speakers is on)")
    self.speaker_labels.setChecked(options.speaker_labels)
    self.file_type = _combo(_FILE_CHOICES, options.file_type)
    self.srt_note = QLabel("Subtitles always include start and end times.")
    self.srt_note.setObjectName("FileStatus")

    self.preview = QPlainTextEdit()
    self.preview.setReadOnly(True)
    self.preview.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    mono = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
    mono.setPointSizeF(max(10.0, QFont().pointSizeF() - 1))
    self.preview.setFont(mono)

    form = QFormLayout()
    form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
    form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.FieldsStayAtSizeHint)
    form.addRow("File type:", self.file_type)
    form.addRow("Timestamps:", self.timestamps)
    form.addRow("Time detail:", self.precision)
    form.addRow("", self.srt_note)
    form.addRow("", self.speaker_labels)

    preview_title = QLabel("PREVIEW")
    preview_title.setObjectName("PaneTitle")

    layout = QVBoxLayout(self)
    layout.addLayout(form)
    layout.addSpacing(8)
    layout.addWidget(preview_title)
    layout.addWidget(self.preview)
    applies = QLabel("Changes apply to files transcribed from now on.")
    applies.setObjectName("Hint")
    layout.addWidget(applies)
    layout.addStretch()

    for combo in (self.timestamps, self.precision, self.file_type):
      combo.currentIndexChanged.connect(self._changed)
    self.speaker_labels.toggled.connect(self._changed)
    self._refresh()

  def options(self) -> OutputOptions:
    return OutputOptions(
      timestamps=self.timestamps.currentData(),
      precision=self.precision.currentData(),
      speaker_labels=self.speaker_labels.isChecked(),
      file_type=self.file_type.currentData(),
    )

  def _refresh(self) -> None:
    options = self.options()
    is_srt = options.file_type == FILE_SRT
    self.timestamps.setEnabled(not is_srt)
    self.precision.setEnabled(not is_srt and options.timestamps != TIMESTAMPS_OFF)
    self.srt_note.setVisible(is_srt)
    text = render_transcript(SAMPLE_LINES, options).rstrip("\n")
    self.preview.setPlainText(text)
    self._fit_preview()

  def _fit_preview(self) -> None:
    """Size the preview to its content (wrapped lines included), never clipped."""
    metrics = self.preview.fontMetrics()
    margin = int(self.preview.document().documentMargin())
    available = max(1, self.preview.viewport().width() - 2 * margin - 4)
    lines = 0
    for line in self.preview.toPlainText().split("\n"):
      width = metrics.horizontalAdvance(line)
      lines += max(1, -(-width // available))  # ceil division
    frame = 2 * margin + 2 * self.preview.frameWidth() + 6
    # Reserve the tallest sample (7 SRT lines) so switching formats never
    # resizes the window; only unusually narrow widths grow it further.
    height = max(7, lines) * metrics.lineSpacing() + frame
    if self.preview.height() != height:
      self.preview.setFixedHeight(height)
    window = self.window()
    if window is not self:
      window.layout().activate()
      window.adjustSize()

  def resizeEvent(self, event) -> None:
    super().resizeEvent(event)
    self._fit_preview()

  def _changed(self) -> None:
    self._refresh()
    options = self.options()
    self._settings.set_output_options(options)
    self._on_change(options)


class _UpdateSignals(QObject):
  checked = Signal(object)        # Release | None
  progress = Signal(int, int)     # bytes done, bytes total
  downloaded = Signal(str)        # path of the new .app bundle
  failed = Signal(str)


class _UpdatesTab(QWidget):
  def __init__(
    self,
    settings: Settings,
    is_busy: Callable[[], bool],
    request_install: Callable[[str, str], str],
  ) -> None:
    super().__init__()
    self._settings = settings
    self._is_busy = is_busy
    self._release: updater.Release | None = None
    self._cancelled = False
    self._signals = _UpdateSignals()
    self._signals.checked.connect(self._on_checked)
    self._signals.progress.connect(self._on_progress)
    self._signals.downloaded.connect(self._on_downloaded)
    self._signals.failed.connect(self._on_failed)

    self._request_install = request_install
    version = QLabel(f"You have version {updater.current_version()}")
    version.setObjectName("FileTitle")

    self.repo = QLineEdit(settings.update_repo())
    self.repo.setPlaceholderText(DEFAULT_UPDATE_REPO)
    self.repo.editingFinished.connect(self._save_repo)
    reset = QPushButton("Use Default")
    reset.setObjectName("Flat")
    reset.clicked.connect(self._reset_repo)
    repo_row = QHBoxLayout()
    repo_row.addWidget(self.repo, 1)
    repo_row.addWidget(reset)
    repo_hint = QLabel("GitHub repository to download releases from (owner/repo).")
    repo_hint.setObjectName("FileStatus")
    self.repo_warning = QLabel(
      "Not the official source: only use a repository you trust, since updates "
      "replace the app."
    )
    self.repo_warning.setWordWrap(True)
    self.repo_warning.setStyleSheet(f"color: {DANGER};")
    self.repo.textChanged.connect(self._update_repo_warning)

    self.check_button = QPushButton("Check for Updates")
    self.check_button.clicked.connect(self._check)
    self.update_button = QPushButton("Update and Restart")
    self.update_button.setDefault(True)
    self.update_button.clicked.connect(self._download)
    self.update_button.hide()
    buttons = QHBoxLayout()
    buttons.addWidget(self.check_button)
    buttons.addWidget(self.update_button)
    buttons.addStretch()

    self.status = QLabel("")
    self.status.setWordWrap(True)
    self.status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    self.progress = QProgressBar()
    self.progress.setTextVisible(False)
    self.progress.setFixedHeight(6)
    self.progress.hide()

    # Where updates come from is a developer concern: tucked under Advanced.
    advanced = QWidget()
    advanced_layout = QVBoxLayout(advanced)
    advanced_layout.setContentsMargins(0, 0, 0, 0)
    advanced_layout.addWidget(QLabel("Update source"))
    advanced_layout.addLayout(repo_row)
    advanced_layout.addWidget(repo_hint)
    advanced_layout.addWidget(self.repo_warning)
    advanced_toggle = QPushButton("Advanced \u25b8")
    advanced_toggle.setObjectName("Quiet")
    advanced_toggle.setCheckable(True)

    def toggle_advanced(shown: bool) -> None:
      advanced.setVisible(shown)
      advanced_toggle.setText("Advanced \u25be" if shown else "Advanced \u25b8")

    advanced_toggle.toggled.connect(toggle_advanced)
    unofficial = settings.update_repo() != DEFAULT_UPDATE_REPO
    advanced_toggle.setChecked(unofficial)
    advanced.setVisible(unofficial)

    layout = QVBoxLayout(self)
    layout.addWidget(version)
    layout.addSpacing(10)
    layout.addLayout(buttons)
    layout.addWidget(self.progress)
    layout.addWidget(self.status)
    layout.addStretch()
    layout.addWidget(advanced_toggle, 0, Qt.AlignmentFlag.AlignLeft)
    layout.addWidget(advanced)
    self._update_repo_warning()

  def _update_repo_warning(self) -> None:
    try:
      official = updater.parse_repo(self.repo.text() or DEFAULT_UPDATE_REPO) == DEFAULT_UPDATE_REPO
    except updater.UpdateError:
      official = True  # invalid input gets its own message on check
    self.repo_warning.setVisible(not official)

  def _save_repo(self) -> None:
    self._settings.set_update_repo(self.repo.text())

  def _reset_repo(self) -> None:
    self.repo.setText(DEFAULT_UPDATE_REPO)
    self._save_repo()

  def _set_status(self, text: str, error: bool = False) -> None:
    self.status.setStyleSheet(f"color: {DANGER};" if error else "")
    self.status.setText(text)

  def _check(self) -> None:
    self._save_repo()
    repo = self._settings.update_repo()
    try:
      updater.parse_repo(repo)
    except updater.UpdateError as error:
      self._set_status(str(error), error=True)
      return
    self.check_button.setEnabled(False)
    self.update_button.hide()
    self._set_status("Checking…")

    def work() -> None:
      try:
        self._signals.checked.emit(updater.check_for_update(repo))
      except updater.UpdateError as error:
        self._signals.failed.emit(str(error))
      except Exception:  # noqa: BLE001 - surface anything to the user
        _log.exception("Update check failed")
        self._signals.failed.emit(
          "Couldn't check for updates right now. Try again later. "
          "(Details are in Help > Debug Log.)"
        )

    threading.Thread(target=work, daemon=True).start()

  def _on_checked(self, release: "updater.Release | None") -> None:
    _log.info("Update check: %s", release or "up to date")
    self.check_button.setEnabled(True)
    self._release = release
    if release is None:
      self._set_status(f"You have the latest version ({updater.current_version()}).")
      return
    can_update, reason = updater.can_self_update()
    size_mb = release.asset_size / 1_000_000
    message = f"Version {release.version} is available ({size_mb:.0f} MB download)."
    if not can_update:
      self._set_status(f"{message}\n{reason}")
      return
    self._set_status(message)
    self.update_button.show()

  def _download(self) -> None:
    if self._release is None:
      return
    if self._is_busy():
      self._set_status("Finish transcribing first, then update.", error=True)
      return
    self._cancelled = False
    self.update_button.setEnabled(False)
    self.check_button.setEnabled(False)
    self.repo.setEnabled(False)
    self.progress.setRange(0, 0)
    self.progress.show()
    self._set_status("Downloading the update… Keep this window open.")
    release = self._release

    def work() -> None:
      try:
        bundle = updater.download_update(
          release,
          on_progress=lambda done, total: self._signals.progress.emit(done, total),
          is_cancelled=lambda: self._cancelled,
        )
        self._signals.downloaded.emit(bundle)
      except updater.UpdateError as error:
        self._signals.failed.emit(str(error))
      except Exception:  # noqa: BLE001
        _log.exception("Update download failed")
        self._signals.failed.emit(
          "The update couldn't be downloaded. Try again later. "
          "(Details are in Help > Debug Log.)"
        )

    threading.Thread(target=work, daemon=True).start()

  def _on_progress(self, done: int, total: int) -> None:
    if total <= 0:
      self._set_status(f"Downloading the update… {done / 1_000_000:.0f} MB")
      return
    percent = int(done * 100 / total)
    self.progress.setRange(0, 100)
    self.progress.setValue(percent)
    self._set_status(
      f"Downloading the update… {percent}% ({done / 1_000_000:.0f} of "
      f"{total / 1_000_000:.0f} MB). Keep this window open."
    )

  def _on_downloaded(self, new_bundle: str) -> None:
    target = updater.running_app_bundle()
    if target is None:
      self._on_failed("The update download is incomplete or damaged. Try again.")
      return
    self.progress.setRange(0, 0)
    self._set_status("Installing… The app will close and reopen by itself.")
    outcome = self._request_install(new_bundle, target)
    if outcome == "later":
      # Files are still being transcribed: install once they're done.
      self.progress.hide()
      self._set_status(
        "The update is ready. It installs and restarts the app automatically "
        "when the current files are finished and saved."
      )
    elif outcome == "failed":
      self._on_failed("The update couldn't be installed. Try again later.")

  def _on_failed(self, message: str) -> None:
    _log.warning("Update failed: %s", message)
    self.progress.hide()
    self.check_button.setEnabled(True)
    self.update_button.setEnabled(True)
    self.repo.setEnabled(True)
    self._set_status(message, error=True)

  def cancel(self) -> None:
    self._cancelled = True


class SettingsDialog(QDialog):
  def __init__(
    self,
    parent: QWidget,
    settings: Settings,
    on_output_change: Callable[[OutputOptions], None],
    is_busy: Callable[[], bool],
    request_install: Callable[[str, str], str],
  ) -> None:
    super().__init__(parent)
    self.setWindowTitle("Settings")
    self.setMinimumWidth(460)
    tabs = QTabWidget()
    tabs.setDocumentMode(True)
    self.output_tab = _OutputTab(settings, on_output_change)
    self.updates_tab = _UpdatesTab(settings, is_busy, request_install)
    tabs.addTab(self.output_tab, "Output")
    tabs.addTab(self.updates_tab, "Updates")
    # Deliberately quiet: a small muted link for troubleshooting.
    debug_link = QPushButton("Debug log")
    debug_link.setObjectName("Quiet")
    debug_link.setCursor(Qt.CursorShape.PointingHandCursor)
    debug_link.setToolTip("Raw program output, for troubleshooting (⌥⌘L)")
    debug_link.clicked.connect(show_debug_log)
    footer = QHBoxLayout()
    footer.addStretch()
    footer.addWidget(debug_link)
    layout = QVBoxLayout(self)
    layout.addWidget(tabs)
    layout.addLayout(footer)
    self.tabs = tabs

  def show_tab(self, index: int) -> None:
    self.tabs.setCurrentIndex(index)
    self.show()
    self.raise_()
    self.activateWindow()

  def reject(self) -> None:
    # Closing the window mid-download cancels it; settings are already saved.
    self.updates_tab.cancel()
    super().reject()
