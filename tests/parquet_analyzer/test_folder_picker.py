"""ui/folder_picker.pick_parquet_file (specification.md 5.9). QFileDialog.exec is
monkeypatched so no modal dialog ever opens."""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QFileDialog

from parquet_analyzer.ui import folder_picker
from parquet_analyzer.ui.i18n import tr


class _Recorder:
    """Patches QFileDialog.exec and records the dialog's state at that moment."""

    def __init__(self, monkeypatch, result, selected):
        self.snapshot: dict = {}
        outer = self

        def fake_exec(dialog):
            outer.snapshot = {
                "title": dialog.windowTitle(),
                "mode": dialog.fileMode(),
                "filter": dialog.nameFilters(),
                "directory": dialog.directory().absolutePath(),
                "sidebar": [u.toLocalFile() for u in dialog.sidebarUrls()],
            }
            return result

        monkeypatch.setattr(QFileDialog, "exec", fake_exec)
        monkeypatch.setattr(QFileDialog, "selectedFiles", lambda dialog: selected)


def test_accepted_returns_first_selected_file(qapp, monkeypatch, tmp_path):
    rec = _Recorder(monkeypatch, QFileDialog.DialogCode.Accepted, [str(tmp_path / "a.parquet"), "other"])
    assert folder_picker.pick_parquet_file(None, None, []) == str(tmp_path / "a.parquet")
    assert rec.snapshot["title"] == tr("folder.open_dialog_title")
    assert rec.snapshot["mode"] == QFileDialog.FileMode.ExistingFile
    assert rec.snapshot["filter"] == ["Parquet files (*.parquet)"]


def test_cancelled_returns_none(qapp, monkeypatch):
    _Recorder(monkeypatch, QFileDialog.DialogCode.Rejected, ["ignored.parquet"])
    assert folder_picker.pick_parquet_file(None, None, []) is None


def test_accepted_with_empty_selection_returns_none(qapp, monkeypatch):
    _Recorder(monkeypatch, QFileDialog.DialogCode.Accepted, [])
    assert folder_picker.pick_parquet_file(None, None, []) is None


def test_start_dir_is_used_when_it_exists(qapp, monkeypatch, tmp_path):
    rec = _Recorder(monkeypatch, QFileDialog.DialogCode.Rejected, [])
    folder_picker.pick_parquet_file(None, str(tmp_path), [])
    assert rec.snapshot["directory"] == tmp_path.resolve().as_posix() or rec.snapshot["directory"] == tmp_path.as_posix()


@pytest.mark.parametrize("start", [None, "", "/definitely/not/a/folder"])
def test_missing_or_empty_start_dir_is_ignored(qapp, monkeypatch, tmp_path, start):
    rec = _Recorder(monkeypatch, QFileDialog.DialogCode.Rejected, [])
    folder_picker.pick_parquet_file(None, start, [])  # must not raise
    assert "/definitely/not/a/folder" not in rec.snapshot["directory"]


def test_recent_folders_are_added_to_sidebar_skipping_vanished_ones(qapp, monkeypatch, tmp_path):
    good = tmp_path / "good"
    good.mkdir()
    also = tmp_path / "also"
    also.mkdir()
    rec = _Recorder(monkeypatch, QFileDialog.DialogCode.Rejected, [])
    folder_picker.pick_parquet_file(None, None, [str(good), str(tmp_path / "gone"), str(also)])
    sidebar = [p.rstrip("/") for p in rec.snapshot["sidebar"]]
    assert str(good) in sidebar or good.resolve().as_posix() in sidebar
    assert str(also) in sidebar or also.resolve().as_posix() in sidebar
    assert not any(p.endswith("/gone") for p in sidebar)
