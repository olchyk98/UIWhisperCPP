"""App-wide look: quiet, native-feeling, light and dark aware.

Surface colors are derived from the system palette at runtime (and rebuilt when
macOS switches between light and dark), so the app always matches the OS. Only
spacing, radii and a few accents are fixed here.
"""
from PySide6.QtCore import QEvent, QObject, QTimer
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

ACCENT = "#3b82f6"
SUCCESS = "#22a355"
DANGER = "#e5484d"


def _rgba(color: QColor, alpha: float) -> str:
  return f"rgba({color.red()}, {color.green()}, {color.blue()}, {alpha})"


def is_dark(palette: QPalette) -> bool:
  return palette.color(QPalette.ColorRole.Window).lightness() < 128


def build_stylesheet(palette: QPalette) -> str:
  window = palette.color(QPalette.ColorRole.Window)
  text = palette.color(QPalette.ColorRole.WindowText)
  dark = is_dark(palette)
  sidebar = window.lighter(112) if dark else window.darker(104)
  return """
QMainWindow, QWidget#Pane { background: %(window)s; }
QWidget#Sidebar { background: %(sidebar)s; }
QSplitter::handle { background: %(line)s; }
QLabel#PaneTitle {
  font-size: 11px; font-weight: 600; letter-spacing: 0.6px; color: %(muted)s;
}
QLabel#FileTitle { font-size: 16px; font-weight: 600; }
QLabel#FileStatus { color: %(muted)s; }
QListView#FileQueue { background: transparent; border: none; outline: none; }
QPlainTextEdit#Transcript {
  border: none; background: transparent; color: %(text)s;
  selection-background-color: %(accent_soft)s;
}
QWidget#BottomBar { background: %(window)s; border-top: 1px solid %(line)s; }
QPushButton#Flat, QToolButton#Flat {
  border: none; padding: 4px 9px; border-radius: 6px; background: transparent;
  color: %(text)s;
}
QPushButton#Flat:hover, QToolButton#Flat:hover { background: %(hover)s; }
QPushButton#Flat:disabled { color: %(muted)s; }
QPushButton#Add {
  border: 1px solid %(line_strong)s; border-radius: 7px; padding: 5px 10px;
  background: %(hover)s; color: %(text)s;
}
QPushButton#Add:hover { background: %(hover_strong)s; }
QPushButton#Add:pressed { background: %(line_strong)s; }
""" % {
    "window": window.name(),
    "sidebar": sidebar.name(),
    "text": text.name(),
    "muted": _rgba(text, 0.5),
    "line": _rgba(text, 0.10),
    "line_strong": _rgba(text, 0.16),
    "hover": _rgba(text, 0.06),
    "hover_strong": _rgba(text, 0.11),
    "accent_soft": _rgba(QColor(ACCENT), 0.35),
  }


class _ThemeFollower(QObject):
  """Rebuilds the stylesheet whenever the system palette really changes.

  macOS announces a light/dark flip before Qt has swapped the palette, so the
  rebuild is deferred to the next event-loop turn and re-checked on every
  application palette change; it only re-applies when the colors differ.
  """

  def __init__(self, app: QApplication) -> None:
    super().__init__(app)
    self._app = app
    self._applied_for: str | None = None
    self._timer = QTimer(self)
    self._timer.setSingleShot(True)
    self._timer.setInterval(0)
    self._timer.timeout.connect(self.refresh)
    app.installEventFilter(self)
    app.styleHints().colorSchemeChanged.connect(lambda _scheme: self._schedule())

  def _schedule(self) -> None:
    self._timer.start()
    # A second pass catches palettes that settle a moment after the signal.
    QTimer.singleShot(150, self.refresh)

  def eventFilter(self, watched: QObject, event: QEvent) -> bool:
    if watched is self._app and event.type() == QEvent.Type.ApplicationPaletteChange:
      self._schedule()
    return False

  def refresh(self) -> None:
    palette = self._app.palette()
    signature = (
      palette.color(QPalette.ColorRole.Window).name()
      + palette.color(QPalette.ColorRole.WindowText).name()
    )
    if signature == self._applied_for:
      return
    self._applied_for = signature
    self._app.setStyleSheet(build_stylesheet(palette))


def apply_theme(app: QApplication) -> None:
  follower = _ThemeFollower(app)
  follower.refresh()


def secondary_text(palette: QPalette) -> QColor:
  color = QColor(palette.color(QPalette.ColorRole.WindowText))
  color.setAlphaF(0.5)
  return color


def selection_fill(selected: bool) -> QColor:
  color = QColor(ACCENT)
  color.setAlphaF(0.26 if selected else 0.09)
  return color

