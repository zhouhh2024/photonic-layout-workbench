from __future__ import annotations

from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
from pathlib import Path
import threading
import uuid
import webbrowser
from urllib.parse import parse_qs, urlparse

from ..assembly import Assembly, DeviceLibrary
from ..library import build_device
from ..models import SpecError, TechnologySpec
from ..onboarding.registry import load_registry_catalog
from ..preview import exact_preview_points
from ..onboarding.server import (
    OnboardingSession,
    _PAGE as ONBOARDING_PAGE,
    _device_payload,
    _summary,
)


_PARAMETRIC_LIBRARY: tuple[dict[str, object], ...] = (
    {
        "key": "parametric:straight",
        "kind": "parametric",
        "component": "straight",
        "title": "Straight waveguide",
        "family": "straight_waveguide",
        "parameters": {"material": "si", "length_um": 20.0, "width_um": 0.5},
        "fields": [
            {"name": "material", "type": "choice", "options": ["si", "sin"]},
            {"name": "length_um", "type": "number", "min": 0.001, "step": 1.0},
            {"name": "width_um", "type": "number", "min": 0.001, "step": 0.1},
        ],
    },
    {
        "key": "parametric:bend_euler",
        "kind": "parametric",
        "component": "bend_euler",
        "title": "Euler bend",
        "family": "bend",
        "parameters": {
            "material": "si",
            "radius_um": 10.0,
            "angle_deg": 90.0,
            "width_um": 0.5,
        },
        "fields": [
            {"name": "material", "type": "choice", "options": ["si", "sin"]},
            {"name": "radius_um", "type": "number", "min": 0.1, "step": 1.0},
            {"name": "angle_deg", "type": "number", "step": 90.0},
            {"name": "width_um", "type": "number", "min": 0.001, "step": 0.1},
        ],
    },
    {
        "key": "parametric:s_bend",
        "kind": "parametric",
        "component": "s_bend",
        "title": "S bend",
        "family": "s_bend",
        "parameters": {
            "material": "si",
            "length_um": 30.0,
            "offset_um": 5.0,
            "width_um": 0.5,
            "radius_um": 10.0,
        },
        "fields": [
            {"name": "material", "type": "choice", "options": ["si", "sin"]},
            {"name": "length_um", "type": "number", "min": 0.001, "step": 1.0},
            {"name": "offset_um", "type": "number", "step": 1.0},
            {"name": "width_um", "type": "number", "min": 0.001, "step": 0.1},
            {"name": "radius_um", "type": "number", "min": 0.1, "step": 1.0},
        ],
    },
    {
        "key": "parametric:taper",
        "kind": "parametric",
        "component": "taper",
        "title": "Taper",
        "family": "taper",
        "parameters": {
            "material": "si",
            "length_um": 12.0,
            "width1_um": 0.5,
            "width2_um": 1.0,
        },
        "fields": [
            {"name": "material", "type": "choice", "options": ["si", "sin"]},
            {"name": "length_um", "type": "number", "min": 0.001, "step": 1.0},
            {"name": "width1_um", "type": "number", "min": 0.001, "step": 0.1},
            {"name": "width2_um", "type": "number", "min": 0.001, "step": 0.1},
        ],
    },
    {
        "key": "parametric:mmi_1x2",
        "kind": "parametric",
        "component": "mmi_1x2",
        "title": "MMI 1×2",
        "family": "mmi_1x2",
        "parameters": {
            "material": "si",
            "port_width_um": 0.5,
            "width_taper_um": 1.0,
            "length_taper_um": 8.0,
            "length_mmi_um": 8.0,
            "width_mmi_um": 3.2,
            "gap_mmi_um": 0.4,
        },
        "fields": [
            {"name": "material", "type": "choice", "options": ["si", "sin"]},
            {"name": "port_width_um", "type": "number", "min": 0.001, "step": 0.1},
            {"name": "width_taper_um", "type": "number", "min": 0.001, "step": 0.1},
            {"name": "length_taper_um", "type": "number", "min": 0.001, "step": 1.0},
            {"name": "length_mmi_um", "type": "number", "min": 0.001, "step": 1.0},
            {"name": "width_mmi_um", "type": "number", "min": 0.001, "step": 0.1},
            {"name": "gap_mmi_um", "type": "number", "min": 0.0, "step": 0.1},
        ],
    },
    {
        "key": "parametric:mmi_2x2",
        "kind": "parametric",
        "component": "mmi_2x2",
        "title": "MMI 2×2",
        "family": "mmi_2x2",
        "parameters": {
            "material": "si",
            "port_width_um": 0.5,
            "width_taper_um": 1.0,
            "length_taper_um": 8.0,
            "length_mmi_um": 8.0,
            "width_mmi_um": 3.2,
            "gap_mmi_um": 0.4,
        },
        "fields": [
            {"name": "material", "type": "choice", "options": ["si", "sin"]},
            {"name": "port_width_um", "type": "number", "min": 0.001, "step": 0.1},
            {"name": "width_taper_um", "type": "number", "min": 0.001, "step": 0.1},
            {"name": "length_taper_um", "type": "number", "min": 0.001, "step": 1.0},
            {"name": "length_mmi_um", "type": "number", "min": 0.001, "step": 1.0},
            {"name": "width_mmi_um", "type": "number", "min": 0.001, "step": 0.1},
            {"name": "gap_mmi_um", "type": "number", "min": 0.0, "step": 0.1},
        ],
    },
    {
        "key": "parametric:directional_coupler",
        "kind": "parametric",
        "component": "directional_coupler",
        "title": "Directional coupler",
        "family": "directional_coupler",
        "parameters": {
            "material": "si",
            "gap_um": 0.3,
            "coupling_length_um": 15.0,
            "dy_um": 6.0,
            "dx_um": 10.0,
            "width_um": 0.5,
            "radius_um": 10.0,
        },
        "fields": [
            {"name": "material", "type": "choice", "options": ["si", "sin"]},
            {"name": "gap_um", "type": "number", "min": 0.0, "step": 0.05},
            {"name": "coupling_length_um", "type": "number", "min": 0.001, "step": 1.0},
            {"name": "dy_um", "type": "number", "min": 0.001, "step": 1.0},
            {"name": "dx_um", "type": "number", "min": 0.001, "step": 1.0},
            {"name": "width_um", "type": "number", "min": 0.001, "step": 0.1},
            {"name": "radius_um", "type": "number", "min": 0.1, "step": 1.0},
        ],
    },
    {
        "key": "parametric:ring_single",
        "kind": "parametric",
        "component": "ring_single",
        "title": "Single ring",
        "family": "ring_resonator",
        "parameters": {
            "material": "si",
            "gap_um": 0.2,
            "radius_um": 10.0,
            "length_x_um": 4.0,
            "length_y_um": 0.6,
            "width_um": 0.5,
        },
        "fields": [
            {"name": "material", "type": "choice", "options": ["si", "sin"]},
            {"name": "gap_um", "type": "number", "min": 0.0, "step": 0.05},
            {"name": "radius_um", "type": "number", "min": 0.1, "step": 1.0},
            {"name": "length_x_um", "type": "number", "min": 0.0, "step": 1.0},
            {"name": "length_y_um", "type": "number", "min": 0.0, "step": 0.1},
            {"name": "width_um", "type": "number", "min": 0.001, "step": 0.1},
        ],
    },
)


