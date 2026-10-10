from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import math
from pathlib import Path
from typing import Iterable

import gdstk
from ..models import Layer, SpecError
from ..preview import exact_preview_points


@dataclass(frozen=True)
class PortCandidate:
    candidate_id: str
    layer: Layer
    center_um: tuple[float, float]
    width_um: float
    orientation_deg: int
    confidence: float
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class DeviceScan:
    device_id: str
    source_path: str
    relative_path: str
    top_cell: str
    source_sha256: str
    source_layers: tuple[Layer, ...]
    bbox_um: tuple[float, float, float, float]
    polygon_count: int
    hierarchy_cell_count: int
    candidates: tuple[PortCandidate, ...]
    preview_polygons: dict[str, list[list[list[float]]]]

    def to_dict(self, *, include_preview: bool = True) -> dict[str, object]:
        value = asdict(self)
        value["source_layers"] = [list(layer) for layer in self.source_layers]
        value["candidates"] = [
            {
                **asdict(candidate),
                "layer": list(candidate.layer),
                "center_um": list(candidate.center_um),
                "reasons": list(candidate.reasons),
            }
            for candidate in self.candidates
        ]
        if not include_preview:
            value.pop("preview_polygons")
        return value


def _axis_facets(ring: list[tuple[float, float]], tolerance: float = 1e-9):
    raw: list[tuple[str, float, float, float]] = []
    for start, end in zip(ring, ring[1:]):
        x1, y1 = start
        x2, y2 = end
        if math.isclose(x1, x2, abs_tol=tolerance) and not math.isclose(
            y1, y2, abs_tol=tolerance
        ):
            raw.append(("v", x1, y1, y2))
        elif math.isclose(y1, y2, abs_tol=tolerance) and not math.isclose(
            x1, x2, abs_tol=tolerance
        ):
            raw.append(("h", y1, x1, x2))

    merged: list[tuple[str, float, float, float]] = []
    for facet in raw:
        if not merged:
            merged.append(facet)
            continue
        previous = merged[-1]
        if (
            facet[0] == previous[0]
            and math.isclose(facet[1], previous[1], abs_tol=tolerance)
            and math.isclose(facet[2], previous[3], abs_tol=tolerance)
            and (facet[3] - facet[2]) * (previous[3] - previous[2]) > 0
        ):
            merged[-1] = (previous[0], previous[1], previous[2], facet[3])
        else:
            merged.append(facet)
    return merged


def _candidate_for_facet(
    facet: tuple[str, float, float, float],
    layer: Layer,
    layer_bbox: tuple[float, float, float, float],
) -> PortCandidate | None:
    axis, fixed, start, end = facet
    width = abs(end - start)
    if width < 0.02:
        return None
    moving = (start + end) / 2.0
    center = (fixed, moving) if axis == "v" else (moving, fixed)
    if axis == "v":
        if math.isclose(center[0], layer_bbox[2], abs_tol=0.002):
            orientation = 0
        elif math.isclose(center[0], layer_bbox[0], abs_tol=0.002):
            orientation = 180
        else:
            return None
    else:
        if math.isclose(center[1], layer_bbox[3], abs_tol=0.002):
            orientation = 90
        elif math.isclose(center[1], layer_bbox[1], abs_tol=0.002):
            orientation = 270
        else:
            return None

    xmin, ymin, xmax, ymax = layer_bbox
    extreme = (
        (orientation == 0 and math.isclose(center[0], xmax, abs_tol=0.002))
        or (orientation == 180 and math.isclose(center[0], xmin, abs_tol=0.002))
        or (orientation == 90 and math.isclose(center[1], ymax, abs_tol=0.002))
        or (orientation == 270 and math.isclose(center[1], ymin, abs_tol=0.002))
    )
    span = max(xmax - xmin, ymax - ymin, 0.001)
    narrow = width <= max(5.0, span * 0.08)
    confidence = 0.30 + (0.35 if extreme else 0.0) + (0.25 if narrow else 0.0)
    reasons = ["axis_aligned_boundary_facet"]
    if extreme:
        reasons.append("on_layer_outer_extent")
    if narrow:
        reasons.append("narrow_relative_to_device")
    digest_source = (
        f"{layer[0]}/{layer[1]}/{center[0]:.6f}/{center[1]:.6f}/"
        f"{width:.6f}/{orientation}"
    )
    return PortCandidate(
        candidate_id="p_" + hashlib.sha1(digest_source.encode("ascii")).hexdigest()[:12],
        layer=layer,
        center_um=(round(center[0], 6), round(center[1], 6)),
        width_um=round(width, 6),
        orientation_deg=orientation,
        confidence=round(min(confidence, 0.99), 3),
        reasons=tuple(reasons),
    )


def _cell_polygons(cell: object) -> list[object]:
    polygons = cell.get_polygons(
        apply_repetitions=True,
        include_paths=True,
        depth=None,
    )
    # Some layout editors encode context records as zero-area polygons.  Such
    # records are not physical geometry and must not create a device root.
    valid = []
    for polygon in polygons:
        area = float(polygon.area())
        if math.isfinite(area) and abs(area) > 1e-12:
            valid.append(polygon)
    return valid


