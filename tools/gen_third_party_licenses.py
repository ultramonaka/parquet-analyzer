#!/usr/bin/env python3
"""Generate THIRD_PARTY_LICENSES.md from pyproject.toml and the installed package metadata.

    uv sync --group convert        # the convert group must be installed to be listed with versions
    uv run python tools/gen_third_party_licenses.py > THIRD_PARTY_LICENSES.md
    uv run python tools/gen_third_party_licenses.py --output THIRD_PARTY_LICENSES.md

Dependency set: the transitive closure of ``[project] dependencies`` (runtime) and of the
``convert`` dependency group (minus what runtime already has). The ``dev`` group is skipped.
Environment markers are evaluated for win32, linux and darwin: packages needed on all three
go in the main tables, packages needed on only some platforms go in a separate table.
A dependency that is not installed here (because it is for another platform) is listed,
but its own dependencies cannot be followed.

License identifier, in this order: ``License-Expression`` metadata; a single non-generic
``License ::`` classifier; the license file in the dist-info (text is recognised by a few
standard phrases); the ``License`` field when it is made only of known SPDX ids;
``OVERRIDES`` below; otherwise "unknown (check upstream)". The output is deterministic
(sorted, no dates) for a given set of installed versions.
"""
from __future__ import annotations

import argparse
import importlib.metadata as md
import re
import sys
import tomllib
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parent.parent

# Marker environments to evaluate (sys_platform, platform_system, os_name).
PLATFORMS = {
    "win32": {"sys_platform": "win32", "platform_system": "Windows", "os_name": "nt"},
    "linux": {"sys_platform": "linux", "platform_system": "Linux", "os_name": "posix"},
    "darwin": {"sys_platform": "darwin", "platform_system": "Darwin", "os_name": "posix"},
}

# Hand overrides for packages whose metadata cannot be resolved automatically (keyed by
# canonical name). Applied only when the automatic steps found nothing.
OVERRIDES = {
    # PyOpenGL ships no license file in its dist-info and only the generic "BSD License"
    # classifier; upstream (github.com/mcfletch/pyopengl) licenses it under a 3-clause BSD license.
    "pyopengl": "BSD-3-Clause",
    # tzdata is only installed on Windows (direct dependency), so it is not available here to
    # inspect; its PyPI metadata says Apache-2.0.
    "tzdata": "Apache-2.0",
}

# Single classifiers that map unambiguously to an SPDX id (the generic "BSD License" and
# the PSF classifier are deliberately absent: they do not say which variant).
CLASSIFIER_TO_SPDX = {
    "License :: OSI Approved :: MIT License": "MIT",
    "License :: OSI Approved :: Apache Software License": "Apache-2.0",
    "License :: OSI Approved :: GNU Lesser General Public License v3 or later (LGPLv3+)": "LGPL-3.0-or-later",
    "License :: OSI Approved :: GNU Lesser General Public License v3 (LGPLv3)": "LGPL-3.0-only",
    "License :: OSI Approved :: ISC License (ISCL)": "ISC",
}

KNOWN_SPDX = {
    "MIT", "MIT-CMU", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "0BSD", "ISC", "Zlib",
    "PSF-2.0", "CC0-1.0", "LGPL-3.0-only", "LGPL-3.0-or-later", "GPL-2.0-only",
    "GPL-3.0-only", "GPL-2.0-or-later", "GPL-3.0-or-later",
}

UNKNOWN = "unknown (check upstream)"


def _license_file_text(dist: md.Distribution) -> str | None:
    """Head (2500 chars) of the main license file of a dist-info; later parts often append third-party licenses (pandas)."""
    cands = []
    for f in dist.files or []:
        parts = f.parts
        if ".dist-info" not in parts[0]:
            continue
        # dist-info/LICENSE* or dist-info/licenses/LICENSE*
        if len(parts) not in (2, 3) or (len(parts) == 3 and parts[1] != "licenses"):
            continue
        name = f.name.upper()
        if name.startswith(("LICENSE", "COPYING")) and name != "LICENSES.TXT" \
                and "EXTERNAL" not in name:
            cands.append(f)
    if not cands:
        return None
    cands.sort(key=lambda f: (len(f.name), f.name))
    try:
        return cands[0].read_binary()[:2500].decode("utf-8", "replace")
    except OSError:
        return None