def build_editor_library(
    user_catalog: str | Path | None = None,
) -> list[dict[str, object]]:
    entries = [json.loads(json.dumps(item)) for item in _PARAMETRIC_LIBRARY]
    catalogs: list[tuple[str, Path]] = []
    if user_catalog is not None and Path(user_catalog).is_file():
        catalogs.append(("user", Path(user_catalog).resolve()))
    for source, catalog_value in catalogs:
        catalog = load_registry_catalog(catalog_value)
        for template_id, raw in sorted(dict(catalog["templates"]).items()):
            record = dict(raw)
            entries.append(
                {
                    "key": f"{source}:{template_id}",
                    "kind": "registered",
                    "component": "registered_gds",
                    "title": template_id,
                    "family": record.get("family", "unclassified_device"),
                    "parameters": {
                        "catalog_path": str(catalog_value),
                        "template_id": template_id,
                    },
                    "fields": [],
                    "source": source,
                    "source_layers": record.get("source_layers", []),
                    "ports": list(dict(record.get("ports", {}))),
                }
            )
    return entries


def build_device_preview(
    descriptor: dict[str, object],
    parameters: dict[str, object] | None = None,
    technology: TechnologySpec | None = None,
) -> dict[str, object]:
    values = dict(descriptor["parameters"])
    if descriptor["kind"] == "parametric" and parameters is not None:
        values.update(parameters)
    built = build_device(
        str(descriptor["component"]),
        values,
        technology or TechnologySpec(default_si_width_um=0.5, default_sin_width_um=1.0),
    )
    raw_polygons = built.component.get_polygons_points(by="tuple")
    layers: dict[str, list[list[list[float]]]] = {}
    all_x: list[float] = []
    all_y: list[float] = []
    for layer, polygons in raw_polygons.items():
        key = f"{int(layer[0])}/{int(layer[1])}"
        layers[key] = []
        for polygon in polygons:
            points = exact_preview_points(polygon)
            layers[key].append(points)
            all_x.extend(point[0] for point in points)
            all_y.extend(point[1] for point in points)
    ports = []
    for port in built.component.ports:
        center = [round(float(value), 6) for value in port.dcenter]
        layer = built.port_layers[port.name]
        ports.append(
            {
                "name": port.name,
                "center_um": center,
                "width_um": float(port.dwidth),
                "orientation_deg": float(port.orientation) % 360.0,
                "layer": list(layer),
            }
        )
        all_x.append(center[0])
        all_y.append(center[1])
    if not all_x or not all_y:
        raise SpecError(f"{descriptor['key']} produced no preview geometry")
    return {
        "key": descriptor["key"],
        "kind": descriptor["kind"],
        "component": descriptor["component"],
        "title": descriptor["title"],
        "family": built.family,
        "parameters": values,
        "fields": descriptor.get("fields", []),
        "layers": layers,
        "ports": ports,
        "bbox_um": [min(all_x), min(all_y), max(all_x), max(all_y)],
    }


