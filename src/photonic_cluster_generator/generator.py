from __future__ import annotations

from collections import Counter
from dataclasses import asdict
import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any
import uuid

import gdsfactory as gf

from .library import BuiltDevice, build_device, cross_section
from .models import ClusterSpec, Layer, NetSpec, SpecError, load_cluster_spec
from .netlist import assign_terminal_nets, render_reference_netlist, validate_device_graph


GENERATOR_VERSION = "0.3.0"


def _layer_list(layer: Layer) -> list[int]:
    return [int(layer[0]), int(layer[1])]


def _port_record(port: Any, layer: Layer) -> dict[str, object]:
    return {
        "center_um": [float(port.dcenter[0]), float(port.dcenter[1])],
        "width_um": float(port.dwidth),
        "orientation_deg": float(port.orientation) % 360.0,
        "layer": _layer_list(layer),
    }


def _short_route_cell_name(net_id: str) -> str:
    digest = hashlib.sha1(net_id.encode("utf-8")).hexdigest()[:8]
    return f"ROUTE_{net_id[:16]}_{digest}"[:31]


def _add_pin_marker(
    component: gf.Component,
    center: tuple[float, float],
    width_um: float,
    orientation_deg: float,
    length_um: float,
    layer: Layer,
) -> None:
    angle = math.radians(orientation_deg)
    direction = (math.cos(angle), math.sin(angle))
    normal = (-direction[1], direction[0])
    half_length = length_um / 2.0
    half_width = max(width_um, 0.2) / 2.0
    points = []
    for along, across in (
        (-half_length, -half_width),
        (half_length, -half_width),
        (half_length, half_width),
        (-half_length, half_width),
    ):
        points.append(
            (
                center[0] + along * direction[0] + across * normal[0],
                center[1] + along * direction[1] + across * normal[1],
            )
        )
    component.add_polygon(points, layer=layer)


def _add_external_port_evidence(
    component: gf.Component,
    name: str,
    port: Any,
    spec: ClusterSpec,
) -> None:
    center = (float(port.dcenter[0]), float(port.dcenter[1]))
    if spec.output.write_port_labels:
        component.add_label(name, position=center, layer=spec.technology.label_layer)
    if spec.output.write_pin_markers:
        _add_pin_marker(
            component,
            center=center,
            width_um=float(port.dwidth),
            orientation_deg=float(port.orientation),
            length_um=spec.output.pin_marker_length_um,
            layer=spec.technology.pin_layer,
        )


