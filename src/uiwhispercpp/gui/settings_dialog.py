"""Settings window (Cmd+,): output format and app updates.

Changes apply immediately, like native macOS preferences; there is no OK button.
"""
from collections.abc import Callable
import threading

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import (
  QApplication,
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

from uiwhispercpp import updater
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

_TIMESTAMP_CHOICES = [
  ("Start and end", TIMESTAMPS_RANGE),
  ("Start only", TIMESTAMPS_START),
  ("None", TIMESTAMPS_OFF),
]
_PRECISION_CHOICES = [
  ("Milliseconds  (00:01:05.250)", PRECISION_MS),
  ("Seconds  (00:01:05)", PRECISION_SECONDS),
]
_FILE_CHOICES = [
  ("Plain text (.txt)", FILE_TXT),
  ("Subtitles (.srt)", FILE_SRT),
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
    self.speaker_labels = QCheckBox("Label speakers  (when Separate speakers is on)")
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
    form.addRow("Save as:", self.file_type)
    form.addRow("Timestamps:", self.timestamps)
    form.addRow("Precision:", self.precision)
    form.addRow("", self.srt_note)
    form.addRow("", self.speaker_labels)

    preview_title = QLabel("PREVIEW")
    preview_title.setObjectName("PaneTitle")

    layout = QVBoxLayout(self)
    layout.addLayout(form)
    layout.addSpacing(8)
    layout.addWidget(preview_title)
    layout.addWidget(self.preview)
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
  def __init__(self, settings: Settings, is_busy: Callable[[], bool]) -> None:
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

    version = QLabel(f"UIWhisperCPP {updater.current_version()}")
    version.setObjectName("FileTitle")

    self.repo = QLineEdit(settings.update_repo())
    self.repo.setPlaceholderText(DEFAULT_UPDATE_REPO)
    self.repo.editingFinished.connect(self._save_repo)
    reset = QPushButton("Reset")
    reset.setObjectName("Flat")
    reset.clicked.connect(self._reset_repo)
    repo_row = QHBoxLayout()
    repo_row.addWidget(self.repo, 1)
    repo_row.addWidget(reset)
    repo_hint = QLabel("GitHub repository to download releases from (owner/repo).")
    repo_hint.setObjectName("FileStatus")

    self.check_button = QPushButton("Check for updates")
    self.check_button.clicked.connect(self._check)
    self.update_button = QPushButton("Update and restart")
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

    layout = QVBoxLayout(self)
    layout.addWidget(version)
    layout.addSpacing(10)
    layout.addWidget(QLabel("Update source"))
    layout.addLayout(repo_row)
    layout.addWidget(repo_hint)
    layout.addSpacing(10)
    layout.addLayout(buttons)
    layout.addWidget(self.progress)
    layout.addWidget(self.status)
    layout.addStretch()

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
    self._set_status("Checking...")

    def work() -> None:
      try:
        self._signals.checked.emit(updater.check_for_update(repo))
      except updater.UpdateError as error:
        self._signals.failed.emit(str(error))
      except Exception as error:  # noqa: BLE001 - surface anything to the user
        self._signals.failed.emit(f"Update check failed: {error}")

    threading.Thread(target=work, daemon=True).start()

  def _on_checked(self, release: "updater.Release | None") -> None:
    self.check_button.setEnabled(True)
    self._release = release
    if release is None:
      self._set_status(f"You're up to date ({updater.current_version()}).")
      return
    can_update, reason = updater.can_self_update()
    size_mb = release.asset_size / 1_000_000
    message = f"Version {release.version} is available ({size_mb:.0f} MB)."
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
    self._set_status("Downloading...")
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
      except Exception as error:  # noqa: BLE001
        self._signals.failed.emit(f"Update failed: {error}")

    threading.Thread(target=work, daemon=True).start()

  def _on_progress(self, done: int, total: int) -> None:
    if total <= 0:
      self._set_status(f"Downloading... {done / 1_000_000:.0f} MB")
      return
    percent = int(done * 100 / total)
    self.progress.setRange(0, 100)
    self.progress.setValue(percent)
    self._set_status(
      f"Downloading... {percent}%  ({done / 1_000_000:.0f} of {total / 1_000_000:.0f} MB)"
    )

  def _on_downloaded(self, new_bundle: str) -> None:
    target = updater.running_app_bundle()
    if target is None:
      self._on_failed("Can't find the running app to replace.")
      return
    self.progress.setRange(0, 0)
    self._set_status("Installing and restarting...")
    updater.install_and_relaunch(new_bundle, target)
    QApplication.instance().setProperty("updating", True)
    QApplication.quit()

  def _on_failed(self, message: str) -> None:
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
  ) -> None:
    super().__init__(parent)
    self.setWindowTitle("Settings")
    self.setMinimumWidth(460)
    tabs = QTabWidget()
    tabs.setDocumentMode(True)
    self.output_tab = _OutputTab(settings, on_output_change)
    self.updates_tab = _UpdatesTab(settings, is_busy)
    tabs.addTab(self.output_tab, "Output")
    tabs.addTab(self.updates_tab, "Updates")
    layout = QVBoxLayout(self)
    layout.addWidget(tabs)
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