def _assembly_from_instances(
    cluster_name: str,
    instances: list[dict[str, object]],
) -> Assembly:
    assembly = Assembly(cluster_name)
    for raw in instances:
        instance_id = str(raw["id"])
        placement = dict(raw.get("placement", {}))
        at = (
            float(placement.get("x_um", 0.0)),
            float(placement.get("y_um", 0.0)),
        )
        rotation = int(placement.get("rotation_deg", 0))
        component = str(raw["component"])
        parameters = dict(raw.get("parameters", {}))
        if component == "registered_gds":
            library = DeviceLibrary(str(parameters["catalog_path"]))
            assembly.add_registered(
                instance_id,
                str(parameters["template_id"]),
                at=at,
                rotation_deg=rotation,
                library=library,
            )
        else:
            assembly.add_parametric(
                instance_id,
                component,
                at=at,
                rotation_deg=rotation,
                **parameters,
            )
    return assembly


def snap_editor_instances(payload: dict[str, object]) -> dict[str, object]:
    instances = list(payload.get("instances", []))
    assembly = _assembly_from_instances("EDITOR_SNAP", instances)
    assembly.align_port(
        str(payload["moving_instance"]),
        str(payload["moving_port"]),
        str(payload["target_instance"]),
        str(payload["target_port"]),
    )
    updated = {
        str(item["id"]): dict(item["placement"])
        for item in assembly.to_dict()["instances"]
    }
    return {"placement": updated[str(payload["moving_instance"])]}


