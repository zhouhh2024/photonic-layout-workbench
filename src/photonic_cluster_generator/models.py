from __future__ import annotations

from dataclasses import dataclass, field
import json
import math
from pathlib import Path
import re
from typing import Mapping, Sequence


Layer = tuple[int, int]
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class SpecError(ValueError):
    """Raised when a cluster specification is structurally invalid."""


def _identifier(value: object, field_name: str) -> str:
    result = str(value)
    if not _IDENTIFIER.fullmatch(result):
        raise SpecError(f"{field_name} must match {_IDENTIFIER.pattern}: {result!r}")
    return result


def _layer(value: object, field_name: str) -> Layer:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or len(value) != 2:
        raise SpecError(f"{field_name} must be [layer, datatype]")
    result = int(value[0]), int(value[1])
    if min(result) < 0:
        raise SpecError(f"{field_name} values cannot be negative")
    return result


def _boolean(value: object, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise SpecError(f"{field_name} must be a JSON boolean")
    return value


def _finite(value: object, field_name: str) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise SpecError(f"{field_name} must be finite")
    return result


@dataclass(frozen=True)
class TechnologySpec:
    si_layer: Layer = (275, 0)
    sin_layer: Layer = (263, 0)
    label_layer: Layer = (200, 0)
    pin_layer: Layer = (201, 0)
    default_si_width_um: float = 0.6
    default_sin_width_um: float = 1.0
    default_bend_radius_um: float = 10.0


@dataclass(frozen=True)
class PlacementSpec:
    x_um: float
    y_um: float
    rotation_deg: int = 0


@dataclass(frozen=True)
class InstanceSpec:
    instance_id: str
    component: str
    parameters: Mapping[str, object]
    placement: PlacementSpec


@dataclass(frozen=True)
class TerminalSpec:
    instance_id: str
    port_name: str


@dataclass(frozen=True)
class RouteSpec:
    direct: bool = False
    radius_um: float | None = None
    start_straight_um: float = 0.0
    end_straight_um: float = 0.0
    waypoints_um: tuple[tuple[float, float], ...] = ()


@dataclass(frozen=True)
class NetSpec:
    net_id: str
    terminals: tuple[TerminalSpec, TerminalSpec]
    route: RouteSpec = field(default_factory=RouteSpec)


@dataclass(frozen=True)
class ExternalPortSpec:
    name: str
    terminal: TerminalSpec


@dataclass(frozen=True)
class OutputSpec:
    write_port_labels: bool = True
    write_pin_markers: bool = True
    pin_marker_length_um: float = 0.8


@dataclass(frozen=True)
class ClusterSpec:
    schema_version: str
    cluster_name: str
    technology: TechnologySpec
    instances: tuple[InstanceSpec, ...]
    nets: tuple[NetSpec, ...]
    external_ports: tuple[ExternalPortSpec, ...]
    output: OutputSpec
    require_connected_device_graph: bool = True


def _terminal(value: Mapping[str, object], field_name: str) -> TerminalSpec:
    return TerminalSpec(
        instance_id=_identifier(value.get("instance"), f"{field_name}.instance"),
        port_name=_identifier(value.get("port"), f"{field_name}.port"),
    )


def parse_cluster_spec(data: Mapping[str, object]) -> ClusterSpec:
    technology_data = dict(data.get("technology", {}))
    technology = TechnologySpec(
        si_layer=_layer(technology_data.get("si_layer", (275, 0)), "technology.si_layer"),
        sin_layer=_layer(technology_data.get("sin_layer", (263, 0)), "technology.sin_layer"),
        label_layer=_layer(technology_data.get("label_layer", (200, 0)), "technology.label_layer"),
        pin_layer=_layer(technology_data.get("pin_layer", (201, 0)), "technology.pin_layer"),
        default_si_width_um=_finite(
            technology_data.get("default_si_width_um", 0.6),
            "technology.default_si_width_um",
        ),
        default_sin_width_um=_finite(
            technology_data.get("default_sin_width_um", 1.0),
            "technology.default_sin_width_um",
        ),
        default_bend_radius_um=_finite(
            technology_data.get("default_bend_radius_um", 10.0),
            "technology.default_bend_radius_um",
        ),
    )
    if str(data.get("schema_version", "1.0.0")) != "1.0.0":
        raise SpecError("only schema_version '1.0.0' is supported")
    if min(
        technology.default_si_width_um,
        technology.default_sin_width_um,
        technology.default_bend_radius_um,
    ) <= 0:
        raise SpecError("technology widths and bend radius must be positive")
    instances = []
    for index, raw in enumerate(data.get("instances", ())):
        value = dict(raw)
        placement = dict(value.get("placement", {}))
        if "x_um" not in placement or "y_um" not in placement:
            raise SpecError(f"instances[{index}].placement requires x_um and y_um")
        rotation = int(placement.get("rotation_deg", 0))
        if rotation % 90:
            raise SpecError(f"instances[{index}].placement.rotation_deg must be a multiple of 90")
        instances.append(
            InstanceSpec(
                instance_id=_identifier(value.get("id"), f"instances[{index}].id"),
                component=_identifier(value.get("component"), f"instances[{index}].component"),
                parameters=dict(value.get("parameters", {})),
                placement=PlacementSpec(
                    x_um=_finite(placement["x_um"], f"instances[{index}].placement.x_um"),
                    y_um=_finite(placement["y_um"], f"instances[{index}].placement.y_um"),
                    rotation_deg=rotation % 360,
                ),
            )
        )
    nets = []
    for index, raw in enumerate(data.get("nets", ())):
        value = dict(raw)
        terminals_raw = tuple(value.get("terminals", ()))
        if len(terminals_raw) != 2:
            raise SpecError(f"nets[{index}] must contain exactly two terminals; use a splitter device for branching")
        route_data = dict(value.get("route", {}))
        waypoints_list = []
        for point_index, point in enumerate(route_data.get("waypoints_um", ())):
            if (
                not isinstance(point, Sequence)
                or isinstance(point, (str, bytes))
                or len(point) != 2
            ):
                raise SpecError(
                    f"nets[{index}].route.waypoints_um[{point_index}] must be [x_um, y_um]"
                )
            waypoints_list.append(
                (
                    _finite(point[0], f"nets[{index}].route.waypoints_um[{point_index}][0]"),
                    _finite(point[1], f"nets[{index}].route.waypoints_um[{point_index}][1]"),
                )
            )
        waypoints = tuple(waypoints_list)
        nets.append(
            NetSpec(
                net_id=_identifier(value.get("id"), f"nets[{index}].id"),
                terminals=(
                    _terminal(dict(terminals_raw[0]), f"nets[{index}].terminals[0]"),
                    _terminal(dict(terminals_raw[1]), f"nets[{index}].terminals[1]"),
                ),
                route=RouteSpec(
                    direct=_boolean(
                        route_data.get("direct", False),
                        f"nets[{index}].route.direct",
                    ),
                    radius_um=(
                        _finite(route_data["radius_um"], f"nets[{index}].route.radius_um")
                        if route_data.get("radius_um") is not None
                        else None
                    ),
                    start_straight_um=_finite(
                        route_data.get("start_straight_um", 0.0),
                        f"nets[{index}].route.start_straight_um",
                    ),
                    end_straight_um=_finite(
                        route_data.get("end_straight_um", 0.0),
                        f"nets[{index}].route.end_straight_um",
                    ),
                    waypoints_um=waypoints,
                ),
            )
        )
        if nets[-1].route.radius_um is not None and nets[-1].route.radius_um <= 0:
            raise SpecError(f"nets[{index}].route.radius_um must be positive")
        if nets[-1].route.start_straight_um < 0 or nets[-1].route.end_straight_um < 0:
            raise SpecError(f"nets[{index}] straight extensions cannot be negative")
        if nets[-1].route.direct and (
            nets[-1].route.radius_um is not None
            or nets[-1].route.start_straight_um
            or nets[-1].route.end_straight_um
            or nets[-1].route.waypoints_um
        ):
            raise SpecError(f"nets[{index}] direct connection cannot contain routing controls")
    external_ports = []
    for index, raw in enumerate(data.get("external_ports", ())):
        value = dict(raw)
        external_ports.append(
            ExternalPortSpec(
                name=_identifier(value.get("name"), f"external_ports[{index}].name"),
                terminal=_terminal(dict(value.get("terminal", {})), f"external_ports[{index}].terminal"),
            )
        )
    output_data = dict(data.get("output", {}))
    output = OutputSpec(
        write_port_labels=_boolean(
            output_data.get("write_port_labels", True), "output.write_port_labels"
        ),
        write_pin_markers=_boolean(
            output_data.get("write_pin_markers", True), "output.write_pin_markers"
        ),
        pin_marker_length_um=_finite(
            output_data.get("pin_marker_length_um", 0.8), "output.pin_marker_length_um"
        ),
    )
    if output.pin_marker_length_um <= 0:
        raise SpecError("output.pin_marker_length_um must be positive")
    cluster_name = _identifier(data.get("cluster_name"), "cluster_name")
    if len(cluster_name) > 32:
        raise SpecError("cluster_name must contain at most 32 characters for GDSII portability")
    spec = ClusterSpec(
        schema_version=str(data.get("schema_version", "1.0.0")),
        cluster_name=cluster_name,
        technology=technology,
        instances=tuple(instances),
        nets=tuple(nets),
        external_ports=tuple(external_ports),
        output=output,
        require_connected_device_graph=_boolean(
            data.get("require_connected_device_graph", True),
            "require_connected_device_graph",
        ),
    )
    _validate_identifiers_and_references(spec)
    return spec


def _validate_identifiers_and_references(spec: ClusterSpec) -> None:
    instance_ids = [item.instance_id for item in spec.instances]
    if not instance_ids:
        raise SpecError("at least one instance is required")
    if len(set(instance_ids)) != len(instance_ids):
        raise SpecError("instance ids must be unique")
    net_ids = [item.net_id for item in spec.nets]
    if len(set(net_ids)) != len(net_ids):
        raise SpecError("net ids must be unique")
    external_names = [item.name for item in spec.external_ports]
    if len(set(external_names)) != len(external_names):
        raise SpecError("external port names must be unique")
    collisions = sorted(set(net_ids) & set(external_names))
    if collisions:
        raise SpecError(
            "net ids and external port names must be disjoint: " + ", ".join(collisions)
        )
    known = set(instance_ids)
    for net in spec.nets:
        for terminal in net.terminals:
            if terminal.instance_id not in known:
                raise SpecError(f"net {net.net_id} references unknown instance {terminal.instance_id}")
    for external in spec.external_ports:
        if external.terminal.instance_id not in known:
            raise SpecError(
                f"external port {external.name} references unknown instance {external.terminal.instance_id}"
            )


def load_cluster_spec(path: Path) -> ClusterSpec:
    return parse_cluster_spec(json.loads(path.read_text(encoding="utf-8")))
