"""Rotatable 3D view (detailed_specification.md 25.3; analysis.ja.md 25.3, spec 5.19 stage 2).

Since 25.5 (2026-10-03) the 3D view lives inside the XY plot window (`xy_plot_window.py`,
2D/3D toggle); there is no separate 3D window any more. This module holds:

* pure data preparation (`compute_xyz`, `normalize`, `nice_ticks`, `colors_for`, ...) that is
  testable headlessly -- OpenGL cannot render offscreen;
* `Xyz3DView(QWidget)`, a `pyqtgraph.opengl` component with `set_scatter` / `set_surface`
  (the latter is reused by the map manager's preview, 27.4). The view buttons sit in a normal
  row above the GL view and the hover readout in a normal label below it: child widgets
  laid over a QOpenGLWidget do not render their text on the real macOS display (25.5);
* a matplotlib `mplot3d` re-draw for image export, and the `probe_gl3d` availability check.

`pyqtgraph.opengl` / PyOpenGL are imported lazily (first 3D use, `probe_gl3d`), never at
module import time, so the app starts without them (25.3).
"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np
from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import QApplication, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from ..core.oplog import log_op
from .analysis_common import chunks as _chunks
from .analysis_common import format_time_tick
from .analysis_window import CANCELLED
from .i18n import tr
from .image_export import _FIG_DPI, _font_context
from .plot_widget import _COLOR_CYCLE

logger = logging.getLogger(__name__)

XYZ_POINT_LIMIT = 500_000  # points above this are thinned by an even stride (25.3)
XYZ_CHUNK_ROWS = 5_000_000
_CMAP = "viridis"
_BASE_DISTANCE = 2.6
_SINGLE_COLOR = _COLOR_CYCLE[0]  # one colour for every point (colour = single; same as the first XY series)
HOVER_RADIUS_PX = 12  # hover picks the nearest point within this screen distance (25.3)
# View presets: (elevation, azimuth) in pyqtgraph's convention; None = the home view (25.3).
# XY from straight above (X right, Y up), YZ from +X (Y right, Z up), ZX from -Y (X right, Z up).
VIEW_PRESETS: dict[str, tuple[float, float] | None] = {
    "home": None,
    "xy": (90.0, -90.0),
    "yz": (0.0, 0.0),
    "zx": (0.0, -90.0),
}
# The axis pointing at the camera in each preset: its tick labels all pile up in one corner, so
# they are left out there (the title stays; the hover readout gives the values) (25.3).
_DEPTH_AXIS = {"xy": "z", "yz": "x", "zx": "y"}

# ---------------------------------------------------------------------------
# Availability probe (25.3)
# ---------------------------------------------------------------------------

_PROBE: tuple[bool, str] | None = None  # session-wide cache of the first probe


def probe_gl3d() -> tuple[bool, str]:
    """Can a `GLViewWidget` really render here? Returns `(ok, reason)` (25.3).

    Creating the widget does NOT raise when OpenGL is unusable (Qt just warns on stderr and
    draws nothing), so the context must be checked after `show()`. Needs a QApplication; call
    from the GUI thread."""
    try:
        import OpenGL  # noqa: F401
        import pyqtgraph.opengl as gl
    except Exception as e:  # noqa: BLE001 - PyOpenGL can raise non-ImportErrors while loading platform libs
        return False, f"import failed: {type(e).__name__}: {e}"
    w = None
    try:
        from PySide6.QtWidgets import QApplication

        w = gl.GLViewWidget()
        w.resize(64, 64)
        w.show()  # the context is only created once the widget is shown
        QApplication.processEvents()
        ctx = w.context()
        ok = ctx is not None and ctx.isValid()
        img = w.grabFramebuffer() if ok else None
        ok = bool(ok and img is not None and not img.isNull())
        return (ok, "ok" if ok else "OpenGL context could not be created")
    except Exception as e:  # noqa: BLE001
        return False, f"widget creation failed: {type(e).__name__}: {e}"
    finally:
        if w is not None:
            try:
                w.close()
                w.deleteLater()
            except Exception:  # noqa: BLE001
                pass


def gl3d_status() -> tuple[bool, str] | None:
    """The cached probe result, or None when the probe has not run yet this session."""
    return _PROBE


def check_gl3d() -> tuple[bool, str]:
    """Run the probe once per session (first 3D button press) and cache it."""
    global _PROBE
    if _PROBE is None:
        _PROBE = probe_gl3d()
        logger.info("3D availability probe: %s", _PROBE)
    return _PROBE


def reset_gl3d_probe() -> None:
    """Forget the cached probe (tests)."""
    global _PROBE
    _PROBE = None


# ---------------------------------------------------------------------------
# Pure data preparation
# ---------------------------------------------------------------------------


def normalize(values, lo: float, hi: float) -> np.ndarray:
    """Map values from [lo, hi] to [0, 1]; a degenerate span maps to 0.5 (25.3)."""
    v = np.asarray(values, dtype=np.float64)
    span = hi - lo
    if not span > 0:
        return np.full(v.shape, 0.5)
    return (v - lo) / span


def nice_ticks(lo: float, hi: float, max_n: int = 5) -> list[float]:
    """Round tick values (1/2/5 x 10^k) inside [lo, hi]; `[lo]` for a degenerate span."""
    if not (math.isfinite(lo) and math.isfinite(hi)):
        return []
    if not hi > lo:
        return [lo]
    raw = (hi - lo) / max(max_n, 1)
    if not (math.isfinite(raw) and raw > 0):
        return [lo]  # plots 24.8: span overflowed (1e308 range) or underflowed (denormal): no round ticks possible
    mag = 10 ** math.floor(math.log10(raw))
    step = next(m * mag for m in (1, 2, 5, 10) if m * mag >= raw)
    first, last = math.ceil(lo / step - 1e-9), math.floor(hi / step + 1e-9)
    ticks = list(dict.fromkeys(round(k * step, 12) for k in range(first, last + 1)))
    # plots 24.8: rounding to 12 places can collapse a tiny span into duplicates (0..1e-300 -> six 0.0)
    return ticks if len(ticks) > 1 else [lo]


def format_tick(value: float) -> str:
    return f"{value:.6g}"


def format_axis_value(value: float, lo: float, hi: float, is_time: bool) -> str:
    """A tick / hover value: local time (precision from the axis span) for a time axis, else
    a plain number (25.5)."""
    return format_time_tick(float(value), hi - lo) if is_time else format_tick(float(value))


def axis_tick_labels(bounds: dict[str, tuple[float, float]], time_axes=()) -> list[tuple[str, float, str]]:
    """(axis "x"/"y"/"z", normalized position in [0,1], real-value label) for every tick (25.3).
    Axes in `time_axes` are labelled in local time (25.5)."""
    out = []
    for axis in ("x", "y", "z"):
        lo, hi = bounds[axis]
        for t in nice_ticks(lo, hi):
            out.append((axis, float(normalize(t, lo, hi)), format_axis_value(t, lo, hi, axis in time_axes)))
    return out


def colors_for(values, lo: float, hi: float, cmap: str = _CMAP) -> np.ndarray:
    """(n, 4) float32 RGBA from a matplotlib colour map over [lo, hi]."""
    import matplotlib

    v = np.clip(normalize(values, lo, hi), 0.0, 1.0)
    return np.asarray(matplotlib.colormaps[cmap](v), dtype=np.float32)


@dataclass
class Xyz3DResult:
    x_name: str
    y_name: str
    z_name: str
    color: str  # "z" | "time" | "single"
    row_start: int
    row_end: int
    time_name: str | None = None  # the time-axis column's name, so the UI knows which axes are time (25.5)
    x: np.ndarray = field(default_factory=lambda: np.empty(0))
    y: np.ndarray = field(default_factory=lambda: np.empty(0))
    z: np.ndarray = field(default_factory=lambda: np.empty(0))
    c: np.ndarray = field(default_factory=lambda: np.empty(0))  # colour values per shown point
    valid: int = 0
    excluded: int = 0
    thinned: tuple[int, int] | None = None  # (shown, total valid) when strided
    bounds: dict[str, tuple[float, float]] = field(default_factory=dict)  # over ALL valid points
    color_range: tuple[float, float] | None = None


def compute_xyz(
    fetch: Callable[[str, int, int], np.ndarray],
    x_name: str,
    y_name: str,
    z_name: str,
    color: str,
    row_start: int,
    row_end: int,
    times: np.ndarray | None = None,
    limit: int | None = None,
    chunk_rows: int | None = None,
    cancelled: Callable[[], bool] | None = None,
    time_name: str | None = None,
) -> Xyz3DResult:
    """Rows [row_start, row_end) with finite X, Y, Z (25.3). Bounds/colour range cover every
    valid point; the returned points are thinned by an even stride above `limit`
    (default `XYZ_POINT_LIMIT`). `fetch` also serves the time column's name (25.5), which
    `time_name` just records. Colour "single" keeps one colour (the view ignores `c`)."""
    limit = XYZ_POINT_LIMIT if limit is None else limit
    chunk_rows = chunk_rows or XYZ_CHUNK_ROWS
    by_time = color == "time"
    n_rows = max(0, row_end - row_start)
    r = Xyz3DResult(x_name, y_name, z_name, color if color in ("time", "single") else "z", row_start, row_end,
                    time_name)
    cache: dict[tuple[str, int, int], np.ndarray] = {}

    def get(name: str, a: int, b: int) -> np.ndarray:
        if n_rows <= chunk_rows:  # a single chunk is read once and reused by pass 2
            if (name, a, b) not in cache:
                cache[(name, a, b)] = np.asarray(fetch(name, a, b), dtype=np.float64)
            return cache[(name, a, b)]
        return np.asarray(fetch(name, a, b), dtype=np.float64)

    def valid(a: int, b: int):
        x, y, z = get(x_name, a, b), get(y_name, a, b), get(z_name, a, b)
        c = np.asarray(times[a:b], dtype=np.float64) if by_time else z
        return x, y, z, c, np.isfinite(x) & np.isfinite(y) & np.isfinite(z) & np.isfinite(c)

    lo = {k: math.inf for k in "xyzc"}
    hi = {k: -math.inf for k in "xyzc"}
    for a, b in _chunks(row_start, row_end, chunk_rows):
        if cancelled is not None and cancelled():
            return CANCELLED
        x, y, z, c, mask = valid(a, b)
        nv = int(mask.sum())
        r.valid += nv
        r.excluded += (b - a) - nv
        if nv:
            for k, arr in zip("xyzc", (x, y, z, c)):
                v = arr[mask]
                lo[k], hi[k] = min(lo[k], float(v.min())), max(hi[k], float(v.max()))
    if r.valid == 0:
        return r
    r.bounds = {k: (lo[k], hi[k]) for k in "xyz"}
    r.color_range = (lo["c"], hi["c"])
    stride = math.ceil(r.valid / limit) if r.valid > limit else 1
    xs, ys, zs, cs = [], [], [], []
    seen = 0
    for a, b in _chunks(row_start, row_end, chunk_rows):
        if cancelled is not None and cancelled():
            return CANCELLED
        x, y, z, c, mask = valid(a, b)
        idx = np.flatnonzero(mask)
        if not len(idx):
            continue
        sel = idx[((seen + np.arange(len(idx))) % stride) == 0]
        seen += len(idx)
        xs.append(x[sel]), ys.append(y[sel]), zs.append(z[sel]), cs.append(c[sel])
    r.x, r.y, r.z, r.c = (np.concatenate(p) for p in (xs, ys, zs, cs))
    if len(r.x) < r.valid:
        r.thinned = (len(r.x), r.valid)
    return r


def _flat_gl_options() -> dict:
    """glOptions for items that must stay visible on top: no depth test (axis lines would
    z-fight with the floor grid, the hover dot with its own data point) and ordinary alpha
    blending (the default additive blend makes dark items vanish on the white background)."""
    from OpenGL import GL

    return {
        GL.GL_DEPTH_TEST: False,
        GL.GL_BLEND: True,
        GL.GL_ALPHA_TEST: False,
        "glBlendFunc": (GL.GL_SRC_ALPHA, GL.GL_ONE_MINUS_SRC_ALPHA),
    }


def matrix_to_numpy(m) -> np.ndarray:
    """A QMatrix4x4 as a (4, 4) row-major numpy array (so `M @ column_vector` works)."""
    return np.array(m.data(), dtype=np.float64).reshape(4, 4).T


def project_points(pos, proj: np.ndarray, view: np.ndarray, width: float, height: float):
    """Screen positions (px, origin top-left) of (n, 3) points under `proj @ view` (25.3).
    Returns `(xy (n, 2), in_front (n,) bool)`; one vectorized pass."""
    p = np.asarray(pos, dtype=np.float64).reshape(-1, 3)
    clip = np.concatenate([p, np.ones((len(p), 1))], axis=1) @ (proj @ view).T
    w = clip[:, 3]
    front = w > 1e-12
    safe = np.where(front, w, 1.0)
    sx = (clip[:, 0] / safe * 0.5 + 0.5) * width
    sy = (1.0 - (clip[:, 1] / safe * 0.5 + 0.5)) * height
    return np.column_stack([sx, sy]), front


def orphan_vertices(faces: np.ndarray, keep: np.ndarray) -> np.ndarray:
    """Flat indices (`i*ny + j`) of the vertices that have a value (`keep`, shape (nx, ny)) but belong
    to none of `faces` (44.3: drawn as points so a ring-shaped measurement does not lose them)."""
    used = np.zeros(keep.size, dtype=bool)
    if len(faces):
        used[np.unique(faces)] = True
    return np.flatnonzero(keep.ravel() & ~used)


def pick_nearest(pos, proj: np.ndarray, view: np.ndarray, width: float, height: float,
                 mx: float, my: float, radius: float = HOVER_RADIUS_PX) -> int | None:
    """Index of the point closest to the mouse (mx, my) on screen within `radius` px, else None."""
    if len(pos) == 0:
        return None
    xy, front = project_points(pos, proj, view, width, height)
    d2 = (xy[:, 0] - mx) ** 2 + (xy[:, 1] - my) ** 2
    d2 = np.where(front, d2, np.inf)
    i = int(np.argmin(d2))
    return i if d2[i] <= radius * radius else None


# ---------------------------------------------------------------------------
# GL component
# ---------------------------------------------------------------------------


class Xyz3DView(QWidget):
    """Mouse-rotatable 3D view. Every axis is normalized to [0, 1] (so differently scaled
    variables look alike); tick labels show the real values (25.3).

    `pyqtgraph.opengl` is imported here, on first construction -- construct only after
    `check_gl3d()` succeeded."""

    # Camera / viewpoint notifications for `link_camera` (27.9): the camera changed (any cause:
    # mouse, wheel, preset, `setCameraPosition`), and a preset/free-rotation frame change.
    cameraChanged = Signal()
    presetChanged = Signal(object)  # preset name ("home", "xy", ...) or None for a free rotation
    # A left press released without moving on a surface grid point: (iy, ix) of that point (27.12).
    gridPointClicked = Signal(int, int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        import pyqtgraph.opengl as gl  # lazy: PyOpenGL is optional at app start (25.3)

        self._gl = gl
        self._camera_state: tuple | None = None
        self._syncing = False
        self._press_pos = None  # left-press position awaiting its release (click vs drag, 27.12)
        owner = self

        class _WatchedView(gl.GLViewWidget):
            """GLViewWidget has no camera signal; every camera change ends in `update()`."""

            def update(self, *args, **kwargs):
                super().update(*args, **kwargs)
                owner._notify_camera()

        self._view = _WatchedView()
        self._view.setBackgroundColor("w")
        self._view.installEventFilter(self)
        self._view.setMouseTracking(True)  # hover readout (25.3)
        self._view.setFocusPolicy(Qt.FocusPolicy.ClickFocus)  # the Home key
        self.setFocusProxy(self._view)
        self.setMinimumHeight(260)
        self._data_items: list = []
        self.overlay_items: list = []  # registered maps overlaid on the scene (44.7); part of `_data_items`
        self._frame_items: list = []
        self._frame_args = None  # (bounds, labels) of the current frame
        self._hidden_ticks: str | None = None
        self._hover_items: list = []  # highlight dots, attached to the view only while hovering
        self._hover_index: int | None = None
        self.scene: dict = {}  # last normalized data, for tests / export
        # Layout (25.5): a normal button row on top, the GL view (+ colour bar) in the middle and
        # a normal label for the hover readout below. Nothing is laid over the GL view: child
        # widgets over a QOpenGLWidget do not render their text on the real macOS display.
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        self._build_button_row(layout)
        self._body = QHBoxLayout()
        self._body.setContentsMargins(0, 0, 0, 0)
        self._body.addWidget(self._view, 1)
        layout.addLayout(self._body, 1)
        self._hover_label = QLabel("")
        self._hover_label.setMinimumHeight(self._hover_label.fontMetrics().height() + 6)
        self._hover_label.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        layout.addWidget(self._hover_label)
        self._colorbar_widget = None
        self._colorbar = None
        self.reset_camera()

    @property
    def view(self):
        return self._view

    # -- camera sync between two views (27.9) ---------------------------------------------

    def camera_state(self) -> tuple:
        """(zoom, elevation, azimuth, center) with the distance divided by the aspect factor, so
        views of different shapes still agree on the zoom."""
        o = self._view.opts
        c = o["center"]
        zoom = float(o["distance"]) / max(getattr(self, "_aspect_factor", 1.0), 1e-9)
        return (round(zoom, 6), round(float(o["elevation"]), 6), round(float(o["azimuth"]), 6),
                (round(float(c.x()), 6), round(float(c.y()), 6), round(float(c.z()), 6)))

    def set_camera_state(self, state: tuple) -> None:
        """Adopt another view's camera without notifying (no feedback loop)."""
        from pyqtgraph import Vector

        zoom, elev, azim, center = state
        self._syncing = True
        try:
            self._view.opts["center"] = Vector(*center)
            self._view.setCameraPosition(distance=zoom * self._aspect_factor, elevation=elev, azimuth=azim)
            self._camera_state = self.camera_state()
        finally:
            self._syncing = False

    def _notify_camera(self) -> None:
        if self._syncing or not hasattr(self, "_aspect_factor"):
            return
        state = self.camera_state()
        if state != self._camera_state:
            self._camera_state = state
            self.cameraChanged.emit()

    def link_camera(self, other: Xyz3DView) -> None:
        """Keep both views' cameras and preset frames identical, in both directions (27.9).
        Applying a state that is already current emits nothing, so there is no ping-pong."""
        for a, b in ((self, other), (other, self)):
            a.cameraChanged.connect(lambda a=a, b=b: b.set_camera_state(a.camera_state()))
            a.presetChanged.connect(lambda name, b=b: b._follow_preset(name))
        other.set_camera_state(self.camera_state())

    def _follow_preset(self, name) -> None:
        """Mirror the other view's preset frame (hidden depth-axis ticks) without logging."""
        self._rebuild_frame(_DEPTH_AXIS.get(name) if name else None)
        self._clear_hover()

    # -- view buttons (row above the GL view) and hover readout (label below), 25.3 / 25.5 ---

    def _build_button_row(self, layout) -> None:
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        self._preset_buttons: dict[str, QPushButton] = {}
        for key in VIEW_PRESETS:
            b = QPushButton(tr(f"xyz.view_{key}"))
            b.setToolTip(tr(f"xyz.view_{key}_tooltip"))
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # keep the Home key on the view
            b.setAutoDefault(False)
            b.clicked.connect(lambda _checked=False, k=key: self.apply_preset(k))
            row.addWidget(b)
            self._preset_buttons[key] = b
        row.addStretch(1)
        self._button_row = row
        layout.addLayout(row)

    def hover_text(self) -> str:
        """The readout label's text ("" while nothing is picked)."""
        return self._hover_label.text() if self._hover_index is not None else ""

    def reset_camera(self) -> None:
        """Home view: initial distance (aspect-aware), elevation 25, azimuth -60 (25.3)."""
        from pyqtgraph import Vector

        self._view.opts["center"] = Vector(0.5, 0.5, 0.5)
        self._aspect_factor = self._factor_for(self._view.width(), self._view.height())
        self._view.setCameraPosition(distance=_BASE_DISTANCE * self._aspect_factor, elevation=25, azimuth=-60)
        self._rebuild_frame(None)
        self._clear_hover()
        self.presetChanged.emit("home")

    def go_home(self) -> None:
        """Button / double-click / Home key (25.3)."""
        log_op("xyz_3d_home")
        self.reset_camera()

    def apply_preset(self, name: str) -> None:
        """Home / XY / YZ / ZX viewpoints at the home distance (25.3)."""
        log_op("xyz_3d_view", preset=name)
        if name == "home" or VIEW_PRESETS.get(name) is None:
            self.reset_camera()
            return
        from pyqtgraph import Vector

        elev, azim = VIEW_PRESETS[name]
        self._rebuild_frame(_DEPTH_AXIS.get(name))
        self._view.opts["center"] = Vector(0.5, 0.5, 0.5)
        self._aspect_factor = self._factor_for(self._view.width(), self._view.height())
        self._view.setCameraPosition(distance=_BASE_DISTANCE * self._aspect_factor, elevation=elev, azimuth=azim)
        self._clear_hover()
        self.presetChanged.emit(name)

    @staticmethod
    def _factor_for(width: int, height: int) -> float:
        return max(1.0, width / height) if height > 0 else 1.0

    def keyPressEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if event.key() == Qt.Key.Key_Home:
            self.go_home()
            event.accept()
            return
        super().keyPressEvent(event)

    def eventFilter(self, obj, event):  # noqa: N802 (Qt override)
        """pyqtgraph's fov is horizontal, so in a wide, short viewport the unit cube is cut
        off: scale the camera distance with the aspect ratio, keeping the user's zoom/rotation.
        Also: double-click / Home key -> home, mouse move -> hover readout (25.3)."""
        if obj is not self._view:
            return False
        t = event.type()
        if t == QEvent.Type.Resize:
            factor = self._factor_for(self._view.width(), self._view.height())
            if factor != self._aspect_factor:
                dist = float(self._view.opts["distance"]) * factor / self._aspect_factor
                self._aspect_factor = factor
                self._view.setCameraPosition(distance=dist)
        elif t == QEvent.Type.MouseButtonDblClick:
            self.go_home()
        elif t == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Home:
            self.go_home()
            return True
        elif t == QEvent.Type.MouseMove:
            if event.buttons() != Qt.MouseButton.NoButton:  # rotating / panning: no picking
                self._clear_hover()
            else:
                p = event.position()
                self.hover_at(p.x(), p.y())
        elif t == QEvent.Type.MouseButtonRelease:
            self._finish_click(event)
        elif t in (QEvent.Type.Leave, QEvent.Type.MouseButtonPress, QEvent.Type.Wheel):
            self._clear_hover()
            if t == QEvent.Type.MouseButtonPress:
                left = event.button() == Qt.MouseButton.LeftButton
                self._press_pos = event.position() if left else None
            if t == QEvent.Type.MouseButtonPress:
                self._rebuild_frame(None)  # a free rotation: every axis' ticks are readable again
                self.presetChanged.emit(None)
        return False

    def _finish_click(self, event) -> None:
        """A left release within `startDragDistance` of its press is a click: emit
        `gridPointClicked(iy, ix)` for the surface grid point under it (27.12). Drags (rotate / pan)
        and non-surface scenes emit nothing."""
        start, self._press_pos = self._press_pos, None
        if start is None or event.button() != Qt.MouseButton.LeftButton:
            return
        end = event.position()
        if (end - start).manhattanLength() >= QApplication.startDragDistance():
            return
        i = self.pick_at(end.x(), end.y())
        cell = self.grid_index_of(i) if i is not None else None
        if cell is not None:
            self.gridPointClicked.emit(*cell)

    def grid_index_of(self, i: int) -> tuple[int, int] | None:
        """`(iy, ix)` of `scene["pos"][i]` for a surface scene, else None (27.12)."""
        grid = self.scene.get("grid_index")
        if self.scene.get("kind") != "surface" or grid is None:
            return None
        ix, iy = divmod(int(grid[i]), self.scene["ny"])  # vertex number = ix * ny + iy
        return int(iy), int(ix)

    # -- hover -----------------------------------------------------------------------

    def pick_at(self, mx: float, my: float) -> int | None:
        """Index (into `scene["pos"]`) of the displayed point nearest to the mouse within
        `HOVER_RADIUS_PX`, or None. One vectorized projection (25.3)."""
        pos = self.scene.get("pos")
        w, h = self._view.width(), self._view.height()
        if pos is None or len(pos) == 0 or w <= 0 or h <= 0:
            return None
        region = (0, 0, w, h)
        proj = matrix_to_numpy(self._view.projectionMatrix(region, region))
        return pick_nearest(pos, proj, matrix_to_numpy(self._view.viewMatrix()), w, h, mx, my)

    def hover_at(self, mx: float, my: float) -> int | None:
        """Update highlight + readout for the mouse at (mx, my) in view pixels. No op log
        (one call per mouse move)."""
        i = self.pick_at(mx, my)
        if i is None:
            self._clear_hover()
            return None
        if i != self._hover_index:
            self._hover_index = i
            self._show_highlight(self.scene["pos"][i])
            self._hover_label.setText(self.hover_label_text(i))
        return i

    def hover_label_text(self, i: int) -> str:
        """"X: … Y: … Z: …" (real values; plus time when coloured by time) for point `i`."""
        real = self.scene["real"][i]
        bounds, time_axes = self.scene["bounds"], self.scene.get("time_axes", ())
        text = "   ".join(
            f"{n}: {format_axis_value(v, *bounds[k], k in time_axes)}"
            for k, n, v in zip("xyz", self.scene["labels"], real)
        )
        extra = self.scene.get("extra")
        if extra is not None:
            text += "   " + str(extra[i])
        elif self.scene.get("extra_fn") is not None:
            ix, iy = divmod(int(self.scene["extra_index"][i]), self.scene["ny"])
            text += "   " + str(self.scene["extra_fn"](iy, ix))
        t = self.scene.get("time")
        if t is not None:
            tv = np.asarray(t, dtype=np.float64)
            span = float(np.nanmax(tv) - np.nanmin(tv)) if len(tv) else 0.0
            text += "   " + tr("xyz.hover_time", value=format_time_tick(float(tv[i]), span))
        return text

    def _rebuild_frame(self, hide_ticks: str | None) -> None:
        """Redraw the axes frame with (or without) the depth axis' tick labels."""
        args = getattr(self, "_frame_args", None)
        if args is not None and self._hidden_ticks != hide_ticks:
            self._build_frame(*args, hide_ticks=hide_ticks)

    def _clear_hover(self) -> None:
        self._hover_index = None
        if hasattr(self, "_hover_label"):
            self._hover_label.setText("")
        for it in self._hover_items:
            if it in self._view.items:
                self._view.removeItem(it)

    def _show_highlight(self, p) -> None:
        gl = self._gl
        if not self._hover_items:
            opts = _flat_gl_options()
            for size, color in ((17.0, (0.0, 0.0, 0.0, 0.85)), (11.0, (1.0, 0.15, 0.1, 1.0))):
                it = gl.GLScatterPlotItem(pos=np.zeros((1, 3), np.float32), color=color, size=size, pxMode=True)
                it.setGLOptions(opts)
                self._hover_items.append(it)
        for it in self._hover_items:
            it.setData(pos=np.asarray([p], dtype=np.float32))
            if it not in self._view.items:
                self._view.addItem(it)

    def view_angles(self) -> tuple[float, float]:
        """(elevation, azimuth) in degrees, the same convention matplotlib's `view_init` uses."""
        return float(self._view.opts["elevation"]), float(self._view.opts["azimuth"])

    # -- scene ---------------------------------------------------------------------

    def _clear(self, items: list) -> None:
        if items is self._data_items:
            self.overlay_items.clear()  # they go with the scene (the window re-adds them, 44.7)
        for it in items:
            self._view.removeItem(it)
        items.clear()

    def _build_frame(self, bounds: dict, labels: tuple[str, str, str], time_axes=(),
                     hide_ticks: str | None = None) -> None:
        gl = self._gl
        self._frame_args = (bounds, labels, tuple(time_axes))
        self._hidden_ticks = hide_ticks
        self._clear(self._frame_items)
        grid = gl.GLGridItem(color=(0, 0, 0, 60))
        grid.setSize(1, 1, 0)
        grid.setSpacing(0.2, 0.2, 0.2)
        grid.translate(0.5, 0.5, 0)
        self._frame_items.append(grid)
        axes = np.array([[0, 0, 0], [1, 0, 0], [0, 0, 0], [0, 1, 0], [0, 0, 0], [0, 0, 1]], dtype=np.float32)
        self._frame_items.append(gl.GLLinePlotItem(pos=axes, color=(0, 0, 0, 1), width=3, mode="lines", glOptions=_flat_gl_options()))
        font_color = QColor(40, 40, 40)
        tick_font, title_font = QFont(), QFont()
        tick_font.setPointSize(8)
        title_font.setPointSize(10)
        title_font.setBold(True)
        offset = {"x": (0, -0.07, 0), "y": (-0.12, 0, 0), "z": (-0.08, -0.08, 0)}
        for axis, pos, text in axis_tick_labels(bounds, time_axes):
            if axis == hide_ticks:
                continue
            p = {"x": (pos, 0, 0), "y": (0, pos, 0), "z": (0, 0, pos)}[axis]
            o = offset[axis]
            self._frame_items.append(
                gl.GLTextItem(pos=(p[0] + o[0], p[1] + o[1], p[2] + o[2]), text=text, color=font_color, font=tick_font)
            )
        for axis, label in zip("xyz", labels):
            # at the axis tips: text is drawn rightwards from its anchor, so a title there never
            # collides with the tick labels along the axis, whatever its length (25.3)
            tip = 1.24 if axis in time_axes else 1.06  # clock labels are wide: keep the title clear of them
            p = {"x": (tip, -0.03, 0), "y": (-0.03, tip, 0), "z": (-0.1, -0.1, 1.1)}[axis]
            self._frame_items.append(gl.GLTextItem(pos=p, text=label, color=QColor(0, 0, 0), font=title_font))
        for it in self._frame_items:
            self._view.addItem(it)

    def clear(self) -> None:
        self._clear(self._data_items)
        self._clear(self._frame_items)
        self._clear_hover()
        self.scene = {}
        self.orphan_points = np.empty((0, 3), dtype=np.float32)  # points drawn for faceless valued vertices (44.3)
        self.set_colorbar(None)

    def set_scatter(self, x, y, z, colors, bounds=None, labels=("X", "Y", "Z"), size: float = 4.0, times=None,
                    time_axes=()) -> None:
        """Scatter of real-valued x/y/z with (n, 4) RGBA `colors`. `bounds` ({"x": (lo, hi), ...})
        defaults to the data's own min/max. `times` (epoch seconds per point) adds a time line
        to the hover readout (25.3). `time_axes` (e.g. ("x",)) names the axes holding epoch
        seconds: their ticks and hover values are shown as local time (25.5)."""
        self._clear_hover()
        arrs = [np.asarray(a, dtype=np.float64) for a in (x, y, z)]
        if bounds is None:
            bounds = {k: ((float(a.min()), float(a.max())) if len(a) else (0.0, 1.0)) for k, a in zip("xyz", arrs)}
        pos = np.column_stack([normalize(a, *bounds[k]) for k, a in zip("xyz", arrs)]).astype(np.float32)
        rgba = np.asarray(colors, dtype=np.float32).reshape(-1, 4)
        self._clear(self._data_items)
        self._build_frame(bounds, labels, time_axes)
        item = self._gl.GLScatterPlotItem(pos=pos, color=rgba, size=size, pxMode=True)
        item.setGLOptions("translucent")  # the default "additive" blend turns white-background points invisible
        self._data_items.append(item)
        self._view.addItem(item)
        self.scene = {"kind": "scatter", "pos": pos, "real": np.column_stack(arrs), "colors": rgba, "bounds": bounds,
                      "labels": labels, "time_axes": tuple(time_axes), "time": None if times is None else np.asarray(times, dtype=np.float64)}

    def set_surface(self, x_axis, y_axis, z_grid, labels=("X", "Y", "Z"), axis_names=None, z_range=None,
                    extra=None) -> None:
        """Surface of a grid: `z_grid` is shaped (len(y_axis), len(x_axis)), the map convention
        (27.1). A NaN (empty, 27.10 / 44.3) grid point is not drawn: every square it touches is left
        out of the mesh, and it is no hover candidate. `axis_names` = ("X", "Y", "Z") titles
        (the map's axis names, 27.7); it overrides `labels`, default X/Y/Z (25.3). `z_range` =
        (lo, hi) fixes the Z normalization (and colours) instead of the grid's own min/max, so
        two surfaces can share one scale (27.9). `extra` = a (len(y), len(x)) array of strings (or a function `(iy, ix) -> str`) appended to
        each grid point's hover readout (the XY map: "count: n (filled)", 44.3)."""
        self._clear_hover()
        self.orphan_points = np.empty((0, 3), dtype=np.float32)  # test hook: the points drawn for orphans
        if axis_names is not None:
            labels = tuple(axis_names)
        xs, ys = np.asarray(x_axis, dtype=np.float64), np.asarray(y_axis, dtype=np.float64)
        z = np.asarray(z_grid, dtype=np.float64)
        if z.shape != (len(ys), len(xs)):
            raise ValueError(f"z_grid must be shaped (len(y), len(x)) = {(len(ys), len(xs))}, got {z.shape}")
        finite = z[np.isfinite(z)]
        zlo, zhi = (float(finite.min()), float(finite.max())) if finite.size else (0.0, 1.0)
        if z_range is not None:
            zlo, zhi = float(z_range[0]), float(z_range[1])
        bounds = {"x": (float(xs.min()), float(xs.max())), "y": (float(ys.min()), float(ys.max())), "z": (zlo, zhi)}
        zn = normalize(np.where(np.isfinite(z), z, zlo), zlo, zhi).T  # pyqtgraph wants (nx, ny)
        xn, yn = normalize(xs, *bounds["x"]), normalize(ys, *bounds["y"])
        colors = colors_for(zn.ravel(), 0.0, 1.0)  # (nx*ny, 4): MeshData wants flat per-vertex colours
        self._clear(self._data_items)
        self._build_frame(bounds, labels)
        item = self._gl.GLSurfacePlotItem(x=xn, y=yn, z=zn, colors=colors, shader="shaded", smooth=False)
        keep = np.isfinite(z).T  # (nx, ny), like zn
        orphans = np.empty(0, dtype=np.intp)
        if not keep.all():
            orphans = self._drop_empty_faces(item, keep)
        self._data_items.append(item)
        self._view.addItem(item)
        if len(orphans):  # valued points that lost every face are drawn as coloured points (44.3)
            gx0, gy0 = np.meshgrid(xn, yn, indexing="ij")
            opos = np.column_stack([gx0.ravel(), gy0.ravel(), zn.ravel()])[orphans].astype(np.float32)
            dots = self._gl.GLScatterPlotItem(pos=opos, color=colors[orphans], size=6.0, pxMode=True)
            dots.setGLOptions("translucent")
            self._data_items.append(dots)
            self._view.addItem(dots)
            self.orphan_points = opos
        # grid vertices are the hover candidates: (nx*ny, 3), real values straight from the table
        gx, gy = np.meshgrid(xn, yn, indexing="ij")
        rx, ry = np.meshgrid(xs, ys, indexing="ij")
        pos = np.column_stack([gx.ravel(), gy.ravel(), zn.ravel()]).astype(np.float32)
        real = np.column_stack([rx.ravel(), ry.ravel(), z.T.ravel()])
        if not keep.all():
            flat = keep.ravel()
            pos, real = pos[flat], real[flat]
        extras = extra_fn = vidx = None
        # vertex number = ix * ny + iy for each candidate: maps a picked point back to its cell (27.12)
        grid_index = np.arange(len(xs) * len(ys))
        if not keep.all():
            grid_index = grid_index[keep.ravel()]
        if callable(extra):  # `extra(iy, ix) -> str`, evaluated lazily for the hovered point only
            extra_fn = extra
            vidx = grid_index
        elif extra is not None:
            extras = np.asarray(extra, dtype=object).T.ravel()
            if not keep.all():
                extras = extras[keep.ravel()]
        self.scene = {"kind": "surface", "x": xn, "y": yn, "z": zn, "pos": pos, "real": real, "time": None,
                      "colors": colors, "bounds": bounds, "labels": labels, "time_axes": (), "extra": extras,
                      "extra_fn": extra_fn, "extra_index": vidx, "ny": len(ys),
                      "grid_index": grid_index}

    def add_points(self, x, y, z, color=(0.3, 0.3, 0.3, 0.75), size: float = 3.0) -> None:
        """Overlay measured points (real values) on the current scene, normalized with its bounds
        (the XY map's "show measured points", 44.3). Does nothing before a scene exists."""
        bounds = self.scene.get("bounds")
        if not bounds or len(x) == 0:
            return
        arrs = [np.asarray(a, dtype=np.float64) for a in (x, y, z)]
        pos = np.column_stack([normalize(a, *bounds[k]) for k, a in zip("xyz", arrs)]).astype(np.float32)
        item = self._gl.GLScatterPlotItem(pos=pos, color=color, size=size, pxMode=True)
        item.setGLOptions("translucent")
        self._data_items.append(item)
        self._view.addItem(item)

    def add_surface_overlay(self, x_axis, y_axis, z_grid, color=(0.85, 0.2, 0.2, 0.4)) -> bool:
        """A translucent single-colour surface of another map over the current scene (44.7). It is
        normalised with the scene's bounds, clipped to the X / Y range (the caller widens Z so that
        every value fits) and NaN (empty, 27.10) grid points drop the
        squares they touch. Returns whether anything was added."""
        bounds = self.scene.get("bounds")
        if not bounds:
            return False
        xs, ys = np.asarray(x_axis, dtype=np.float64), np.asarray(y_axis, dtype=np.float64)
        z = np.asarray(z_grid, dtype=np.float64)
        ix = np.flatnonzero((xs >= bounds["x"][0]) & (xs <= bounds["x"][1]))
        iy = np.flatnonzero((ys >= bounds["y"][0]) & (ys <= bounds["y"][1]))
        if len(ix) < 2 or len(iy) < 2:
            return False
        z = z[np.ix_(iy, ix)].copy()
        zlo, zhi = bounds["z"]  # the window widens the scene's Z range to hold the overlay (44.7)
        if not np.isfinite(z).any():
            return False
        xn, yn = normalize(xs[ix], *bounds["x"]), normalize(ys[iy], *bounds["y"])
        zn = normalize(np.where(np.isfinite(z), z, zlo), zlo, zhi).T  # pyqtgraph wants (nx, ny)
        colors = np.tile(np.asarray(color, dtype=np.float32), (zn.size, 1))
        item = self._gl.GLSurfacePlotItem(x=xn, y=yn, z=zn, colors=colors, smooth=False)
        item.setGLOptions("translucent")
        keep = np.isfinite(z).T
        if not keep.all():
            self._drop_empty_faces(item, keep)
        self._data_items.append(item)
        self.overlay_items.append(item)
        self._view.addItem(item)
        return True

    def clear_overlays(self) -> None:
        """Remove the overlay surfaces (the base scene stays)."""
        for it in self.overlay_items:
            if it in self._view.items:
                self._view.removeItem(it)
            if it in self._data_items:
                self._data_items.remove(it)
        self.overlay_items.clear()

    @staticmethod
    def _drop_empty_faces(item, keep: np.ndarray) -> np.ndarray:
        """Remove the mesh squares that touch a vertex with `keep == False` (shape (nx, ny)). pyqtgraph's
        surface numbers vertices `i*ny + j` and makes two triangles per square; GLSurfacePlotItem
        cannot draw NaN, and alpha 0 per vertex would fade the neighbours, so the faces go instead.
        Returns the flat indices of the valued vertices left without any face (`orphan_vertices`)."""
        nx, ny = keep.shape
        if nx < 2 or ny < 2:
            return np.empty(0, dtype=np.intp)
        md = item.opts["meshdata"]
        faces = md.faces()
        if faces is None or not len(faces):
            return np.empty(0, dtype=np.intp)
        if len(faces) == 2 * (nx - 1) * (ny - 1):
            # pyqtgraph: row i (of nx-1) holds the first triangles of its ny-1 squares, then the second
            # ones; a square goes as a whole when any of its four corners is empty (44.3)
            square_ok = keep[:-1, :-1] & keep[1:, :-1] & keep[:-1, 1:] & keep[1:, 1:]  # (nx-1, ny-1)
            good = np.concatenate([square_ok, square_ok], axis=1).ravel()
        else:  # an unexpected face layout: drop the triangles that use an empty vertex
            good = keep.ravel()[faces].all(axis=1)
        kept_faces = np.ascontiguousarray(faces[good])
        md.setFaces(kept_faces)
        item.meshDataChanged()
        return orphan_vertices(kept_faces, keep)

    # -- colour bar ----------------------------------------------------------------

    def set_colorbar(self, values: tuple[float, float] | None, time_ticks: bool = False) -> None:
        import pyqtgraph as pg

        if values is None:
            if self._colorbar_widget is not None:
                self._colorbar_widget.setVisible(False)
            return
        if self._colorbar_widget is None:
            self._colorbar_widget = pg.GraphicsLayoutWidget()
            self._colorbar_widget.setFixedWidth(130)
            self._colorbar = pg.ColorBarItem(values=(0, 1), colorMap=pg.colormap.get(_CMAP), interactive=False)
            self._colorbar_widget.addItem(self._colorbar, row=0, col=0)
            self._body.addWidget(self._colorbar_widget)
        lo, hi = values
        axis = self._colorbar.axis
        if time_ticks:
            span = hi - lo
            axis.tickStrings = lambda vals, scale, spacing: [format_time_tick(v, span) for v in vals]
            axis.setWidth(110)
        else:
            axis.tickStrings = type(axis).tickStrings.__get__(axis)
            axis.setWidth(45)
        axis.picture = None
        axis.update()
        self._colorbar.setLevels((lo, hi if hi > lo else lo + 1.0))
        self._colorbar_widget.setVisible(True)


