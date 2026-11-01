"""Small reusable widgets that keep the layout calm at any window size."""
from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import (
  QComboBox,
  QLabel,
  QSizePolicy,
  QStyle,
  QStyleOptionComboBox,
  QStylePainter,
)

SHORT_TEXT_ROLE = Qt.ItemDataRole.UserRole + 10


class ElidedLabel(QLabel):
  """A one-line label that ends in "..." instead of clipping or pushing layouts.

  It never asks for more width than it gets, so long file names can't widen
  panes; the full text is available as a tooltip.
  """

  def __init__(self, text: str = "", mode: Qt.TextElideMode = Qt.TextElideMode.ElideMiddle) -> None:
    super().__init__(text)
    self._mode = mode
    self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    self.setMinimumWidth(1)

  def setText(self, text: str) -> None:
    super().setText(text)
    self.setToolTip(text if text else "")
    self.update()

  def minimumSizeHint(self) -> QSize:
    return QSize(1, super().minimumSizeHint().height())

  def paintEvent(self, event) -> None:
    painter = QPainter(self)
    painter.setFont(self.font())
    painter.setPen(self.palette().color(self.foregroundRole()))
    rect = self.contentsRect()
    elided = self.fontMetrics().elidedText(self.text(), self._mode, rect.width())
    painter.drawText(rect, int(self.alignment()) | Qt.AlignmentFlag.AlignVCenter, elided)


class CompactComboBox(QComboBox):
  """Shows each item's short name when closed, the full description when open."""

  def paintEvent(self, event) -> None:
    painter = QStylePainter(self)
    option = QStyleOptionComboBox()
    self.initStyleOption(option)
    short = self.currentData(SHORT_TEXT_ROLE)
    if short:
      option.currentText = short
    painter.drawComplexControl(QStyle.ComplexControl.CC_ComboBox, option)
    painter.drawControl(QStyle.ControlElement.CE_ComboBoxLabel, option)

  def sizeHint(self) -> QSize:
    return self._hint(super().sizeHint())

  def minimumSizeHint(self) -> QSize:
    return self._hint(super().minimumSizeHint())

  def _hint(self, base: QSize) -> QSize:
    texts = [self.itemData(i, SHORT_TEXT_ROLE) or self.itemText(i) for i in range(self.count())]
    widest = max((self.fontMetrics().horizontalAdvance(t) for t in texts), default=0)
    option = QStyleOptionComboBox()
    self.initStyleOption(option)
    size = self.style().sizeFromContents(
      QStyle.ContentsType.CT_ComboBox, option, QSize(widest + 8, base.height()), self
    )
    return QSize(size.width(), base.height())
