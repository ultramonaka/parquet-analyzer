"""detailed_specification.md 29.4: a time axis with NaN/Inf must not freeze the app.

The bug is a hang (pyqtgraph painting a NaN-x path), so each scenario runs in a
subprocess with a time limit; a hang shows up as a timeout failure."""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

_SRC = str(Path(__file__).resolve().parents[2] / "src")
_N = 3000

_SCRIPT = textwrap.dedent(
    """
    import os, sys
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    sys.path.insert(0, {src!r})
    import numpy as np
    from PySide6.QtWidgets import QApplication, QMessageBox
    for n in ("information", "warning", "critical", "question"):
        setattr(QMessageBox, n, staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok))
    app = QApplication(sys.argv[:1])
    from parquet_analyzer.ui.main_window import MainWindow
    w = MainWindow(); w.resize(900, 700); w.show()
    assert w.load_parquet({path!r})
    app.processEvents()
    def paint():
        for _ in range(5):
            app.processEvents()
        w.grab()
        for _ in range(5):
            app.processEvents()
    w.set_time_column({axis!r}); paint()          # nothing plotted yet
    plot = w.plot_grid.plots[0]
    w._add_variable_to_plot(plot, "val"); paint()  # first series
    w._nav_sync.refresh(); paint()
    w.plot_grid.go_home(); paint()
    w.set_time_column("time"); paint()             # back to a clean axis
    w.set_time_column({axis!r}); paint()           # and again with series present
    w.plot_grid.go_home(); paint()
    lo, hi = w.navigator._region.getRegion()
    ok = np.isfinite(w._time_values)
    if ok.any():
        assert np.isfinite(lo) and np.isfinite(hi), (lo, hi)
        assert lo == w._time_values[ok].min() and hi == w._time_values[ok].max(), (lo, hi)
    xd = w.navigator._curve.xData
    assert xd is None or np.isfinite(xd).all()
    for p in w.plot_grid.plots:
        vr = p.viewRange()[0]
        assert np.isfinite(vr).all(), vr
        for name in p.series_names():
            cx = p._series[name]["curve"].xData
            assert cx is None or np.isfinite(cx).all()
    print("DONE")
    """
)


def _axis(kind: str) -> np.ndarray:
    t = np.linspace(-1.0, 1.0, _N)
    t = np.random.default_rng(1).permutation(t)  # not ascending
    if kind == "nan_start_mid":
        t[:200] = np.nan
        t[1500:1600] = np.nan
    elif kind == "inf":
        t[0] = np.inf
        t[1] = -np.inf
        t[1200] = np.inf
    elif kind == "all_nan":
        t[:] = np.nan
    return t


@pytest.mark.parametrize("kind", ["nan_start_mid", "inf", "all_nan"])
def test_nonfinite_time_axis_does_not_hang(tmp_path, kind):
    path = tmp_path / f"{kind}.parquet"
    table = pa.table(
        {
            "time": pa.array(np.arange(_N, dtype=np.float64)),
            "bad": pa.array(_axis(kind)),
            "val": pa.array(np.sin(np.arange(_N) / 50.0)),
        }
    )
    pq.write_table(table, path)
    script = _SCRIPT.format(src=_SRC, path=str(path), axis="bad")
    # Keep the child's cfg/data/log under tmp_path, like isolated_config does in-process.
    env = {**os.environ, "PARQUET_ANALYZER_ROOT": str(tmp_path / "root")}
    try:
        proc = subprocess.run(
            [sys.executable, "-c", script], capture_output=True, text=True, timeout=60, env=env
        )
    except subprocess.TimeoutExpired:
        pytest.fail(f"{kind}: app hung painting a non-finite time axis (29.4)")
    assert "DONE" in proc.stdout, proc.stdout + proc.stderr
