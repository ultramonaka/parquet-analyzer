from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QFileDialog, QWidget

from .i18n import tr


def pick_parquet_file(parent: QWidget, start_dir: str | None, recent_folders: list[str]) -> str | None:
    """Open a file picker for a Parquet file (specification.md 5.9).

    `start_dir` is the last-used folder (default location); `recent_folders`
    (up to 5, MRU order) are offered as quick-access sidebar entries via the
    native dialog's sidebar URLs.
    """
    dialog = QFileDialog(parent, tr("folder.open_dialog_title"))
    dialog.setFileMode(QFileDialog.FileMode.ExistingFile)
    dialog.setNameFilter("Parquet files (*.parquet)")
    if start_dir and Path(start_dir).is_dir():
        dialog.setDirectory(start_dir)

    from PySide6.QtCore import QUrl

    sidebar_urls = [QUrl.fromLocalFile(f) for f in recent_folders if Path(f).is_dir()]
    if sidebar_urls:
        dialog.setSidebarUrls(dialog.sidebarUrls() + sidebar_urls)

    if dialog.exec() == QFileDialog.DialogCode.Accepted:
        files = dialog.selectedFiles()
        return files[0] if files else None
    return None
