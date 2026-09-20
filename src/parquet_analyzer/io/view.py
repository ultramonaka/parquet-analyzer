from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .. import config


@dataclass
class SourceDef:
    parquet_path: str
    path_type: str = "relative"  # "relative" | "absolute"


@dataclass
class DerivedVariableDef:
    name: str
    expression: str


@dataclass
class SeriesDef:
    variable: str
    color: str


@dataclass
class PlotDef:
    plot_id: str
    series: list[SeriesDef]
    y_axis_range: tuple[float | None, float | None] = (None, None)


@dataclass
class View:
    """A saved screen layout (specification.md 5.8, detailed_specification.md 8章)."""

    view_name: str
    source: SourceDef
    plots: list[PlotDef]
    derived_variables: list[DerivedVariableDef] = field(default_factory=list)
    x_axis_range: tuple[float, float] | None = None
    downsample_enabled: bool = True
    schema_version: str = "1"


def view_path(view_name: str) -> Path:
    # `Path.__truediv__` treats a right-hand operand starting with "/" as an absolute
    # path, *discarding* the left side entirely — so an unvalidated view_name like
    # "/tmp/evil" would silently write outside PARQUET_ANALYZER_DATA_DIR altogether.
    # Requiring the name to survive a round-trip through Path(...).name rules that out,
    # along with "..", other embedded separators, and empty/blank names.
    if not view_name.strip() or view_name != Path(view_name).name:
        raise ValueError(f"invalid view name: {view_name!r}")
    return config.PARQUET_ANALYZER_DATA_DIR / f"{view_name}.json"


def list_views() -> list[str]:
    data_dir = config.PARQUET_ANALYZER_DATA_DIR
    if not data_dir.exists():
        return []
    return sorted(p.stem for p in data_dir.glob("*.json"))


def save_view(view: View) -> Path:
    path = view_path(view.view_name)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": view.schema_version,
        "view_name": view.view_name,
        "source": asdict(view.source),
        "derived_variables": [asdict(v) for v in view.derived_variables],
        "plots": [
            {
                "plot_id": p.plot_id,
                "series": [asdict(s) for s in p.series],
                "y_axis_range": list(p.y_axis_range),
            }
            for p in view.plots
        ],
        "x_axis_range": list(view.x_axis_range) if view.x_axis_range else None,
        "downsample": {"enabled": view.downsample_enabled},
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def resolve_source_path(view: View) -> Path:
    if view.source.path_type == "absolute":
        return Path(view.source.parquet_path)
    return (view_path(view.view_name).parent / view.source.parquet_path).resolve()


def load_view(view_name: str) -> View:
    path = view_path(view_name)
    payload = json.loads(path.read_text(encoding="utf-8"))
    source = SourceDef(**payload["source"])
    derived = [DerivedVariableDef(**d) for d in payload.get("derived_variables", [])]
    plots = [
        PlotDef(
            plot_id=p["plot_id"],
            series=[SeriesDef(**s) for s in p["series"]],
            y_axis_range=tuple(p.get("y_axis_range", [None, None])),
        )
        for p in payload["plots"]
    ]
    x_range = payload.get("x_axis_range")
    return View(
        view_name=payload["view_name"],
        source=source,
        plots=plots,
        derived_variables=derived,
        x_axis_range=tuple(x_range) if x_range else None,
        downsample_enabled=payload.get("downsample", {}).get("enabled", True),
        schema_version=payload.get("schema_version", "1"),
    )