def _route_net(
    top: gf.Component,
    net: NetSpec,
    references: dict[str, Any],
    devices: dict[str, BuiltDevice],
    spec: ClusterSpec,
) -> dict[str, object]:
    terminal_a, terminal_b = net.terminals
    ref_a = references[terminal_a.instance_id]
    ref_b = references[terminal_b.instance_id]
    port_a = ref_a.ports[terminal_a.port_name]
    port_b = ref_b.ports[terminal_b.port_name]
    layer_a = devices[terminal_a.instance_id].port_layers[terminal_a.port_name]
    layer_b = devices[terminal_b.instance_id].port_layers[terminal_b.port_name]
    if layer_a != layer_b:
        raise SpecError(
            f"net {net.net_id} directly connects different layers {layer_a} and {layer_b}; "
            "insert an explicit si_sin_transition instance"
        )
    width_a = float(port_a.dwidth)
    width_b = float(port_b.dwidth)
    if not math.isclose(width_a, width_b, rel_tol=0.0, abs_tol=1e-6):
        raise SpecError(
            f"net {net.net_id} directly connects widths {width_a} and {width_b} um; "
            "insert an explicit taper instance"
        )

    if net.route.direct:
        center_a = tuple(float(value) for value in port_a.dcenter)
        center_b = tuple(float(value) for value in port_b.dcenter)
        center_distance = math.dist(center_a, center_b)
        orientation_delta = (
            float(port_a.orientation) - float(port_b.orientation)
        ) % 360.0
        if center_distance > 0.001:
            raise SpecError(
                f"direct net {net.net_id} endpoints are {center_distance} um apart; "
                "snap the ports before exporting"
            )
        if not math.isclose(orientation_delta, 180.0, rel_tol=0.0, abs_tol=1e-6):
            raise SpecError(
                f"direct net {net.net_id} ports do not face each other; "
                f"orientation delta={orientation_delta} degrees"
            )
        return {
            "id": net.net_id,
            "kind": "direct",
            "terminals": [
                {"instance": terminal_a.instance_id, "port": terminal_a.port_name},
                {"instance": terminal_b.instance_id, "port": terminal_b.port_name},
            ],
            "route_cell": None,
            "layer": _layer_list(layer_a),
            "width_um": width_a,
            "length_um": 0.0,
            "length_straights_um": 0.0,
            "endpoints_um": [list(center_a), list(center_b)],
        }

    route_cell_name = _short_route_cell_name(net.net_id)
    route_cell = gf.Component(route_cell_name)
    route_port_a = route_cell.add_port(
        name="a",
        center=tuple(float(value) for value in port_a.dcenter),
        width=width_a,
        orientation=float(port_a.orientation),
        layer=layer_a,
    )
    route_port_b = route_cell.add_port(
        name="b",
        center=tuple(float(value) for value in port_b.dcenter),
        width=width_b,
        orientation=float(port_b.orientation),
        layer=layer_b,
    )
    radius = (
        net.route.radius_um
        if net.route.radius_um is not None
        else spec.technology.default_bend_radius_um
    )
    waypoints = list(net.route.waypoints_um) or None
    try:
        route = gf.routing.route_single(
            route_cell,
            port1=route_port_a,
            port2=route_port_b,
            cross_section=cross_section(layer_a, width_a, radius),
            start_straight_length=net.route.start_straight_um,
            end_straight_length=net.route.end_straight_um,
            waypoints=waypoints,
            allow_width_mismatch=False,
            auto_taper=False,
            radius=radius,
        )
    except Exception as error:
        raise SpecError(f"routing failed for net {net.net_id}: {error}") from error
    top.add_ref(route_cell, name=f"route_{net.net_id}")
    length_straights_um = float(route.length) / 1000.0
    length_parts = [instance.cell.info.get("route_info_length") for instance in route.instances]
    if any(value is None for value in length_parts):
        raise SpecError(
            f"route {net.net_id} contains a child without route_info_length; "
            "physical route length cannot be certified"
        )
    length_um = sum(float(value) for value in length_parts)
    return {
        "id": net.net_id,
        "kind": "routed",
        "terminals": [
            {"instance": terminal_a.instance_id, "port": terminal_a.port_name},
            {"instance": terminal_b.instance_id, "port": terminal_b.port_name},
        ],
        "route_cell": route_cell.name,
        "layer": _layer_list(layer_a),
        "width_um": width_a,
        "length_um": length_um,
        "length_straights_um": length_straights_um,
        "endpoints_um": [
            [float(value) for value in port_a.dcenter],
            [float(value) for value in port_b.dcenter],
        ],
    }


def build_cluster(spec: ClusterSpec) -> tuple[gf.Component, dict[str, object], str]:
    gf.clear_cache()
    from .onboarding.registry import clear_import_cache

    clear_import_cache()
    top = gf.Component(spec.cluster_name)
    devices: dict[str, BuiltDevice] = {}
    references: dict[str, Any] = {}
    instance_ports: dict[str, list[str]] = {}
    instance_families: dict[str, str] = {}
    instance_records: list[dict[str, object]] = []

    for instance in spec.instances:
        built = build_device(instance.component, instance.parameters, spec.technology)
        reference = top.add_ref(built.component, name=instance.instance_id)
        if instance.placement.rotation_deg:
            reference.rotate(instance.placement.rotation_deg)
        reference.dmove((instance.placement.x_um, instance.placement.y_um))
        port_names = [port.name for port in built.component.ports]
        devices[instance.instance_id] = built
        references[instance.instance_id] = reference
        instance_ports[instance.instance_id] = port_names
        instance_families[instance.instance_id] = built.family
        instance_records.append(
            {
                "id": instance.instance_id,
                "component": instance.component,
                "family": built.family,
                "parameters": dict(instance.parameters),
                "placement": asdict(instance.placement),
                "source_cell": built.component.name,
                "source_layers": [_layer_list(layer) for layer in built.source_layers],
                "template_id": built.template_id,
                "source_template": built.source_path,
                "bbox_um": [
                    float(reference.dbbox().left),
                    float(reference.dbbox().bottom),
                    float(reference.dbbox().right),
                    float(reference.dbbox().top),
                ],
                "ports": {
                    name: _port_record(reference.ports[name], built.port_layers[name])
                    for name in port_names
                },
            }
        )

    assign_terminal_nets(spec, instance_ports)
    device_graph = validate_device_graph(spec)
    route_records = [
        _route_net(top, net, references, devices, spec)
        for net in spec.nets
    ]

    external_records = []
    for external in spec.external_ports:
        terminal = external.terminal
        port = references[terminal.instance_id].ports[terminal.port_name]
        top.add_port(name=external.name, port=port)
        _add_external_port_evidence(top, external.name, port, spec)
        layer = devices[terminal.instance_id].port_layers[terminal.port_name]
        external_records.append(
            {
                "name": external.name,
                "terminal": {"instance": terminal.instance_id, "port": terminal.port_name},
                **_port_record(port, layer),
            }
        )

    expected_direct_children = [record["source_cell"] for record in instance_records] + [
        record["route_cell"] for record in route_records if record["route_cell"] is not None
    ]
    manifest: dict[str, object] = {
        "manifest_version": "1.0.0",
        "generator_version": GENERATOR_VERSION,
        "cluster_name": spec.cluster_name,
        "top_cell": top.name,
        "hierarchy_policy": "device and net-route child cells retained; no flatten operation",
        "technology": {
            "si_layer": _layer_list(spec.technology.si_layer),
            "sin_layer": _layer_list(spec.technology.sin_layer),
            "label_layer": _layer_list(spec.technology.label_layer),
            "pin_layer": _layer_list(spec.technology.pin_layer),
        },
        "output": asdict(spec.output),
        "instances": instance_records,
        "nets": route_records,
        "external_ports": external_records,
        "device_graph": device_graph,
        "expected_top_direct_child_counts": dict(Counter(expected_direct_children)),
    }
    netlist = render_reference_netlist(spec, instance_ports, instance_families)
    return top, manifest, netlist


