"""Signal aliases (persistence 54.1/54.2): app-wide names for file columns.

An alias dictionary maps `alias -> [candidate source columns, in priority order]`. Opening a
file attaches each alias to the first candidate the file has. Pure functions only (no Qt, no
I/O); persistence lives in `io/signal_aliases.py`, the wiring in `ui/main_window.py`.
"""
from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping, Sequence


class AliasProblem(ValueError):
    """An alias entry that cannot be stored. `key`/`params` are an i18n key and its format
    arguments so the UI translates it (core/ never imports ui/); `str(e)` is plain English."""

    def __init__(self, key: str, **params: object) -> None:
        self.key = key
        self.params = params
        super().__init__(f"{key} {params}" if params else key)


def clean_entry(alias: object, sources: object) -> tuple[str, list[str]]:
    """Normalize one entry (strip names) or raise `AliasProblem`. Duplicates in `sources` are
    an error, not silently merged: the priority order is the user's statement."""
    if not isinstance(alias, str) or not alias.strip():
        raise AliasProblem("alias.err_empty_name")
    alias = alias.strip()
    if not isinstance(sources, (list, tuple)) or not sources:
        raise AliasProblem("alias.err_no_sources", alias=alias)
    cleaned: list[str] = []
    for s in sources:
        if not isinstance(s, str) or not s.strip():
            raise AliasProblem("alias.err_empty_source", alias=alias)
        s = s.strip()
        if s in cleaned:
            raise AliasProblem("alias.err_duplicate_source", alias=alias, source=s)
        cleaned.append(s)
    return alias, cleaned


def validate_entry(
    alias: object, sources: object, *, reserved: Collection[str] = (), others: Collection[str] = ()
) -> tuple[str, list[str]]:
    """`clean_entry` plus the dictionary-level rules (54.1): not a function/map name
    (`reserved`), not already used by another entry (`others`)."""
    alias, cleaned = clean_entry(alias, sources)
    if alias in reserved:
        raise AliasProblem("alias.err_reserved", alias=alias)
    if alias in others:
        raise AliasProblem("alias.err_duplicate_name", alias=alias)
    return alias, cleaned


def resolve_aliases(columns: Iterable[str], aliases: Mapping[str, Sequence[str]]) -> dict[str, str]:
    """`{alias: source column}` for a file with these `columns` (54.2).

    First candidate present in `columns` wins; an alias with no present candidate is not
    attached; an alias equal to a real column name is not attached (the file's column wins);
    aliases never chain (candidates are matched against real columns only); several aliases may
    share a source.
    """
    present = set(columns)
    out: dict[str, str] = {}
    for alias, sources in aliases.items():
        if alias in present:
            continue
        for s in sources:
            if s in present:
                out[alias] = s
                break
    return out


def aliases_by_source(alias_sources: Mapping[str, str]) -> dict[str, list[str]]:
    """Inverse of `resolve_aliases`' result: `{source: [aliases sorted by name]}`."""
    out: dict[str, list[str]] = {}
    for alias in sorted(alias_sources):
        out.setdefault(alias_sources[alias], []).append(alias)
    return out


def display_name(source: str, by_source: Mapping[str, Sequence[str]]) -> str:
    """The name shown for `source`: its first alias (by name), else the source itself."""
    names = by_source.get(source)
    return names[0] if names else source


def visible_names(columns: Sequence[str], by_source: Mapping[str, Sequence[str]]) -> list[str]:
    """Names to list for a file with these `columns` (54.2): an aliased column is replaced, at its
    position, by its aliases (name order); the source name disappears from the list."""
    out: list[str] = []
    for c in columns:
        out.extend(by_source.get(c) or [c])
    return out