def _geometry_top_cells(library: gdstk.Library) -> list[tuple[object, list[object]]]:
    """Return top-level cells that contribute polygon geometry.

    Metadata-only roots written by layout editors are intentionally excluded
    by content, not by cell name.  If several roots contain geometry, each is
    a separate device candidate during folder scanning.
    """
    result: list[tuple[object, list[object]]] = []
    for cell in library.top_level():
        polygons = _cell_polygons(cell)
        if polygons:
            result.append((cell, polygons))
    return result


def _scan_selected_cell(
    source: Path,
    library: gdstk.Library,
    selected: object,
    polygons: list[object],
    *,
    root: str | Path | None,
) -> DeviceScan:
    if not polygons:
        raise SpecError(f"{source.name}:{selected.name} contains no polygon geometry")
    by_layer: dict[Layer, list[object]] = {}
    preview: dict[str, list[list[list[float]]]] = {}
    all_x: list[float] = []
    all_y: list[float] = []
    for polygon in polygons:
        layer = int(polygon.layer), int(polygon.datatype)
        by_layer.setdefault(layer, []).append(polygon)
        preview.setdefault(f"{layer[0]}/{layer[1]}", []).append(
            exact_preview_points(polygon.points)
        )
        all_x.extend(float(point[0]) for point in polygon.points)
        all_y.extend(float(point[1]) for point in polygon.points)

    candidates: list[PortCandidate] = []
    seen: set[str] = set()
    for layer, shapes in sorted(by_layer.items()):
        xs = [float(point[0]) for shape in shapes for point in shape.points]
        ys = [float(point[1]) for shape in shapes for point in shape.points]
        layer_bbox = min(xs), min(ys), max(xs), max(ys)
        for shape in shapes:
            ring = [tuple(float(value) for value in point) for point in shape.points]
            if ring and ring[0] != ring[-1]:
                ring.append(ring[0])
            for facet in _axis_facets(ring):
                candidate = _candidate_for_facet(facet, layer, layer_bbox)
                if candidate is not None and candidate.candidate_id not in seen:
                    seen.add(candidate.candidate_id)
                    candidates.append(candidate)
    candidates.sort(
        key=lambda item: (
            -item.confidence,
            item.layer,
            item.orientation_deg,
            item.center_um,
        )
    )
    relative = source.name
    if root is not None:
        try:
            relative = source.relative_to(Path(root).resolve()).as_posix()
        except ValueError:
            pass
    identity = f"{relative}::{selected.name}"
    device_id = "d_" + hashlib.sha1(identity.encode("utf-8")).hexdigest()[:12]
    return DeviceScan(
        device_id=device_id,
        source_path=str(source),
        relative_path=relative,
        top_cell=selected.name,
        source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        source_layers=tuple(sorted(by_layer)),
        bbox_um=(min(all_x), min(all_y), max(all_x), max(all_y)),
        polygon_count=len(polygons),
        hierarchy_cell_count=len(library.cells),
        candidates=tuple(candidates),
        preview_polygons=preview,
    )


def scan_gds_file(
    path: str | Path,
    *,
    root: str | Path | None = None,
    top_cell: str | None = None,
) -> DeviceScan:
    source = Path(path).resolve()
    if not source.is_file() or source.suffix.lower() != ".gds":
        raise SpecError(f"not a readable GDS file: {source}")
    library = gdstk.read_gds(source)
    if top_cell is None:
        geometry_tops = _geometry_top_cells(library)
        if not geometry_tops:
            raise SpecError(f"{source.name} contains no top-level polygon geometry")
        if len(geometry_tops) != 1:
            names = ", ".join(cell.name for cell, _ in geometry_tops)
            raise SpecError(
                f"{source.name} has {len(geometry_tops)} geometry-bearing top-level "
                f"cells ({names}); select one explicitly"
            )
        selected, polygons = geometry_tops[0]
    else:
        matches = [cell for cell in library.cells if cell.name == top_cell]
        if len(matches) != 1:
            raise SpecError(f"top cell {top_cell!r} not found uniquely in {source.name}")
        selected = matches[0]
        polygons = _cell_polygons(selected)
    return _scan_selected_cell(
        source,
        library,
        selected,
        polygons,
        root=root,
    )


def scan_gds_folder(folder: str | Path) -> tuple[DeviceScan, ...]:
    root = Path(folder).resolve()
    if not root.is_dir():
        raise SpecError(f"GDS source folder does not exist: {root}")
    results: list[DeviceScan] = []
    for path in sorted(root.rglob("*.gds")):
        source = path.resolve()
        library = gdstk.read_gds(source)
        geometry_tops = _geometry_top_cells(library)
        if not geometry_tops:
            raise SpecError(f"{source.name} contains no top-level polygon geometry")
        for selected, polygons in geometry_tops:
            results.append(
                _scan_selected_cell(
                    source,
                    library,
                    selected,
                    polygons,
                    root=root,
                )
            )
    if not results:
        raise SpecError(f"no .gds files found under {root}")
    return tuple(results)
