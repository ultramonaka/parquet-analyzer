"""Chapter 49 (persistence 5.42 / 49): saving a view lets you pick an existing view from a
list, and an existing name is always confirmed before it is overwritten."""

from __future__ import annotations

import json
import os
import time
from datetime import date

import pytest
from PySide6.QtWidgets import QApplication, QInputDialog, QMessageBox

from parquet_analyzer import config
from parquet_analyzer.core import oplog
from parquet_analyzer.io import settings as settings_io
from parquet_analyzer.io import view as view_io
from parquet_analyzer.ui import i18n
from parquet_analyzer.ui.main_window import MainWindow


class Answers:
    """Records every dialog the save path opens and answers them from a script."""

    def __init__(self, monkeypatch):
        self.name_answer: tuple[str, bool] = ("", False)
        self.confirm_answer = QMessageBox.StandardButton.Yes
        self.item_calls: list[tuple] = []
        self.question_texts: list[str] = []
        self.boxes: list[str] = []
        monkeypatch.setattr(QInputDialog, "getItem", staticmethod(self._get_item))
        monkeypatch.setattr(QMessageBox, "question", staticmethod(self._question))
        for kind in ("information", "warning", "critical"):
            monkeypatch.setattr(QMessageBox, kind, staticmethod(lambda *a, _k=kind, **k: self.boxes.append(_k)))

    def _get_item(self, *args, **kwargs):
        self.item_calls.append((args, kwargs))
        return self.name_answer

    def _question(self, *args, **kwargs):
        self.question_texts.append(args[2])
        return self.confirm_answer


@pytest.fixture
def answers(qapp, isolated_config, monkeypatch):
    return Answers(monkeypatch)


@pytest.fixture
def win(qapp, isolated_config, sample_parquet_path, answers):
    w = MainWindow()
    w.resize(800, 600)
    assert w.load_parquet(str(sample_parquet_path))
    _settle(w)
    yield w
    w.close()


def _settle(win, timeout=10.0):
    end = time.monotonic() + timeout
    while win._busy_sources and time.monotonic() < end:
        QApplication.processEvents()
        time.sleep(0.01)
    QApplication.processEvents()


def _save(win, answers, name):
    answers.name_answer = (name, True)
    answers.question_texts.clear()
    win.save_view_dialog()


def _last_items_call(answers):
    args, kwargs = answers.item_calls[-1]
    return args, kwargs


def _log_records(kind):
    oplog.flush()
    path = oplog.log_path_for(date.today())
    out = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return [r for r in out if r["kind"] == kind]


@pytest.fixture
def log(qapp, isolated_config):
    oplog.reset()
    oplog.configure(config.PARQUET_ANALYZER_LOG_DIR, debug=False, app_version="test")
    yield
    oplog.reset()


# -- 49.1 the name list ------------------------------------------------------------------


def test_get_item_lists_saved_views_with_empty_entry_selected_when_no_current_view(win, answers):
    for name in ("beta", "alpha"):
        _save(win, answers, name)
    win._view_name = None
    answers.item_calls.clear()
    answers.name_answer = ("", False)
    win.save_view_dialog()
    args, kwargs = _last_items_call(answers)
    assert args[1] == i18n.tr("toolbar.save_view_title")
    assert args[2] == i18n.tr("view.name_label")
    assert args[3] == ["", "alpha", "beta"]
    assert args[4] == 0
    assert kwargs.get("editable") is True


def test_get_item_selects_current_view_when_it_is_saved(win, answers):
    for name in ("alpha", "beta", "gamma"):
        _save(win, answers, name)
    assert win._view_name == "gamma"
    answers.item_calls.clear()
    answers.name_answer = ("", False)
    win.save_view_dialog()
    args, _ = _last_items_call(answers)
    assert args[3] == ["alpha", "beta", "gamma"]
    assert args[4] == 2


def test_get_item_prepends_empty_entry_when_current_view_was_deleted(win, answers):
    _save(win, answers, "alpha")
    _save(win, answers, "beta")
    view_io.delete_view("beta")  # the current view's file is gone
    answers.item_calls.clear()
    answers.name_answer = ("", False)
    win.save_view_dialog()
    args, _ = _last_items_call(answers)
    assert args[3] == ["", "alpha"]
    assert args[4] == 0


# -- 49.2 saving and the overwrite confirm -----------------------------------------------


def test_new_name_saves_without_confirm(win, answers):
    _save(win, answers, "fresh")
    assert view_io.list_views() == ["fresh"]
    assert answers.question_texts == []
    assert win._view_name == "fresh"