def generate_cluster(
    spec_or_path: ClusterSpec | str | Path,
    output_dir: str | Path,
) -> dict[str, object]:
    from .validator import render_annotated_preview, render_device_atlas, validate_written_gds

    spec_path: Path | None = None
    if isinstance(spec_or_path, ClusterSpec):
        spec = spec_or_path
    else:
        spec_path = Path(spec_or_path).resolve()
        spec = load_cluster_spec(spec_path)

    destination = Path(output_dir).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    top, manifest, netlist = build_cluster(spec)
    gds_path = destination / f"{spec.cluster_name}.gds"
    netlist_path = destination / f"{spec.cluster_name}.cir"
    manifest_path = destination / "manifest.json"
    validation_path = destination / "validation.json"
    preview_path = destination / f"{spec.cluster_name}_annotated.png"
    atlas_path = destination / f"{spec.cluster_name}_device_atlas.png"
    candidate_id = uuid.uuid4().hex
    candidate_gds = destination / f".{candidate_id}.candidate.gds"
    candidate_netlist = destination / f".{candidate_id}.candidate.cir"
    candidate_preview = destination / f".{candidate_id}.candidate.png"
    candidate_atlas = destination / f".{candidate_id}.candidate.atlas.png"
    candidate_validation = destination / f".{candidate_id}.candidate.validation.json"
    candidate_manifest = destination / f".{candidate_id}.candidate.manifest.json"
    candidates = (
        candidate_gds,
        candidate_netlist,
        candidate_preview,
        candidate_atlas,
        candidate_validation,
        candidate_manifest,
    )
    try:
        top.write_gds(candidate_gds, with_metadata=False)
        candidate_netlist.write_text(netlist, encoding="utf-8")
        validation = validate_written_gds(candidate_gds, manifest)
        validation["gds_path"] = str(gds_path)
        render_annotated_preview(candidate_gds, manifest, candidate_preview)
        render_device_atlas(candidate_gds, manifest, candidate_atlas)

        manifest["source_spec"] = str(spec_path) if spec_path else "in-memory"
        manifest["artifacts"] = {
            "gds": str(gds_path),
            "reference_netlist": str(netlist_path),
            "annotated_preview": str(preview_path),
            "device_atlas": str(atlas_path),
            "validation": str(validation_path),
        }
        manifest["gds_sha256"] = hashlib.sha256(candidate_gds.read_bytes()).hexdigest()
        manifest["roundtrip_validation"] = validation
        candidate_validation.write_text(
            json.dumps(validation, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        candidate_manifest.write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        for candidate, final in (
            (candidate_gds, gds_path),
            (candidate_netlist, netlist_path),
            (candidate_preview, preview_path),
            (candidate_atlas, atlas_path),
            (candidate_validation, validation_path),
            (candidate_manifest, manifest_path),
        ):
            os.replace(candidate, final)
    except Exception:
        for candidate in candidates:
            candidate.unlink(missing_ok=True)
        raise
    return {
        "gds": str(gds_path),
        "netlist": str(netlist_path),
        "manifest": str(manifest_path),
        "preview": str(preview_path),
        "device_atlas": str(atlas_path),
        "validation_report": str(validation_path),
        "validation": validation,
    }