# ---------------------------------------------------------------------------
# Image export
# ---------------------------------------------------------------------------


def build_xyz_figure(r: Xyz3DResult, stamp_lines, size_px, elev: float, azim: float):
    """matplotlib `mplot3d` scatter of an Xyz3DResult seen from the GL view's current angles
    (25.3). The scatter is rasterized so SVG/PDF stay small."""
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure
    from matplotlib.ticker import FuncFormatter

    with _font_context():
        width, height = max(int(size_px[0]), 200), max(int(size_px[1]), 150)
        fig = Figure(figsize=(width / _FIG_DPI, height / _FIG_DPI), dpi=_FIG_DPI, facecolor="white", layout="constrained")
        FigureCanvasAgg(fig)
        n_stamp = max(len(stamp_lines), 1)
        stamp_h = (n_stamp * 7 * 1.25 + 4) / 72 / (height / _FIG_DPI)
        fig.get_layout_engine().set(rect=(0, stamp_h, 1, 1 - stamp_h))
        ax = fig.add_subplot(projection="3d")
        lo, hi = r.color_range
        single = r.color == "single"
        if single:
            sc = ax.scatter(r.x, r.y, r.z, color=_SINGLE_COLOR, s=2, rasterized=True)
        else:
            sc = ax.scatter(r.x, r.y, r.z, c=r.c, s=2, cmap=_CMAP, vmin=lo, vmax=hi, rasterized=True)
        ax.view_init(elev=elev, azim=azim)
        ax.set_box_aspect((1, 1, 1))
        ax.set_xlabel(r.x_name)
        ax.set_ylabel(r.y_name)
        ax.set_zlabel(r.z_name)
        for axis_obj, name, key in ((ax.xaxis, r.x_name, "x"), (ax.yaxis, r.y_name, "y"), (ax.zaxis, r.z_name, "z")):
            if name == r.time_name and key in r.bounds:  # a time axis reads as local time (25.5)
                b_lo, b_hi = r.bounds[key]
                axis_obj.set_major_formatter(
                    FuncFormatter(lambda v, _pos, span=b_hi - b_lo: format_time_tick(v, span))
                )
        if not single:
            c_is_time = r.color == "time" or (r.color == "z" and r.z_name == r.time_name)
            cb = fig.colorbar(sc, ax=ax, shrink=0.7, pad=0.1,
                              label=r.z_name if r.color == "z" else tr("xy.color_time"))
            if c_is_time:
                span = hi - lo
                cb.formatter = FuncFormatter(lambda v, _pos, span=span: format_time_tick(v, span))
                cb.update_ticks()
        if stamp_lines:
            fig.text(0.995, 0.004, "\n".join(stamp_lines), ha="right", va="bottom", fontsize=7, color="#666666")
    return fig