def test_existing_other_name_confirm_yes_overwrites(win, answers):
    _save(win, answers, "keep")
    _save(win, answers, "other")
    win._add_variable_to_plot(win.plot_grid.plots[0], "a")
    answers.name_answer = ("keep", True)
    answers.confirm_answer = QMessageBox.StandardButton.Yes
    before = view_io.view_path("keep").read_text(encoding="utf-8")
    answers.boxes.clear()
    win.save_view_dialog()
    assert answers.question_texts == [i18n.tr("view.overwrite_confirm", name="keep")]
    after = view_io.view_path("keep").read_text(encoding="utf-8")
    assert after != before
    assert "a" in [s.variable for p in view_io.load_view("keep").plots for s in p.series]
    assert answers.boxes == ["information"]  # the saved notice, no warning
    assert win._view_name == "keep"


def test_existing_other_name_confirm_no_leaves_file_untouched(win, answers):
    _save(win, answers, "keep")
    _save(win, answers, "other")
    win._add_variable_to_plot(win.plot_grid.plots[0], "a")
    path = view_io.view_path("keep")
    before_text = path.read_text(encoding="utf-8")
    before_stat = os.stat(path)
    time.sleep(0.02)  # make an mtime change visible on coarse file systems
    answers.name_answer = ("keep", True)
    answers.confirm_answer = QMessageBox.StandardButton.No
    answers.boxes.clear()
    win.save_view_dialog()
    assert answers.question_texts == [i18n.tr("view.overwrite_confirm", name="keep")]
    assert path.read_text(encoding="utf-8") == before_text
    assert os.stat(path).st_mtime_ns == before_stat.st_mtime_ns
    assert answers.boxes == []
    assert win._view_name == "other"  # a refused save does not change the current view


def test_same_name_as_current_view_still_confirms(win, answers):
    _save(win, answers, "cur")
    assert win._view_name == "cur"
    before = view_io.view_path("cur").read_text(encoding="utf-8")
    answers.confirm_answer = QMessageBox.StandardButton.No
    _save(win, answers, "cur")
    assert answers.question_texts == [i18n.tr("view.overwrite_confirm", name="cur")]
    assert view_io.view_path("cur").read_text(encoding="utf-8") == before
    answers.confirm_answer = QMessageBox.StandardButton.Yes
    _save(win, answers, "cur")
    assert len(answers.question_texts) == 1


@pytest.mark.parametrize("name", ["", "   ", "\t"])
def test_blank_name_does_nothing(win, answers, name):
    _save(win, answers, name)
    assert view_io.list_views() == []
    assert answers.question_texts == [] and answers.boxes == []
    assert win._view_name is None


def test_cancel_does_nothing_even_with_a_name_typed(win, answers):
    answers.name_answer = ("cancelled", False)
    answers.boxes.clear()
    win.save_view_dialog()
    assert view_io.list_views() == []
    assert answers.question_texts == [] and answers.boxes == []


def test_name_is_stripped_before_the_existence_check(win, answers):
    _save(win, answers, "padded")
    _save(win, answers, "  padded  ")
    assert view_io.list_views() == ["padded"]
    assert answers.question_texts == [i18n.tr("view.overwrite_confirm", name="padded")]


def test_log_records_overwrite_flag(win, answers, log):
    _save(win, answers, "one")
    _save(win, answers, "one")  # confirm is Yes by default
    records = [r for r in _log_records("op") if r["action"] == "save_view"]
    assert [r["details"]["overwrite"] for r in records] == [False, True]


def test_titles_of_save_dialogs_use_the_new_key(win, answers, monkeypatch):
    titles = []
    monkeypatch.setattr(QInputDialog, "getItem", staticmethod(lambda *a, **k: titles.append(a[1]) or ("t", True)))
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: titles.append(a[1])))
    monkeypatch.setattr(
        QMessageBox, "question", staticmethod(lambda *a, **k: titles.append(a[1]) or QMessageBox.StandardButton.Yes)
    )
    win.save_view_dialog()  # new name: getItem title, then the information title
    win.save_view_dialog()  # existing name: getItem, question, information
    assert titles and all(t == i18n.tr("toolbar.save_view_title") for t in titles)
    assert len(titles) == 5


# -- strings ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "lang, title, confirm",
    [
        ("en", "Save view", '"keep" already exists. Overwrite it?'),
        ("ja", "ビューの保存", "「keep」は既にあります。上書きしますか？"),
    ],
)
def test_new_strings_translated(qapp, isolated_config, lang, title, confirm):
    prev = i18n.get_language()
    try:
        st = settings_io.load_settings()
        st.language = lang
        settings_io.save_settings(st)
        i18n.set_language(lang)
        assert i18n.tr("toolbar.save_view_title") == title
        assert i18n.tr("view.overwrite_confirm", name="keep") == confirm
    finally:
        i18n.set_language(prev)