def export_editor_design(
    payload: dict[str, object],
    output_root: str | Path,
) -> dict[str, object]:
    cluster_name = str(payload.get("cluster_name", "WEB_ASSEMBLY"))
    instances = list(payload.get("instances", []))
    if not instances:
        raise SpecError("place at least one device before export")
    assembly = _assembly_from_instances(cluster_name, instances)
    used: set[tuple[str, str]] = set()
    for index, raw in enumerate(list(payload.get("nets", [])), start=1):
        terminals = list(raw["terminals"])
        if len(terminals) != 2:
            raise SpecError("editor nets must contain exactly two terminals")
        left, right = [dict(value) for value in terminals]
        route = dict(raw.get("route", {}))
        direct = bool(route.pop("direct", False))
        assembly.connect(
            str(left["instance"]),
            str(left["port"]),
            str(right["instance"]),
            str(right["port"]),
            net_id=str(raw.get("id", f"N_{index}")),
            direct=direct,
            route=route,
        )
        used.add((str(left["instance"]), str(left["port"])))
        used.add((str(right["instance"]), str(right["port"])))

    external_index = 1
    for raw in instances:
        instance_id = str(raw["id"])
        built = assembly._built(instance_id)
        for port in built.component.ports:
            terminal = (instance_id, port.name)
            if terminal in used:
                continue
            name = f"E{external_index}_{instance_id}_{port.name}"
            assembly.external(name, instance_id, port.name)
            external_index += 1

    root = Path(output_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    destination = root / f"{cluster_name}_{stamp}_{uuid.uuid4().hex[:6]}"
    spec_path = assembly.save_spec(destination / "editor_spec.json")
    result = assembly.export(destination)
    return {
        **result,
        "spec": str(spec_path),
        "output_dir": str(destination),
    }


def _normalize_workspace_draft(payload: dict[str, object]) -> dict[str, object]:
    cluster_name = str(payload.get("cluster_name", "WEB_ASSEMBLY")).strip()
    if not cluster_name or len(cluster_name) > 32:
        raise SpecError("draft cluster_name must contain 1 to 32 characters")
    raw_instances = payload.get("instances", [])
    raw_nets = payload.get("nets", [])
    raw_hidden_layers = payload.get("hidden_layers", [])
    if not isinstance(raw_instances, list) or len(raw_instances) > 1000:
        raise SpecError("draft instances must be a list with at most 1000 entries")
    if not isinstance(raw_nets, list) or len(raw_nets) > 5000:
        raise SpecError("draft nets must be a list with at most 5000 entries")
    if not isinstance(raw_hidden_layers, list) or len(raw_hidden_layers) > 1000:
        raise SpecError("draft hidden_layers must be a list with at most 1000 entries")
    hidden_layers = sorted({str(layer) for layer in raw_hidden_layers if str(layer)})

    instances: list[dict[str, object]] = []
    instance_ids: set[str] = set()
    for raw in raw_instances:
        if not isinstance(raw, dict):
            raise SpecError("each draft instance must be an object")
        instance_id = str(raw.get("id", "")).strip()
        library_key = str(raw.get("library_key", "")).strip()
        parameters = raw.get("parameters", {})
        placement = raw.get("placement", {})
        if not instance_id or instance_id in instance_ids:
            raise SpecError("draft instance ids must be non-empty and unique")
        if not library_key:
            raise SpecError(f"draft instance {instance_id!r} has no library_key")
        if not isinstance(parameters, dict) or not isinstance(placement, dict):
            raise SpecError(f"draft instance {instance_id!r} has invalid state")
        x_um = float(placement.get("x_um", 0.0))
        y_um = float(placement.get("y_um", 0.0))
        rotation_deg = float(placement.get("rotation_deg", 0.0))
        if not all(math.isfinite(value) for value in (x_um, y_um, rotation_deg)):
            raise SpecError(f"draft instance {instance_id!r} has non-finite placement")
        if rotation_deg % 90:
            raise SpecError(f"draft instance {instance_id!r} rotation must be a multiple of 90")
        instance_ids.add(instance_id)
        instances.append(
            {
                "id": instance_id,
                "library_key": library_key,
                "parameters": parameters,
                "placement": {
                    "x_um": x_um,
                    "y_um": y_um,
                    "rotation_deg": int(rotation_deg) % 360,
                },
            }
        )

    nets: list[dict[str, object]] = []
    net_ids: set[str] = set()
    for raw in raw_nets:
        if not isinstance(raw, dict):
            raise SpecError("each draft net must be an object")
        net_id = str(raw.get("id", "")).strip()
        terminals = raw.get("terminals", [])
        route = raw.get("route", {})
        if not net_id or net_id in net_ids:
            raise SpecError("draft net ids must be non-empty and unique")
        if not isinstance(terminals, list) or len(terminals) != 2:
            raise SpecError(f"draft net {net_id!r} must have two terminals")
        if not isinstance(route, dict):
            raise SpecError(f"draft net {net_id!r} route must be an object")
        normalized_terminals = []
        for terminal in terminals:
            if not isinstance(terminal, dict):
                raise SpecError(f"draft net {net_id!r} has an invalid terminal")
            instance_id = str(terminal.get("instance", "")).strip()
            port_name = str(terminal.get("port", "")).strip()
            if instance_id not in instance_ids or not port_name:
                raise SpecError(f"draft net {net_id!r} references an invalid terminal")
            normalized_terminals.append({"instance": instance_id, "port": port_name})
        net_ids.add(net_id)
        nets.append(
            {
                "id": net_id,
                "terminals": normalized_terminals,
                "route": route,
            }
        )
    return {
        "schema_version": 1,
        "cluster_name": cluster_name,
        "instances": instances,
        "nets": nets,
        "hidden_layers": hidden_layers,
    }


def _save_workspace_draft(path: Path, payload: dict[str, object]) -> dict[str, object]:
    draft = _normalize_workspace_draft(payload)
    draft["saved_at"] = datetime.now().isoformat(timespec="seconds")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(draft, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return draft


def _load_workspace_draft(path: Path) -> dict[str, object] | None:
    if not path.is_file():
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise SpecError("workspace draft root must be an object")
    draft = _normalize_workspace_draft(raw)
    draft["saved_at"] = str(raw.get("saved_at", ""))
    return draft


def run_workspace(
    output_root: str | Path,
    *,
    user_catalog: str | Path | None = None,
    host: str = "127.0.0.1",
    port: int = 0,
    open_browser: bool = True,
    start_page: str = "assembly",
    initial_source: str | Path | None = None,
) -> None:
    if user_catalog is None:
        raise SpecError("the unified workspace requires a project device registry")
    registry_catalog = Path(user_catalog).resolve()
    if registry_catalog.name.lower() != "catalog.json":
        raise SpecError("user_catalog must point to device_registry/catalog.json")
    onboarding = OnboardingSession(registry_catalog.parent, initial_source)
    registry = onboarding.registry

    def editor_library() -> list[dict[str, object]]:
        # Reload on every request so a newly registered GDS becomes available
        # to assembly without restarting the shared workspace service.
        return build_editor_library(registry.catalog_path)

    static_page = Path(__file__).resolve().parent / "static" / "index.html"
    page = static_page.read_bytes()
    onboarding_page = ONBOARDING_PAGE.encode("utf-8")
    artifact_root = Path(output_root).resolve()
    draft_path = artifact_root / "workspace_draft.json"
    draft_lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def _json(self, value: object, status: int = 200) -> None:
            payload = json.dumps(value, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def _body(self) -> dict[str, object]:
            length = int(self.headers.get("Content-Length", "0"))
            value = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(value, dict):
                raise ValueError("request body must be an object")
            return value

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path == "/":
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(page)))
                self.end_headers()
                self.wfile.write(page)
                return
            if parsed.path in {"/register", "/register/"}:
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(onboarding_page)))
                self.end_headers()
                self.wfile.write(onboarding_page)
                return
            if parsed.path == "/api/library":
                self._json(editor_library())
                return
            if parsed.path == "/api/health":
                library = editor_library()
                self._json(
                    {
                        "status": "ok",
                        "library_items": len(library),
                        "output_root": str(artifact_root),
                        "registry": str(registry.catalog_path),
                    }
                )
                return
            if parsed.path == "/api/devices":
                catalog = load_registry_catalog(registry.catalog_path)
                self._json([_summary(scan, catalog) for scan in onboarding.scans()])
                return
            if parsed.path == "/api/state":
                self._json(onboarding.state())
                return
            if parsed.path == "/api/registry":
                catalog = load_registry_catalog(registry.catalog_path)
                families = sorted(
                    {
                        str(item["family"])
                        for item in editor_library()
                        if str(item.get("family", "")).strip()
                    }
                )
                self._json(
                    {
                        "registry_revision": catalog["registry_revision"],
                        "template_ids": sorted(dict(catalog["templates"])),
                        "families": families,
                    }
                )
                return
            if parsed.path == "/api/draft":
                with draft_lock:
                    draft = _load_workspace_draft(draft_path)
                self._json({"exists": draft is not None, "draft": draft})
                return
            if parsed.path == "/api/device":
                device_id = parse_qs(parsed.query).get("id", [""])[0]
                try:
                    scan = onboarding.get_scan(device_id)
                except SpecError as error:
                    self._json({"error": str(error)}, 404)
                    return
                catalog = load_registry_catalog(registry.catalog_path)
                self._json(_device_payload(scan, catalog))
                return
            if parsed.path == "/api/artifact":
                relative = parse_qs(parsed.query).get("file", [""])[0]
                target = (artifact_root / relative).resolve()
                if artifact_root not in target.parents or not target.is_file():
                    self._json({"error": "artifact not found"}, 404)
                    return
                mime = "image/png" if target.suffix.lower() == ".png" else "application/octet-stream"
                payload = target.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", mime)
                self.send_header("Content-Length", str(len(payload)))
                self.send_header("Content-Disposition", f'inline; filename="{target.name}"')
                self.end_headers()
                self.wfile.write(payload)
                return
            self._json({"error": "not found"}, 404)

        def do_POST(self) -> None:
            try:
                body = self._body()
                path = urlparse(self.path).path
                if path == "/api/draft":
                    with draft_lock:
                        draft = _save_workspace_draft(draft_path, body)
                    self._json(
                        {
                            "saved": True,
                            "saved_at": draft["saved_at"],
                            "instance_count": len(draft["instances"]),
                            "net_count": len(draft["nets"]),
                        }
                    )
                    return
                if path == "/api/draft/clear":
                    with draft_lock:
                        existed = draft_path.is_file()
                        draft_path.unlink(missing_ok=True)
                    self._json({"cleared": True, "existed": existed})
                    return
                if path == "/api/source":
                    self._json(onboarding.load_source(str(body["source_folder"])))
                    return
                if path == "/api/register":
                    scan = onboarding.get_scan(str(body["device_id"]))
                    replace_existing = body.get("replace_existing", False)
                    if not isinstance(replace_existing, bool):
                        raise ValueError("replace_existing must be a boolean")
                    record = registry.register(
                        scan,
                        template_id=str(body["template_id"]),
                        family=str(body["family"]),
                        ports=list(body["ports"]),
                        replace_existing=replace_existing,
                    )
                    catalog = load_registry_catalog(registry.catalog_path)
                    self._json(
                        {
                            "saved": True,
                            "template_id": body["template_id"],
                            "port_count": len(record["ports"]),
                            "registry_revision": catalog["registry_revision"],
                            "catalog": str(registry.catalog_path),
                        }
                    )
                    return
                if path == "/api/preview":
                    key = str(body["key"])
                    library = editor_library()
                    by_key = {str(item["key"]): item for item in library}
                    if key not in by_key:
                        raise SpecError(f"unknown library item {key!r}")
                    self._json(
                        build_device_preview(
                            by_key[key],
                            dict(body.get("parameters", {})),
                        )
                    )
                    return
                if path == "/api/snap":
                    self._json(snap_editor_instances(body))
                    return
                if path == "/api/export":
                    result = export_editor_design(body, artifact_root)
                    relative = {
                        name: str(Path(str(value)).resolve().relative_to(artifact_root)).replace("\\", "/")
                        for name, value in result.items()
                        if name in {"gds", "netlist", "manifest", "preview", "device_atlas", "validation_report", "spec"}
                    }
                    self._json(
                        {
                            "passed": bool(dict(result["validation"])["passed"]),
                            "output_dir": result["output_dir"],
                            "artifacts": relative,
                        }
                    )
                    return
                self._json({"error": "not found"}, 404)
            except (KeyError, TypeError, ValueError, SpecError, json.JSONDecodeError) as error:
                self._json({"error": str(error)}, 400)
            except Exception as error:
                self._json({"error": f"export failed: {error}"}, 500)

        def log_message(self, format: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer((host, port), Handler)
    address = f"http://{host}:{server.server_port}/"
    start_url = address if start_page == "assembly" else f"{address}register"
    library = editor_library()
    print(f"Photonic layout workspace: {address}")
    print(f"Library items: {len(library)}; registry: {registry.catalog_path}")
    if open_browser:
        threading.Timer(0.4, lambda: webbrowser.open(start_url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
