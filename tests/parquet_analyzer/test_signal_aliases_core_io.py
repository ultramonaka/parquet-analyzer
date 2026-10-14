"""Signal aliases, stage 1 (persistence 54.1/54.2/54.7): pure resolution + the dictionary file."""

from __future__ import annotations

import json

import pytest
from hypothesis import given
from hypothesis import strategies as st

from parquet_analyzer import config
from parquet_analyzer.core.signal_aliases import (
    AliasProblem,
    aliases_by_source,
    display_name,
    resolve_aliases,
    validate_entry,
    visible_names,
)
from parquet_analyzer.io import signal_aliases as alias_io

# -- resolve_aliases ------------------------------------------------------------------------


def test_priority_order_first_present_candidate_wins():
    aliases = {"rpm": ["EngSpd", "NE_rpm"]}
    assert resolve_aliases(["t", "NE_rpm", "EngSpd"], aliases) == {"rpm": "EngSpd"}
    assert resolve_aliases(["t", "NE_rpm"], aliases) == {"rpm": "NE_rpm"}


def test_no_candidate_present_means_no_alias():
    assert resolve_aliases(["t", "x"], {"rpm": ["EngSpd"]}) == {}


def test_alias_equal_to_a_real_column_is_not_attached():
    assert resolve_aliases(["t", "rpm", "EngSpd"], {"rpm": ["EngSpd"]}) == {}


def test_several_aliases_may_share_one_column():
    res = resolve_aliases(["t", "EngSpd"], {"a": ["EngSpd"], "b": ["EngSpd"]})
    assert res == {"a": "EngSpd", "b": "EngSpd"}


def test_aliases_do_not_chain():
    # B is only an alias, not a column: A's candidate "B" finds nothing
    assert resolve_aliases(["t", "x"], {"A": ["B"], "B": ["x"]}) == {"B": "x"}


def test_case_sensitive():
    assert resolve_aliases(["t", "engspd"], {"a": ["EngSpd"]}) == {}


def test_visible_names_and_display_name():
    by_source = aliases_by_source({"z_alias": "x", "a_alias": "x", "q": "y"})
    assert by_source == {"x": ["a_alias", "z_alias"], "y": ["q"]}
    assert visible_names(["t", "x", "y", "w"], by_source) == ["t", "a_alias", "z_alias", "q", "w"]
    assert display_name("x", by_source) == "a_alias"
    assert display_name("w", by_source) == "w"


_names = st.text(alphabet="abcdeAB_", min_size=1, max_size=3)


@given(
    columns=st.lists(_names, unique=True, max_size=6),
    aliases=st.dictionaries(_names, st.lists(_names, min_size=1, max_size=3, unique=True), max_size=6),
)
def test_property_sources_are_columns_and_aliases_are_not(columns, aliases):
    res = resolve_aliases(columns, aliases)
    for alias, source in res.items():
        assert source in columns
        assert alias not in columns
        assert source in aliases[alias]
        first = next(s for s in aliases[alias] if s in columns)
        assert source == first


# -- validation -----------------------------------------------------------------------------


def test_validate_entry_strips_and_returns():
    assert validate_entry("  a ", [" x ", "y"]) == ("a", ["x", "y"])


@pytest.mark.parametrize(
    "alias, sources, key",
    [
        ("", ["x"], "alias.err_empty_name"),
        ("  ", ["x"], "alias.err_empty_name"),
        (3, ["x"], "alias.err_empty_name"),
        ("a", [], "alias.err_no_sources"),
        ("a", "x", "alias.err_no_sources"),
        ("a", ["x", " "], "alias.err_empty_source"),
        ("a", ["x", "x"], "alias.err_duplicate_source"),
    ],
    ids=["empty", "blank", "nonstr", "nosources", "strsources", "emptysource", "dupsource"],
)
def test_validate_entry_rejects(alias, sources, key):
    with pytest.raises(AliasProblem) as ei:
        validate_entry(alias, sources)
    assert ei.value.key == key


def test_validate_entry_reserved_and_duplicate_names():
    with pytest.raises(AliasProblem) as ei:
        validate_entry("sin", ["x"], reserved={"sin"})
    assert ei.value.key == "alias.err_reserved"
    with pytest.raises(AliasProblem) as ei:
        validate_entry("a", ["x"], others={"a"})
    assert ei.value.key == "alias.err_duplicate_name"


