"""Regenerate the old-version view JSONs used by test_view_backward_compat.py (app 46.7, A4).

    uv run python tests/parquet_analyzer/data/views/make_old_views.py            # all versions
    uv run python tests/parquet_analyzer/data/views/make_old_views.py 0.3.0      # one version

For each version it makes a temporary `git worktree` of that version's commit, starts a child
Python with that version's `src/` first on `sys.path` (offscreen Qt), writes a small Parquet file,
drives that version's own `MainWindow` to build views using as many of its features as it has, and
saves them through that version's own `save_view_dialog` (dialogs monkeypatched). The JSON files
land in `tests/parquet_analyzer/data/views/<version>/`; the worktree is removed afterwards. The JSON
is never written by hand. The tests do not run this script (it needs git history and takes a while).

Versions (git commits; the only tag is v0.3.0):
    0.1.0  f5573e9^   the last 0.1 shape (the commit before the bump to 0.2.0)
    0.2.0  f5573e9    same source as 0.1.0 except the version string
    0.3.0  v0.3.0     adds time_column / relative_path / series visibility / height_ratio / X-link ...

Each version has a feature table in `VIEWS` below: which views it can build. A feature a version
does not have is skipped (probed with `hasattr`), never faked.

When the view format changes in the future, run this script from the `main` *before* the change
(add the new version to VERSIONS first), commit the new JSONs, then change the format.

The generated JSON keeps absolute paths of the temporary data directory
(`/tmp/parquet_analyzer_old_views/<version>/`); the tests never open that path (the Parquet file is
opened first and the view is applied to it, or the JSON is copied next to a freshly written file).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[3]
N_ROWS = 1000
WORK_ROOT = Path("/tmp/parquet_analyzer_old_views")  # fixed so the absolute paths in the JSON are stable

VERSIONS = {  # version -> git revision
    "0.1.0": "f5573e9^",
    "0.2.0": "f5573e9",
    "0.3.0": "v0.3.0",
}


def write_sample_parquet(path: Path) -> None:
    """time (ascending, 1 s steps), sine, cosine, ramp (ascending 0..N-1), flag (0/1)."""
    import numpy as np
    import pyarrow as pa
    import pyarrow.parquet as pq

    n = N_ROWS
    k = np.arange(n)
    times = np.datetime64("2026-01-01T00:00:00", "ms") + k.astype("int64") * np.timedelta64(1000, "ms")
    table = pa.table(
        {
            "time": pa.array(times),
            "sine": np.sin(k / 50.0),
            "cosine": np.cos(k / 50.0),
            "ramp": k.astype(np.float64),
            "flag": ((k // 100) % 2).astype(np.int64),
        }
    )
    pq.write_table(table, path)


# ---------------------------------------------------------------- child: runs inside one version


def _child(version: str, src: str, root: str, out: str) -> None:
    sys.path.insert(0, src)
    os.environ["PARQUET_ANALYZER_ROOT"] = root
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    import parquet_analyzer

    assert Path(parquet_analyzer.__file__).resolve().is_relative_to(Path(src).resolve()), parquet_analyzer.__file__
    print(f"[{version}] using {parquet_analyzer.__file__} (version string {parquet_analyzer.__version__})")

    from PySide6.QtWidgets import QApplication, QInputDialog, QMessageBox

    app = QApplication.instance() or QApplication([])
    pending_name: list[str] = []
    QInputDialog.getText = staticmethod(lambda *a, **k: (pending_name[0], True))  # type: ignore[method-assign]
    for fn in ("information", "warning", "critical", "question"):
        setattr(QMessageBox, fn, staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    try:  # 0.3.0 has its own dialog wrapper module
        from parquet_analyzer.ui import messages

        for fn in ("information", "warning", "critical"):
            setattr(messages, fn, lambda *a, **k: None)
    except ImportError:
        pass

    from parquet_analyzer import config
    from parquet_analyzer.ui.main_window import MainWindow

    data_dir = Path(root)
    parquet = data_dir / "sample.parquet"
    write_sample_parquet(parquet)

    def pump(seconds: float = 0.15) -> None:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            app.processEvents()
            time.sleep(0.005)

    def new_window() -> MainWindow:
        w = MainWindow()
        w.resize(1200, 800)
        w.show()
        assert w.load_parquet(str(parquet))
        pump()
        return w

    def add(w, plot, *names):
        for n in names:
            w._add_variable_to_plot(plot, n)
        pump()

    def zoom_x(w, lo_frac: float, hi_frac: float) -> None:
        lo, hi = w.plot_grid.anchor_plot().getViewBox().viewRange()[0] if hasattr(w.plot_grid, "anchor_plot") else w.plot_grid.plots[0].getViewBox().viewRange()[0]
        w.plot_grid.set_x_range(lo + (hi - lo) * lo_frac, lo + (hi - lo) * hi_frac)
        pump()

    def save(w, name: str) -> None:
        pump()
        pending_name[:] = [name]
        w.save_view_dialog()
        w.close()
        pump(0.05)

    done: list[str] = []

    # --- view "basic": two plots, several series, Y and X ranges (every version)
    w = new_window()
    p1 = w.plot_grid.plots[0]
    add(w, p1, "sine", "cosine")
    p2 = w.plot_grid.add_plot()
    add(w, p2, "ramp")
    p1.getViewBox().setYRange(-2.0, 2.0, padding=0)
    zoom_x(w, 0.2, 0.6)
    if hasattr(p1, "set_series_visible"):  # 0.3.0: a hidden series and unequal plot heights
        p1.set_series_visible("cosine", False)
    if hasattr(w.plot_grid, "set_plot_height_ratios"):
        w.plot_grid.set_plot_height_ratios([2.0, 1.0])
    save(w, "basic")
    done.append("basic")

    # --- view "derived": nested derived variables, a series of a derived one, downsampling off
    w = new_window()
    p1 = w.plot_grid.plots[0]
    w._on_add_derived_variable("d_double", "sine * 2")
    w._on_add_derived_variable("d_sum", "d_double + cosine")  # depends on another derived variable
    w._on_add_derived_variable("d_abs", "abs(sine - cosine)")
    add(w, p1, "d_sum", "d_abs")
    p2 = w.plot_grid.add_plot()
    add(w, p2, "d_double", "flag")
    if hasattr(w, "_apply_downsample_enabled"):
        w._apply_downsample_enabled(False, update_action=True)
    elif hasattr(w, "downsample_action"):
        w.downsample_action.setChecked(False)
    save(w, "derived")
    done.append("derived")

    # --- features of later versions (each guarded by the feature itself)
    if hasattr(w.plot_grid, "set_plot_x_linked"):
        w = new_window()
        p1 = w.plot_grid.plots[0]
        add(w, p1, "sine")
        p2 = w.plot_grid.add_plot()
        add(w, p2, "cosine")
        p3 = w.plot_grid.add_plot()
        add(w, p3, "ramp", "flag")
        zoom_x(w, 0.1, 0.9)
        assert w.plot_grid.set_plot_x_linked(p2, False)
        lo, hi = p2.getViewBox().viewRange()[0]
        p2.setXRange(lo + (hi - lo) * 0.3, lo + (hi - lo) * 0.5, padding=0)
        w.plot_grid.set_plot_height_ratios([1.0, 2.0, 3.0])
        p3.set_series_visible("flag", False)
        pump()
        save(w, "independent_x")
        done.append("independent_x")

    if hasattr(MainWindow, "set_time_column"):
        w = new_window()
        w.set_time_column("ramp")  # a float column as the X axis (0.3.0 saves it as time_column)
        pump()
        add(w, w.plot_grid.plots[0], "sine", "cosine")
        w._on_add_derived_variable("d_flag2", "flag * 2")
        add(w, w.plot_grid.plots[0], "d_flag2")
        # 0.1/0.2 do not store the time column (their JSON has no `time_column`); 0.3.0 does
        save(w, "time_axis")
        done.append("time_axis")

    if hasattr(MainWindow, "function_registry"):  # user functions and maps exist from 0.3.0
        import numpy as np

        from parquet_analyzer.core.functions import UserFunction
        from parquet_analyzer.core.maps import LookupTable

        w = new_window()
        reg = w._function_registry
        reg.user_functions["scaled"] = UserFunction("scaled", ["x"], [("k", 2.0)], "x * k")
        reg.maps["curve"] = LookupTable("curve", "curve", np.array([0.0, 500.0, 999.0]), np.array([0.0, 1.0, 0.5]))
        w._on_add_derived_variable("d_fn", "scaled(sine)")
        w._on_add_derived_variable("d_map", "curve(ramp)")
        w._on_add_derived_variable("d_plain", "cosine + 1")
        add(w, w.plot_grid.plots[0], "d_fn", "d_map", "d_plain")
        save(w, "functions_maps")
        done.append("functions_maps")

    out_dir = Path(out)
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("*.json"):
        old.unlink()
    for f in sorted(config.PARQUET_ANALYZER_DATA_DIR.glob("*.json")):
        shutil.copy2(f, out_dir / f.name)
        payload = json.loads(f.read_text(encoding="utf-8"))
        print(f"[{version}] saved {f.name}: keys={sorted(payload)}")
    print(f"[{version}] views: {done}")


# ---------------------------------------------------------------- parent: worktrees


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=REPO, check=True, capture_output=True, text=True).stdout.strip()


def make_version(version: str) -> bool:
    rev = VERSIONS[version]
    tmp = Path(tempfile.mkdtemp(prefix=f"pa_old_{version}_"))
    wt = tmp / "wt"
    root = WORK_ROOT / version
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True)
    try:
        _git("worktree", "add", "--detach", str(wt), rev)
        env = dict(os.environ, PYTHONPATH=str(wt / "src"), QT_QPA_PLATFORM="offscreen", PARQUET_ANALYZER_ROOT=str(root))
        proc = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "--child", version, str(wt / "src"), str(root), str(HERE / version)],
            cwd=tmp, env=env, text=True, capture_output=True, timeout=600,
        )
        sys.stdout.write(proc.stdout)
        if proc.returncode != 0:
            sys.stderr.write(f"[{version}] FAILED (skipped):\n{proc.stderr[-3000:]}\n")
            return False
        return True
    finally:
        subprocess.run(["git", "worktree", "remove", "--force", str(wt)], cwd=REPO, check=False, capture_output=True)
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "--child":
        _child(*sys.argv[2:6])
        return 0
    ap = argparse.ArgumentParser()
    ap.add_argument("versions", nargs="*", default=list(VERSIONS))
    args = ap.parse_args()
    ok = {v: make_version(v) for v in args.versions}
    print(ok)
    return 0 if all(ok.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
