from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
from typing import Mapping, Sequence
import uuid

import gdsfactory as gf
import gdstk
from shapely.geometry import LineString, Point, Polygon
from shapely.ops import unary_union

from ..models import Layer, SpecError
from .scanner import DeviceScan


_TEMPLATE_ID = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")
_PORT_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_IMPORTED_COMPONENTS: dict[
    tuple[str, str, str],
    tuple[gf.Component, str, dict[str, Layer], dict[str, str], tuple[Layer, ...], str],
] = {}


def clear_import_cache() -> None:
    """Drop components tied to the previous gdsfactory layout after gf.clear_cache()."""
    _IMPORTED_COMPONENTS.clear()


def _read_geometry(
    path: Path,
    top_cell: str | None,
    geometry_layers: set[Layer] | None = None,
) -> tuple[set[Layer], dict[Layer, object]]:
    library = gdstk.read_gds(path)
    if top_cell is None:
        tops = library.top_level()
        if len(tops) != 1:
            raise SpecError(f"{path.name} requires an explicit top_cell")
        selected = tops[0]
    else:
        matches = [cell for cell in library.cells if cell.name == top_cell]
        if len(matches) != 1:
            raise SpecError(f"top cell {top_cell!r} not found uniquely in {path.name}")
        selected = matches[0]
    polygons = selected.get_polygons(apply_repetitions=True, include_paths=True, depth=None)
    layers = {(int(polygon.layer), int(polygon.datatype)) for polygon in polygons}
    requested = layers if geometry_layers is None else geometry_layers
    geometries = {
        layer: unary_union(
            [
                Polygon(polygon.points)
                for polygon in polygons
                if (int(polygon.layer), int(polygon.datatype)) == layer
            ]
        )
        for layer in requested
        if layer in layers
    }
    return layers, geometries


def validate_port_geometry(
    template_id: str,
    port_name: str,
    port: Mapping[str, object],
    geometries: Mapping[Layer, object],
) -> None:
    layer = tuple(int(value) for value in port["layer"])
    if layer not in geometries:
        raise SpecError(f"{template_id}.{port_name}: layer {layer} is absent from source GDS")
    center = tuple(float(value) for value in port["center_um"])
    width = float(port["width_um"])
    orientation_deg = float(port["orientation_deg"]) % 360.0
    if width <= 0 or not math.isfinite(width):
        raise SpecError(f"{template_id}.{port_name}: width must be positive and finite")
    if orientation_deg not in {0.0, 90.0, 180.0, 270.0}:
        raise SpecError(f"{template_id}.{port_name}: orientation must be a multiple of 90 degrees")
    geometry = geometries[layer]
    if Point(center).distance(geometry.boundary) > 0.004:
        raise SpecError(
            f"{template_id}.{port_name}: center {center} is not on the source polygon boundary"
        )
    orientation = math.radians(orientation_deg)
    inward = (-math.cos(orientation), -math.sin(orientation))
    normal = (-math.sin(orientation), math.cos(orientation))
    probe_center = (center[0] + 0.003 * inward[0], center[1] + 0.003 * inward[1])
    half_width = max(width / 2.0 - 0.003, width * 0.45)
    probe = LineString(
        [
            (probe_center[0] - half_width * normal[0], probe_center[1] - half_width * normal[1]),
            (probe_center[0] + half_width * normal[0], probe_center[1] + half_width * normal[1]),
        ]
    )
    if not geometry.buffer(0.002).covers(probe):
        raise SpecError(
            f"{template_id}.{port_name}: width {width} um is not covered inside source geometry"
        )


def load_registry_catalog(catalog_path: str | Path) -> dict[str, object]:
    path = Path(catalog_path).resolve()
    if not path.is_file():
        raise SpecError(f"device registry catalog is missing: {path}")
    catalog = json.loads(path.read_text(encoding="utf-8"))
    if str(catalog.get("catalog_version")) != "1.0.0":
        raise SpecError(f"unsupported registry catalog version in {path}")
    if not isinstance(catalog.get("templates"), dict):
        raise SpecError(f"registry templates must be an object in {path}")
    return catalog


