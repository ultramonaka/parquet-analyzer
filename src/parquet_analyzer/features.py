"""Developer feature flags (detailed_specification.md 21.1).

Lets a feature be committed switched OFF until it is finished and reviewed. Stdlib-only,
like config.py, since both io/ and ui/ import it. Flags gate UI entry points only; the
underlying computation/APIs stay reachable (and tested) regardless.

Override for a run with PARQUET_ANALYZER_FEATURES="fft=on,xy_plot=1". Ignored when frozen
(a shipped exe is pinned to the defaults).
"""

import logging
import os
import sys

logger = logging.getLogger(__name__)

DEFAULT_FEATURES: dict[str, bool] = {
    "fft": False,  # debugging unfinished (formerly io/settings.py's FFT_ENABLED)
    "independent_x_axis": True,  # turned on 2026-10-02 for the user's real-machine check (ch.24 reviewed)
    "histogram": True,  # analysis 5.33 / ch.42: ON since 2026-10-06 (user's real-machine check OK)
    "xy_plot": True,  # on 2026-10-03 after review + user check (ch.25 stage 1)
    "xyz_3d": True,  # on 2026-10-03 after review + user check (ch.25.3/25.5)
    "user_functions": True,  # on 2026-10-03 after review + user check (ch.26)
    "big_a_thin_first_draw": True,  # on 2026-10-04: adopted after the 36.7 comparison (A: strided first draw)
    "maps": True,  # on 2026-10-03 after review + user check (ch.27)
    "big_c_windowed_derived": True,  # on 2026-10-04: adopted after 36.7 (C: derived variables computed per window)
    "big_d_time_index": True,  # on 2026-10-04: adopted after 36.7 (D: TimeIndex keeps a big ascending time axis on disk)
    "map_from_xy": True,  # on 2026-10-07 after review + user check of stages 1-2 and 44.9 (ch.44)
    "parquet_export": True,  # on 2026-10-16 after review + user check (export 57, stages 1-2)
    "map_exclude_points": False,  # analysis 44.13 (8): hidden 2026-10-13 (user found it unclear in the real-machine check)
}

ENV_VAR = "PARQUET_ANALYZER_FEATURES"

_TRUE = {"on", "1", "true", "yes"}
_FALSE = {"off", "0", "false", "no"}


def _parse_overrides(text: str) -> dict[str, bool]:
    """Parse "name=value,..." into overrides; unknown names / bad values are skipped with a warning."""
    result: dict[str, bool] = {}
    for item in (text or "").split(","):
        item = item.strip()
        if not item:
            continue
        name, sep, value = item.partition("=")
        name, value = name.strip(), value.strip().lower()
        if not sep or name not in DEFAULT_FEATURES:
            logger.warning("%s: ignoring unknown feature %r", ENV_VAR, item)
        elif value in _TRUE:
            result[name] = True
        elif value in _FALSE:
            result[name] = False
        else:
            logger.warning("%s: ignoring invalid value in %r", ENV_VAR, item)
    return result


def _compute_effective(env, frozen: bool) -> dict[str, bool]:
    """Defaults plus env overrides (env is never consulted when frozen)."""
    effective = dict(DEFAULT_FEATURES)
    if not frozen:
        effective.update(_parse_overrides(env.get(ENV_VAR, "")))
    return effective


# Computed once at import: the UI is built at startup, so no runtime changes are expected.
_EFFECTIVE: dict[str, bool] = _compute_effective(os.environ, bool(getattr(sys, "frozen", False)))


def is_enabled(name: str) -> bool:
    """Whether feature `name` is on. Unknown names raise KeyError so typos aren't silently "off"."""
    return _EFFECTIVE[name]


def effective_features() -> dict[str, bool]:
    return dict(_EFFECTIVE)