def test_every_problem_key_has_en_and_ja_translations():
    from parquet_analyzer.ui.i18n import _STRINGS

    for key in (
        "alias.err_empty_name", "alias.err_no_sources", "alias.err_empty_source",
        "alias.err_duplicate_source", "alias.err_duplicate_name", "alias.err_reserved",
    ):
        assert _STRINGS[key]["en"] and _STRINGS[key]["ja"]


# -- the dictionary file --------------------------------------------------------------------


def test_missing_file_is_empty(isolated_config):
    assert alias_io.load_signal_aliases() == ({}, [], [])


def test_roundtrip_keeps_order_and_unknown_keys_are_ignored(isolated_config):
    data = {"a": ["x", "y"], "b": ["z"]}
    alias_io.save_signal_aliases(data)
    path = alias_io.signal_aliases_path()
    assert path.parent == config.PARQUET_ANALYZER_CFG_DIR
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw == {"version": 1, "aliases": [{"alias": "a", "sources": ["x", "y"]}, {"alias": "b", "sources": ["z"]}]}
    raw["future"] = 1
    raw["aliases"][0]["note"] = "ignored"
    path.write_text(json.dumps(raw), encoding="utf-8")
    loaded, warnings, skipped = alias_io.load_signal_aliases()
    assert loaded == data and list(loaded) == ["a", "b"] and warnings == [] and skipped == []


@pytest.mark.parametrize("content", ["{not json", "[]", '{"aliases": 3}', '{"version": 1}'], ids=["syntax", "list", "notlist", "nokey"])
def test_broken_file_is_quarantined_and_empty(isolated_config, content):
    path = alias_io.signal_aliases_path()
    path.parent.mkdir(parents=True)
    path.write_text(content, encoding="utf-8")
    loaded, warnings, skipped = alias_io.load_signal_aliases()
    assert loaded == {} and skipped == []
    assert len(warnings) == 1 and "moved to signal_aliases.json.bad-" in warnings[0]
    assert not path.exists()
    bads = list(path.parent.glob("signal_aliases.json.bad-*"))
    assert len(bads) == 1 and bads[0].read_text(encoding="utf-8") == content
    # next load: no file -> empty, no further warning
    assert alias_io.load_signal_aliases() == ({}, [], [])


def test_bad_items_are_skipped_with_one_warning_each(isolated_config):
    path = alias_io.signal_aliases_path()
    path.parent.mkdir(parents=True)
    items = [
        {"alias": "ok", "sources": ["x"]},
        {"alias": "", "sources": ["x"]},
        {"alias": "nosrc", "sources": []},
        {"alias": "ok", "sources": ["y"]},  # duplicate name
        {"alias": "sin", "sources": ["x"]},  # a function name
        {"alias": "dups", "sources": ["x", "x"]},
        "not an object",
        {"alias": "also_ok", "sources": ["y", "z"]},
    ]
    path.write_text(json.dumps({"version": 1, "aliases": items}), encoding="utf-8")
    loaded, warnings, skipped = alias_io.load_signal_aliases(reserved={"sin"})
    assert loaded == {"ok": ["x"], "also_ok": ["y", "z"]}
    assert len(warnings) == 6 and skipped == items[1:7]
    # saving writes the skipped items back verbatim instead of deleting them
    alias_io.save_signal_aliases(loaded, skipped)
    assert json.loads(path.read_text(encoding="utf-8"))["aliases"][2:] == items[1:7]


def test_unreadable_file_raises_and_is_not_moved(isolated_config, monkeypatch):
    path = alias_io.signal_aliases_path()
    path.parent.mkdir(parents=True)
    path.write_text("{}", encoding="utf-8")
    real = type(path).read_text

    def boom(self, *a, **k):
        if self.name == "signal_aliases.json":
            raise PermissionError("denied")
        return real(self, *a, **k)

    monkeypatch.setattr(type(path), "read_text", boom)
    with pytest.raises(alias_io.SignalAliasesUnreadable):
        alias_io.load_signal_aliases()
    assert path.exists()


def test_failed_write_raises_and_leaves_old_file(isolated_config, monkeypatch):
    alias_io.save_signal_aliases({"a": ["x"]})
    path = alias_io.signal_aliases_path()
    before = path.read_text(encoding="utf-8")

    def boom(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(alias_io.os, "replace", boom)
    with pytest.raises(OSError):
        alias_io.save_signal_aliases({"a": ["x"], "b": ["y"]})
    assert path.read_text(encoding="utf-8") == before
    assert not list(path.parent.glob("*.tmp"))