class DeviceRegistry:
    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.catalog_path = self.root / "catalog.json"
        self.templates_path = self.root / "templates"

    def initialize(self, name: str = "black_box_device_library") -> dict[str, object]:
        self.root.mkdir(parents=True, exist_ok=True)
        self.templates_path.mkdir(parents=True, exist_ok=True)
        if self.catalog_path.exists():
            return load_registry_catalog(self.catalog_path)
        catalog: dict[str, object] = {
            "catalog_version": "1.0.0",
            "registry_name": name,
            "registry_revision": 0,
            "port_source": "geometry_candidates_human_confirmed",
            "templates": {},
            "versions": {},
            "history": [],
        }
        self._write(catalog)
        return catalog

    def _write(self, catalog: Mapping[str, object]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        temporary = self.root / f".{uuid.uuid4().hex}.catalog.json"
        temporary.write_text(
            json.dumps(dict(catalog), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        os.replace(temporary, self.catalog_path)

    def register(
        self,
        scan: DeviceScan,
        *,
        template_id: str,
        family: str,
        ports: Sequence[Mapping[str, object]],
        replace_existing: bool = False,
    ) -> dict[str, object]:
        if not _TEMPLATE_ID.fullmatch(template_id):
            raise SpecError(f"invalid template_id {template_id!r}")
        family = str(family).strip()
        if not family:
            raise SpecError("family cannot be empty")
        if not ports:
            raise SpecError("at least one human-confirmed port is required")
        catalog = self.initialize()
        templates = dict(catalog["templates"])
        previous = templates.get(template_id)
        if previous is not None and not replace_existing:
            raise SpecError(
                f"template_id {template_id!r} is already registered; "
                "explicit replacement confirmation is required"
            )
        source = Path(scan.source_path).resolve()
        actual_hash = hashlib.sha256(source.read_bytes()).hexdigest()
        if actual_hash != scan.source_sha256:
            raise SpecError(f"source GDS changed after scan: {source}")
        requested_layers = {
            tuple(int(value) for value in raw["layer"])
            for raw in ports
        }
        layers, geometries = _read_geometry(source, scan.top_cell, requested_layers)
        if layers != set(scan.source_layers):
            raise SpecError(f"source GDS layers changed after scan: {source}")

        port_records: dict[str, dict[str, object]] = {}
        for index, raw in enumerate(ports):
            name = str(raw.get("name", ""))
            if not _PORT_NAME.fullmatch(name):
                raise SpecError(f"ports[{index}].name is not a valid identifier: {name!r}")
            if name in port_records:
                raise SpecError(f"duplicate port name {name!r}")
            record = {
                "center_um": [float(value) for value in raw["center_um"]],
                "width_um": float(raw["width_um"]),
                "orientation_deg": int(raw["orientation_deg"]) % 360,
                "layer": [int(value) for value in raw["layer"]],
                "route_profile": str(
                    raw.get("route_profile", f"layer_{raw['layer'][0]}_{raw['layer'][1]}")
                ),
                "confirmation": "human",
                "candidate_id": raw.get("candidate_id"),
            }
            validate_port_geometry(template_id, name, record, geometries)
            port_records[name] = record

        digest = actual_hash[:12]
        safe_suffix = source.suffix.lower()
        destination_name = f"{template_id}__{digest}{safe_suffix}"
        destination = self.templates_path / destination_name
        if not destination.exists():
            shutil.copy2(source, destination)
        if hashlib.sha256(destination.read_bytes()).hexdigest() != actual_hash:
            raise SpecError(f"registered template copy failed hash verification: {destination}")

        revision = int(catalog.get("registry_revision", 0)) + 1
        record = {
            "family": family,
            "filename": destination_name,
            "top_cell": scan.top_cell,
            "sha256": actual_hash,
            "source_layers": [list(layer) for layer in sorted(layers)],
            "ports": port_records,
            "onboarding": {
                "relative_source": scan.relative_path,
                "source_polygon_count": scan.polygon_count,
                "source_hierarchy_cell_count": scan.hierarchy_cell_count,
                "candidate_count": len(scan.candidates),
                "confirmed_at_utc": datetime.now(timezone.utc).isoformat(),
            },
            "registered_revision": revision,
        }
        templates[template_id] = record
        history = list(catalog.get("history", []))
        if previous is not None:
            versions = dict(catalog.get("versions", {}))
            archived = list(versions.get(template_id, []))
            archived.append(
                {
                    "replaced_at_revision": revision,
                    "record": previous,
                }
            )
            versions[template_id] = archived
            catalog["versions"] = versions
        history.append(
            {
                "revision": revision,
                "template_id": template_id,
                "action": "updated" if previous is not None else "registered",
                "sha256": actual_hash,
                "previous_sha256": (
                    dict(previous).get("sha256") if previous is not None else None
                ),
                "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            }
        )
        catalog["templates"] = templates
        catalog["registry_revision"] = revision
        catalog["history"] = history
        self._write(catalog)
        return record


def import_registered_template(
    catalog_path: str | Path,
    template_id: str,
) -> tuple[gf.Component, str, dict[str, Layer], dict[str, str], tuple[Layer, ...], str]:
    path = Path(catalog_path).resolve()
    catalog = load_registry_catalog(path)
    templates = dict(catalog["templates"])
    if template_id not in templates:
        raise SpecError(f"unknown registered template {template_id!r} in {path}")
    record = dict(templates[template_id])
    source = path.parent / "templates" / str(record["filename"])
    if not source.is_file():
        raise SpecError(f"registered template file is missing: {source}")
    actual_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    if actual_hash != record["sha256"]:
        raise SpecError(f"registered template {template_id!r} failed SHA256 verification")
    cache_key = (str(path), template_id, actual_hash)
    cached = _IMPORTED_COMPONENTS.get(cache_key)
    if cached is not None:
        return cached
    top_cell = record.get("top_cell")
    requested_layers = {
        tuple(int(value) for value in raw["layer"])
        for raw in dict(record["ports"]).values()
    }
    actual_layers, geometries = _read_geometry(
        source,
        str(top_cell) if top_cell else None,
        requested_layers,
    )
    expected_layers = {tuple(int(value) for value in layer) for layer in record["source_layers"]}
    if actual_layers != expected_layers:
        raise SpecError(
            f"registered template {template_id!r} layer mismatch; "
            f"expected={sorted(expected_layers)}, actual={sorted(actual_layers)}"
        )
    component = gf.import_gds(source, cellname=str(top_cell) if top_cell else None)
    component.name = ("REG_" + hashlib.sha1(
        (str(path) + "::" + template_id + "::" + actual_hash).encode("utf-8")
    ).hexdigest()[:24])[:32]
    port_layers: dict[str, Layer] = {}
    port_profiles: dict[str, str] = {}
    for port_name, raw in dict(record["ports"]).items():
        port = dict(raw)
        validate_port_geometry(template_id, port_name, port, geometries)
        layer = tuple(int(value) for value in port["layer"])
        component.add_port(
            name=port_name,
            center=tuple(float(value) for value in port["center_um"]),
            width=float(port["width_um"]),
            orientation=float(port["orientation_deg"]),
            layer=layer,
        )
        port_layers[port_name] = layer
        port_profiles[port_name] = str(port.get("route_profile", ""))
    result = (
        component,
        str(record["family"]),
        port_layers,
        port_profiles,
        tuple(sorted(expected_layers)),
        str(source),
    )
    _IMPORTED_COMPONENTS[cache_key] = result
    return result
