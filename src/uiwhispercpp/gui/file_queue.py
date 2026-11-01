"""Left pane: the list of files being transcribed, one compact row per file."""
from dataclasses import dataclass, field
import os

from PySide6.QtCore import QModelIndex, QRect, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPalette
from PySide6.QtWidgets import (
  QListWidget,
  QListWidgetItem,
  QStyle,
  QStyledItemDelegate,
  QStyleOptionViewItem,
)

from uiwhispercpp.gui.theme import ACCENT, DANGER, SUCCESS, secondary_text, selection_fill
from uiwhispercpp.transcript import Line, OutputOptions

QUEUED = "queued"
RUNNING = "running"
DONE = "done"
FAILED = "failed"

INDETERMINATE = -1

_JOB_ROLE = Qt.ItemDataRole.UserRole + 1
_ROW_HEIGHT = 50


@dataclass
class Job:
  id: int
  path: str
  state: str = QUEUED
  status: str = "Waiting"
  progress: int = INDETERMINATE
  lines: list[Line] = field(default_factory=list)
  output_path: str | None = None
  # Options the saved file was written with (None until saved).
  saved_options: OutputOptions | None = None
  error: str | None = None

  @property
  def name(self) -> str:
    return os.path.basename(self.path)


class _JobDelegate(QStyledItemDelegate):
  def __init__(self, queue: "FileQueue") -> None:
    super().__init__(queue)
    self._queue = queue

  def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:
    return QSize(option.rect.width(), _ROW_HEIGHT)

  def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
    job: Job | None = index.data(_JOB_ROLE)
    if job is None:
      return
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    palette = option.palette
    rect = option.rect.adjusted(6, 2, -6, -2)

    selected = bool(option.state & QStyle.StateFlag.State_Selected)
    hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
    if selected or hovered:
      painter.setPen(Qt.PenStyle.NoPen)
      painter.setBrush(selection_fill(selected))
      painter.drawRoundedRect(rect, 7, 7)

    inner = rect.adjusted(10, 7, -10, -7)
    name_font = QFont(option.font)
    name_font.setPointSizeF(option.font.pointSizeF() + 0.5)
    name_font.setWeight(QFont.Weight.Medium)
    painter.setFont(name_font)
    painter.setPen(palette.color(QPalette.ColorRole.WindowText))
    name_rect = QRect(inner.left(), inner.top(), inner.width(), inner.height() // 2)
    elided = painter.fontMetrics().elidedText(
      job.name, Qt.TextElideMode.ElideMiddle, name_rect.width()
    )
    painter.drawText(name_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, elided)

    status_font = QFont(option.font)
    status_font.setPointSizeF(max(9.0, option.font.pointSizeF() - 1.5))
    painter.setFont(status_font)
    status_color = {
      DONE: QColor(SUCCESS),
      FAILED: QColor(DANGER),
    }.get(job.state, secondary_text(palette))
    painter.setPen(status_color)
    status_rect = QRect(
      inner.left(), inner.top() + inner.height() // 2, inner.width(), inner.height() // 2
    )
    status = job.status
    if job.state == RUNNING and job.progress != INDETERMINATE:
      status = f"{status}  {job.progress}%"
    painter.drawText(
      status_rect,
      Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
      painter.fontMetrics().elidedText(status, Qt.TextElideMode.ElideRight, status_rect.width()),
    )

    if job.state == RUNNING:
      track = QRect(rect.left() + 10, rect.bottom() - 4, rect.width() - 20, 3)
      track_color = secondary_text(palette)
      track_color.setAlphaF(0.18)
      painter.setPen(Qt.PenStyle.NoPen)
      painter.setBrush(track_color)
      painter.drawRoundedRect(track, 1.5, 1.5)
      painter.setBrush(QColor(ACCENT))
      if job.progress == INDETERMINATE:
        width = max(24, track.width() // 4)
        span = track.width() + width
        x = track.left() - width + (self._queue.animation_phase * 6) % span
        bar = QRect(int(x), track.top(), width, track.height()).intersected(track)
      else:
        bar = QRect(track.left(), track.top(), track.width() * job.progress // 100, track.height())
      if bar.width() > 0:
        painter.drawRoundedRect(bar, 1.5, 1.5)
    painter.restore()


class FileQueue(QListWidget):
  """Shows jobs; the window owns the Job objects and calls `refresh` on change."""

  def __init__(self) -> None:
    super().__init__()
    self.setObjectName("FileQueue")
    self.setItemDelegate(_JobDelegate(self))
    self.setMouseTracking(True)
    self.setUniformItemSizes(True)
    self.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
    self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    self.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
    self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
    self.animation_phase = 0
    # Animates indeterminate bars; runs only while a job is indeterminate.
    self._timer = QTimer(self)
    self._timer.setInterval(40)
    self._timer.timeout.connect(self._tick)
    self._items: dict[int, QListWidgetItem] = {}

  def add_job(self, job: Job) -> None:
    item = QListWidgetItem()
    item.setData(_JOB_ROLE, job)
    item.setToolTip(job.path)
    self.addItem(item)
    self._items[job.id] = item
    self.viewport().update()

  def remove_job(self, job_id: int) -> None:
    item = self._items.pop(job_id, None)
    if item is not None:
      self.takeItem(self.row(item))
    self.viewport().update()

  def job_at(self, row: int) -> Job | None:
    item = self.item(row)
    return item.data(_JOB_ROLE) if item else None

  def selected_job(self) -> Job | None:
    items = self.selectedItems()
    return items[0].data(_JOB_ROLE) if items else None

  def select_job(self, job_id: int) -> None:
    item = self._items.get(job_id)
    if item is not None:
      self.setCurrentItem(item)
      self.scrollToItem(item)

  def refresh(self, job: Job) -> None:
    item = self._items.get(job.id)
    if item is not None:
      self.update(self.indexFromItem(item))
    animating = any(
      j.state == RUNNING and j.progress == INDETERMINATE
      for j in (i.data(_JOB_ROLE) for i in self._items.values())
    )
    if animating and not self._timer.isActive():
      self._timer.start()
    elif not animating and self._timer.isActive():
      self._timer.stop()

  def _tick(self) -> None:
    self.animation_phase += 1
    for item in self._items.values():
      job: Job = item.data(_JOB_ROLE)
      if job.state == RUNNING:
        self.update(self.indexFromItem(item))

  def paintEvent(self, event) -> None:
    super().paintEvent(event)
    if self.count():
      return
    painter = QPainter(self.viewport())
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    rect = self.viewport().rect().adjusted(12, 12, -12, -12)
    pen_color = secondary_text(self.palette())
    pen_color.setAlphaF(0.28)
    pen = painter.pen()
    pen.setColor(pen_color)
    pen.setStyle(Qt.PenStyle.DashLine)
    painter.setPen(pen)
    painter.drawRoundedRect(rect, 10, 10)
    painter.setPen(secondary_text(self.palette()))
    painter.drawText(
      rect,
      Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
      "Drop audio or video files here\n\nWAV, MP3, M4A, WMA, FLAC, OGG, AAC, MP4 and more",
    )