def _identify_text(text: str) -> str | None:
    """Recognise a license from standard phrases; several licenses are joined by AND."""
    low = text.lower()
    if "license agreement for matplotlib" in low:
        return "PSF-based (Matplotlib License)"
    ids = []
    if "apache license" in low and "version 2.0" in low:
        ids.append("Apache-2.0")
    if "redistribution and use in source and binary forms" in low:
        three = "neither the name" in low or "may be used to endorse" in low
        ids.append("BSD-3-Clause" if three else "BSD-2-Clause")
    if "permission is hereby granted, free of charge" in low:
        ids.append("MIT")
    return " AND ".join(ids) if ids else None


def _is_spdx_expression(s: str) -> bool:
    toks = [t for t in re.split(r"\s+|[()]", s) if t and t not in ("AND", "OR", "WITH")]
    return bool(toks) and all(t in KNOWN_SPDX for t in toks)


def resolve_license(dist: md.Distribution) -> str:
    meta = dist.metadata
    canon = canonicalize_name(meta["Name"])
    expr = (meta.get("License-Expression") or "").strip()
    if expr:
        return expr
    cls = [c for c in meta.get_all("Classifier") or [] if c.startswith("License ::")]
    if len(cls) == 1 and cls[0] in CLASSIFIER_TO_SPDX:
        return CLASSIFIER_TO_SPDX[cls[0]]
    text = _license_file_text(dist)
    if text:
        found = _identify_text(text)
        if found:
            return found
    field = " ".join((meta.get("License") or "").split())
    if field and len(field) <= 80 and _is_spdx_expression(field):
        return field
    return OVERRIDES.get(canon, UNKNOWN)


def project_url(dist: md.Distribution) -> str:
    meta = dist.metadata
    if meta.get("Home-page"):
        return meta["Home-page"].strip()
    urls = {}
    for item in meta.get_all("Project-URL") or []:
        k, _, v = item.partition(",")
        urls.setdefault(k.strip().lower(), v.strip())
    for k in ("homepage", "home", "source", "repository", "documentation"):
        if k in urls:
            return urls[k]
    return next(iter(sorted(urls.values())), "")


def closure(roots: list[str], env: dict) -> dict[str, set[str]]:
    """canonical name -> set of requiring names ('' = root), markers evaluated with env."""
    found: dict[str, set[str]] = {}
    extras_of: dict[str, set[str]] = {}
    todo = []

    def add(req: Requirement, parent: str) -> None:
        name = canonicalize_name(req.name)
        new_extras = set(req.extras) - extras_of.get(name, set())
        first = name not in found
        found.setdefault(name, set()).add(parent)
        extras_of.setdefault(name, set()).update(req.extras)
        if first or new_extras:
            todo.append(name)

    for r in roots:
        req = Requirement(r)
        if req.marker is None or req.marker.evaluate({**env, "extra": ""}):
            add(req, "")
    while todo:
        name = todo.pop()
        try:
            dist = md.distribution(name)
        except md.PackageNotFoundError:
            continue  # not installed on this platform: listed, but not followed
        for line in dist.requires or []:
            req = Requirement(line)
            if req.marker is not None and not any(
                    req.marker.evaluate({**env, "extra": e}) for e in extras_of[name] | {""}):
                continue
            add(req, name)
    return found


def display(name: str) -> tuple[str, str, str, str]:
    try:
        d = md.distribution(name)
    except md.PackageNotFoundError:
        return name, "-", OVERRIDES.get(name, UNKNOWN), ""
    return d.metadata["Name"], d.version, resolve_license(d), project_url(d)


def row(name: str, extra: str | None = None) -> str:
    n, v, lic, url = display(name)
    link = f"[link]({url})" if url else ""
    cells = [n, v, lic, link] + ([extra] if extra is not None else [])
    return "| " + " | ".join(cells) + " |"


