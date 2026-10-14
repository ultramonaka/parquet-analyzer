"""`QTableWidget` whose first row and first column stay visible while scrolling
(detailed_specification.md 27.11).

Built like Qt's "Frozen Column" example: three `QTableView` overlays (top row, left column,
corner) sit on the table's viewport and share its model *and* selection model, so editing and
selecting in an overlay is editing / selecting in the table (`itemChanged` fires as usual).
The overlays have no scrollbars, headers or frame; the top one follows the table's horizontal
scroll, the left one its vertical scroll. `set_frozen(False)` hides them (curves).
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QModelIndex, Qt
from PySide6.QtWidgets import QAbstractItemView, QTableView, QTableWidget


class _Overlay(QTableView):
    """A frozen part. It never scrolls by itself: everything is delegated to the owner."""

    def __init__(self, owner: FrozenAxesTable) -> None:
        super().__init__(owner)  # not the viewport: its children move when it scrolls
        self._owner = owner
        self.setModel(owner.model())
        self.setSelectionModel(owner.selectionModel())
        self.setFrameShape(QTableView.Shape.NoFrame)
        self.horizontalHeader().hide()
        self.verticalHeader().hide()
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setEditTriggers(owner.editTriggers())
        self.setSelectionMode(owner.selectionMode())
        self.setSelectionBehavior(owner.selectionBehavior())
        self.hide()

    def scrollTo(self, index, hint=QAbstractItemView.ScrollHint.EnsureVisible) -> None:  # noqa: N802
        self._owner.scrollTo(index, hint)

    def wheelEvent(self, event) -> None:  # noqa: N802
        self._owner.wheelEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        self._owner.keyPressEvent(event)


class FrozenAxesTable(QTableWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._frozen = False
        self.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self._left = _Overlay(self)
        self._top = _Overlay(self)
        self._corner = _Overlay(self)
        self.viewport().installEventFilter(self)
        self.horizontalHeader().sectionResized.connect(self._on_column_resized)
        self.verticalHeader().sectionResized.connect(self._on_row_resized)
        m = self.model()
        for sig in (m.rowsInserted, m.rowsRemoved, m.columnsInserted, m.columnsRemoved, m.modelReset, m.layoutChanged):
            sig.connect(self._sync_all)
        self.horizontalScrollBar().valueChanged.connect(self._sync_scroll)
        self.verticalScrollBar().valueChanged.connect(self._sync_scroll)

    # -- public --------------------------------------------------------------------------

    def set_frozen(self, frozen: bool) -> None:
        self._frozen = bool(frozen)
        self._sync_all()

    @property
    def is_frozen(self) -> bool:
        return self._frozen

    def overlays(self) -> tuple[QTableView, QTableView, QTableView]:
        """(top row, left column, corner)."""
        return self._top, self._left, self._corner

    def frozen_size(self) -> tuple[int, int]:
        """(width of the frozen column, height of the frozen row); (0, 0) when not frozen."""
        if not self._active():
            return 0, 0
        return self.columnWidth(0), self.rowHeight(0)

    # -- geometry / sync -----------------------------------------------------------------

    def _active(self) -> bool:
        return self._frozen and self.rowCount() > 0 and self.columnCount() > 0

    def _sync_all(self, *_) -> None:
        active = self._active()
        for ov in (self._left, self._top, self._corner):
            ov.setVisible(active)
        if not active:
            return
        nr, nc = self.rowCount(), self.columnCount()
        for c in range(nc):
            self._top.setColumnWidth(c, self.columnWidth(c))
        for r in range(nr):
            self._left.setRowHeight(r, self.rowHeight(r))
        self._left.setColumnWidth(0, self.columnWidth(0))
        self._top.setRowHeight(0, self.rowHeight(0))
        self._corner.setColumnWidth(0, self.columnWidth(0))
        self._corner.setRowHeight(0, self.rowHeight(0))
        for r in range(nr):
            self._top.setRowHidden(r, r != 0)
            self._corner.setRowHidden(r, r != 0)
        for c in range(nc):
            self._left.setColumnHidden(c, c != 0)
            self._corner.setColumnHidden(c, c != 0)
        self._update_geometry()

    def _update_geometry(self) -> None:
        if not self._active():
            return
        vp = self.viewport().geometry()  # in the table's coordinates (frame, Qt headers)
        w0, h0 = self.columnWidth(0), self.rowHeight(0)
        self._left.setGeometry(vp.x(), vp.y(), w0, vp.height())
        self._top.setGeometry(vp.x(), vp.y(), vp.width(), h0)
        self._corner.setGeometry(vp.x(), vp.y(), w0, h0)
        for ov in (self._left, self._top, self._corner):  # corner on top
            ov.raise_()
        self._sync_scroll()

    def _sync_scroll(self, *_) -> None:
        if not self._active():
            return
        self._top.horizontalScrollBar().setValue(self.horizontalScrollBar().value())
        self._top.verticalScrollBar().setValue(0)
        self._left.verticalScrollBar().setValue(self.verticalScrollBar().value())
        self._left.horizontalScrollBar().setValue(0)

    def _on_column_resized(self, column: int, _old: int, new: int) -> None:
        if not self._active():
            return
        self._top.setColumnWidth(column, new)
        if column == 0:
            self._left.setColumnWidth(0, new)
            self._corner.setColumnWidth(0, new)
        self._update_geometry()

    def _on_row_resized(self, row: int, _old: int, new: int) -> None:
        if not self._active():
            return
        self._left.setRowHeight(row, new)
        if row == 0:
            self._top.setRowHeight(0, new)
            self._corner.setRowHeight(0, new)
        self._update_geometry()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._update_geometry()

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if obj is self.viewport() and event.type() in (QEvent.Type.Resize, QEvent.Type.Move):
            self._update_geometry()
        return super().eventFilter(obj, event)

    # -- keep the current cell out from under the frozen part -----------------------------

    def scrollTo(self, index: QModelIndex, hint=QAbstractItemView.ScrollHint.EnsureVisible) -> None:  # noqa: N802
        if not self._active():
            super().scrollTo(index, hint)
            return
        hbar, vbar = self.horizontalScrollBar(), self.verticalScrollBar()
        h0, v0 = hbar.value(), vbar.value()
        super().scrollTo(index, hint)
        # axis cells are always visible through the overlays: don't scroll for them
        if index.column() == 0:
            hbar.setValue(h0)
        if index.row() == 0:
            vbar.setValue(v0)
        rect = self.visualRect(index)
        w0, hh0 = self.columnWidth(0), self.rowHeight(0)
        if index.column() >= 1 and rect.left() < w0:
            hbar.setValue(hbar.value() - (w0 - rect.left()))
        if index.row() >= 1 and rect.top() < hh0:
            vbar.setValue(vbar.value() - (hh0 - rect.top()))

    def edit(self, index, trigger=None, event=None):  # noqa: ANN001
        if trigger is None:
            return super().edit(index)
        # an axis cell is edited in its overlay so the editor sits where the cell is seen
        if self._active() and trigger != QAbstractItemView.EditTrigger.NoEditTriggers:
            target = self._top if index.row() == 0 else self._left if index.column() == 0 else None
            if target is not None and index.flags() & Qt.ItemFlag.ItemIsEditable:
                return target.edit(index, trigger, event)
        return super().edit(index, trigger, event)