def table(names: list[str], extra_head: str | None = None, extra=None) -> list[str]:
    head = ["Package", "Version", "License", "URL"] + ([extra_head] if extra_head else [])
    out = ["| " + " | ".join(head) + " |", "|" + "|".join("---" for _ in head) + "|"]
    for n in sorted(names, key=lambda s: s.lower()):
        out.append(row(n, extra(n) if extra else None))
    return out


NOTES = """\
## Notes

**PySide6 / shiboken6 / Qt for Python** are offered under LGPL-3.0-only, GPL-2.0-only or
GPL-3.0-only (and a commercial license). This application uses them under LGPL-3.0. If an
executable of this application is distributed, the LGPL-3.0 terms apply to those libraries,
including providing the LGPL-3.0 license text and allowing users to replace the Qt/PySide6
libraries with their own (compatible) versions.

**asammdf** (LGPL-3.0-or-later) belongs to the optional `convert` group and is only needed by
the conversion tool (`tools/convert_to_parquet.sh`); the viewer itself does not import it.

**Platform-specific packages** are only required on some operating systems (environment
markers in the dependency metadata); a package listed there but not installed on the machine
that generated this file is shown without a version.

**Scope**: license identifiers are taken from the installed package metadata and license
files (see `tools/gen_third_party_licenses.py` for the order and the few manual overrides);
they are informational, not legal advice. Each package's own license text is authoritative.

**Regeneration**: this file is produced by `tools/gen_third_party_licenses.py`; do not edit
it by hand.

```bash
uv sync --group convert
uv run python tools/gen_third_party_licenses.py --output THIRD_PARTY_LICENSES.md
```
"""


def generate() -> str:
    cfg = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    rt_roots = cfg["project"]["dependencies"]
    cv_roots = cfg["dependency-groups"]["convert"]
    rt = {p: closure(rt_roots, env) for p, env in PLATFORMS.items()}
    cv = {p: closure(cv_roots + rt_roots, env) for p, env in PLATFORMS.items()}
    rt_all = set.intersection(*(set(c) for c in rt.values()))
    rt_any = set.union(*(set(c) for c in rt.values()))
    cv_any = set.union(*(set(c) for c in cv.values())) - rt_any
    cv_all = set.intersection(*(set(c) - rt_any for c in cv.values()))
    plat_rt = rt_any - rt_all
    plat_cv = cv_any - cv_all

    def where(group: str):
        src = rt if group == "runtime" else cv

        def f(n: str) -> str:
            plats = [p for p in PLATFORMS if n in src[p]]
            by = sorted({canonicalize_name(x) if x else "pyproject.toml" for p in plats for x in src[p][n]})
            return f"{', '.join(plats)} (required by {', '.join(by)}; {group})"
        return f

    lines = [
        "# Third-Party Licenses", "",
        "This application uses the following third-party software and libraries. Each component",
        "keeps its own license; this file lists the packages that are installed as dependencies.",
        "It is generated by `tools/gen_third_party_licenses.py` (see Notes).", "",
        "## Runtime dependencies", "",
        *table(sorted(rt_all)), "",
        "## Optional: conversion tool (`convert` group)", "",
        "Only needed to run `tools/convert_to_parquet.sh` (packages not already listed above).", "",
        *table(sorted(cv_all)), "",
        "## Platform-specific dependencies", "",
        "Required only on some operating systems.", "",
        *table(sorted(plat_rt), "Required on", where("runtime")),
    ]
    # one table, two groups: continue rows with the convert group's entries
    lines += [row(n, where("convert")(n)) for n in sorted(plat_cv, key=str.lower)]
    lines += ["", NOTES]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--output", "-o", help="write to this file instead of stdout")
    a = ap.parse_args()
    text = generate()
    if a.output:
        Path(a.output).write_text(text, encoding="utf-8", newline="\n")
    else:
        sys.stdout.write(text)


if __name__ == "__main__":
    main()
