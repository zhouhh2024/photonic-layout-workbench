from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import json
import math
from pathlib import Path
import re
from typing import Mapping

from .generator import generate_cluster
from .library import BuiltDevice, build_device
from .models import SpecError, TechnologySpec, parse_cluster_spec
from .onboarding.registry import load_registry_catalog


_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True)
class PortState:
    instance_id: str
    port_name: str
    center_um: tuple[float, float]
    width_um: float
    orientation_deg: float
    layer: tuple[int, int]


class DeviceLibrary:
    """Read-only browser for a versioned black-box GDS catalog."""

    def __init__(self, catalog_path: str | Path):
        self.catalog_path = str(catalog_path)
        self._catalog = load_registry_catalog(catalog_path)

    def template_ids(self) -> tuple[str, ...]:
        return tuple(sorted(dict(self._catalog["templates"])))

    def describe(self, template_id: str) -> dict[str, object]:
        templates = dict(self._catalog["templates"])
        if template_id not in templates:
            raise SpecError(f"unknown template {template_id!r}")
        return json.loads(json.dumps(templates[template_id]))

    def search(self, text: str = "") -> list[dict[str, object]]:
        needle = text.lower().strip()
        results = []
        for template_id in self.template_ids():
            record = self.describe(template_id)
            searchable = (
                f"{template_id} {record.get('family', '')} {record.get('filename', '')}"
            ).lower()
            if needle and needle not in searchable:
                continue
            results.append(
                {
                    "template_id": template_id,
                    "family": record.get("family"),
                    "filename": record.get("filename"),
                    "source_layers": record.get("source_layers", []),
                    "ports": list(dict(record.get("ports", {}))),
                }
            )
        return results


class Assembly:
    """Code-first editor model shared by scripts and the future drag-and-drop UI."""

    def __init__(
        self,
        cluster_name: str,
        *,
        library: DeviceLibrary | None = None,
        technology: TechnologySpec | None = None,
    ):
        if not _IDENTIFIER.fullmatch(cluster_name) or len(cluster_name) > 32:
            raise SpecError("cluster_name must be a portable identifier of at most 32 characters")
        self.cluster_name = cluster_name
        self.library = library
        self.technology = technology or TechnologySpec(
            default_si_width_um=0.5,
            default_sin_width_um=1.0,
        )
        self._instances: dict[str, dict[str, object]] = {}
        self._nets: list[dict[str, object]] = []
        self._external_ports: list[dict[str, object]] = []
        self._built_cache: dict[str, BuiltDevice] = {}

    @staticmethod
    def _validate_id(value: str, field: str) -> None:
        if not _IDENTIFIER.fullmatch(value):
            raise SpecError(f"{field} must be a valid identifier: {value!r}")

    def add_registered(
        self,
        instance_id: str,
        template_id: str,
        *,
        at: tuple[float, float] = (0.0, 0.0),
        rotation_deg: int = 0,
        library: DeviceLibrary | None = None,
    ) -> "Assembly":
        selected_library = library or self.library
        if selected_library is None:
            raise SpecError(
                "add_registered requires a DeviceLibrary backed by a local catalog"
            )
        selected_library.describe(template_id)
        return self._add(
            instance_id,
            "registered_gds",
            {
                "catalog_path": selected_library.catalog_path,
                "template_id": template_id,
            },
            at,
            rotation_deg,
        )

    def add_parametric(
        self,
        instance_id: str,
        component: str,
        *,
        at: tuple[float, float] = (0.0, 0.0),
        rotation_deg: int = 0,
        **parameters: object,
    ) -> "Assembly":
        return self._add(instance_id, component, parameters, at, rotation_deg)

    def _add(
        self,
        instance_id: str,
        component: str,
        parameters: Mapping[str, object],
        at: tuple[float, float],
        rotation_deg: int,
    ) -> "Assembly":
        self._validate_id(instance_id, "instance_id")
        if instance_id in self._instances:
            raise SpecError(f"duplicate instance id {instance_id!r}")
        if rotation_deg % 90:
            raise SpecError("rotation_deg must be a multiple of 90")
        x, y = float(at[0]), float(at[1])
        if not math.isfinite(x) or not math.isfinite(y):
            raise SpecError("instance coordinates must be finite")
        self._instances[instance_id] = {
            "id": instance_id,
            "component": component,
            "parameters": dict(parameters),
            "placement": {"x_um": x, "y_um": y, "rotation_deg": rotation_deg % 360},
        }
        return self

    def move(self, instance_id: str, x_um: float, y_um: float) -> "Assembly":
        x, y = float(x_um), float(y_um)
        if not math.isfinite(x) or not math.isfinite(y):
            raise SpecError("instance coordinates must be finite")
        placement = self._placement(instance_id)
        placement["x_um"] = x
        placement["y_um"] = y
        return self

    def rotate(self, instance_id: str, rotation_deg: int) -> "Assembly":
        if rotation_deg % 90:
            raise SpecError("rotation_deg must be a multiple of 90")
        self._placement(instance_id)["rotation_deg"] = rotation_deg % 360
        return self

    def set_parameters(self, instance_id: str, **parameters: object) -> "Assembly":
        """Update parametric settings; future UI context menus call this method."""
        instance = self._instance(instance_id)
        if instance["component"] == "registered_gds":
            raise SpecError(
                "a registered GDS is immutable; select a parameter variant with "
                "replace_template() instead"
            )
        values = dict(instance["parameters"])
        values.update(parameters)
        # Validate before committing so a rejected property edit cannot leave
        # the editor model in an invalid state.
        built = build_device(str(instance["component"]), values, self.technology)
        instance["parameters"] = values
        self._built_cache[instance_id] = built
        return self

    def replace_template(
        self,
        instance_id: str,
        template_id: str,
        *,
        library: DeviceLibrary | None = None,
    ) -> "Assembly":
        instance = self._instance(instance_id)
        if instance["component"] != "registered_gds":
            raise SpecError("replace_template applies only to registered_gds instances")
        current = dict(instance["parameters"])
        selected_library = library or DeviceLibrary(str(current["catalog_path"]))
        selected_library.describe(template_id)
        values = {
            "catalog_path": selected_library.catalog_path,
            "template_id": template_id,
        }
        built = build_device("registered_gds", values, self.technology)
        instance["parameters"] = values
        self._built_cache[instance_id] = built
        return self

    def port(self, instance_id: str, port_name: str) -> PortState:
        built = self._built(instance_id)
        if port_name not in built.port_layers:
            raise SpecError(f"instance {instance_id!r} has no port {port_name!r}")
        component_port = built.component.ports[port_name]
        placement = self._placement(instance_id)
        angle = math.radians(float(placement["rotation_deg"]))
        cosine, sine = math.cos(angle), math.sin(angle)
        local_x, local_y = [float(value) for value in component_port.dcenter]
        center = (
            cosine * local_x - sine * local_y + float(placement["x_um"]),
            sine * local_x + cosine * local_y + float(placement["y_um"]),
        )
        return PortState(
            instance_id=instance_id,
            port_name=port_name,
            center_um=center,
            width_um=float(component_port.dwidth),
            orientation_deg=(
                float(component_port.orientation) + float(placement["rotation_deg"])
            )
            % 360.0,
            layer=built.port_layers[port_name],
        )

    def align_port(
        self,
        moving_instance: str,
        moving_port: str,
        target_instance: str,
        target_port: str,
        *,
        gap_um: float = 0.0,
    ) -> "Assembly":
        """Rotate and move one instance so two ports face each other on one axis."""
        if moving_instance == target_instance:
            raise SpecError("cannot align an instance to itself")
        target = self.port(target_instance, target_port)
        built = self._built(moving_instance)
        if moving_port not in built.port_layers:
            raise SpecError(f"instance {moving_instance!r} has no port {moving_port!r}")
        local = built.component.ports[moving_port]
        desired_orientation = (target.orientation_deg + 180.0) % 360.0
        rotations = [
            rotation
            for rotation in (0, 90, 180, 270)
            if math.isclose(
                (float(local.orientation) + rotation) % 360.0,
                desired_orientation,
                rel_tol=0.0,
                abs_tol=1e-6,
            )
        ]
        if not rotations:
            raise SpecError("ports cannot face each other using 90-degree rotations")
        rotation = rotations[0]
        angle = math.radians(rotation)
        cosine, sine = math.cos(angle), math.sin(angle)
        local_x, local_y = [float(value) for value in local.dcenter]
        rotated_local = (
            cosine * local_x - sine * local_y,
            sine * local_x + cosine * local_y,
        )
        gap = float(gap_um)
        if gap < 0 or not math.isfinite(gap):
            raise SpecError("gap_um must be finite and non-negative")
        direction = math.radians(target.orientation_deg)
        desired_center = (
            target.center_um[0] + gap * math.cos(direction),
            target.center_um[1] + gap * math.sin(direction),
        )
        placement = self._placement(moving_instance)
        placement.update(
            {
                "x_um": desired_center[0] - rotated_local[0],
                "y_um": desired_center[1] - rotated_local[1],
                "rotation_deg": rotation,
            }
        )
        return self

    def snap(
        self,
        moving_instance: str,
        moving_port: str,
        target_instance: str,
        target_port: str,
        *,
        net_id: str | None = None,
    ) -> "Assembly":
        self._assert_terminal_available(moving_instance, moving_port)
        self._assert_terminal_available(target_instance, target_port)
        moving = self.port(moving_instance, moving_port)
        target = self.port(target_instance, target_port)
        self._assert_compatible_ports(moving, target)
        self.align_port(
            moving_instance,
            moving_port,
            target_instance,
            target_port,
            gap_um=0.0,
        )
        return self.connect(
            target_instance,
            target_port,
            moving_instance,
            moving_port,
            net_id=net_id,
            direct=True,
        )

    def connect(
        self,
        instance_a: str,
        port_a: str,
        instance_b: str,
        port_b: str,
        *,
        net_id: str | None = None,
        direct: bool | None = None,
        route: Mapping[str, object] | None = None,
    ) -> "Assembly":
        self._assert_terminal_available(instance_a, port_a)
        self._assert_terminal_available(instance_b, port_b)
        left = self.port(instance_a, port_a)
        right = self.port(instance_b, port_b)
        self._assert_compatible_ports(left, right)
        auto_direct = (
            math.dist(left.center_um, right.center_um) <= 0.001
            and math.isclose(
                (left.orientation_deg - right.orientation_deg) % 360.0,
                180.0,
                abs_tol=1e-6,
            )
        )
        is_direct = auto_direct if direct is None else bool(direct)
        if is_direct and not auto_direct:
            raise SpecError("direct connection requires coincident, opposite-facing ports")
        name = net_id or f"N_{len(self._nets) + 1}"
        self._validate_id(name, "net_id")
        if any(net["id"] == name for net in self._nets):
            raise SpecError(f"duplicate net id {name!r}")
        route_data = dict(route or {})
        if is_direct:
            route_data = {"direct": True}
        self._nets.append(
            {
                "id": name,
                "terminals": [
                    {"instance": instance_a, "port": port_a},
                    {"instance": instance_b, "port": port_b},
                ],
                "route": route_data,
            }
        )
        return self

    def external(self, name: str, instance_id: str, port_name: str) -> "Assembly":
        self._validate_id(name, "external port name")
        self.port(instance_id, port_name)
        if any(item["name"] == name for item in self._external_ports):
            raise SpecError(f"duplicate external port name {name!r}")
        self._assert_terminal_available(instance_id, port_name)
        self._external_ports.append(
            {"name": name, "terminal": {"instance": instance_id, "port": port_name}}
        )
        return self

    def to_dict(self) -> dict[str, object]:
        technology = self.technology
        return {
            "schema_version": "1.0.0",
            "cluster_name": self.cluster_name,
            "technology": {
                "si_layer": list(technology.si_layer),
                "sin_layer": list(technology.sin_layer),
                "label_layer": list(technology.label_layer),
                "pin_layer": list(technology.pin_layer),
                "default_si_width_um": technology.default_si_width_um,
                "default_sin_width_um": technology.default_sin_width_um,
                "default_bend_radius_um": technology.default_bend_radius_um,
            },
            "instances": deepcopy(list(self._instances.values())),
            "nets": deepcopy(self._nets),
            "external_ports": deepcopy(self._external_ports),
            "output": {
                "write_port_labels": True,
                "write_pin_markers": True,
                "pin_marker_length_um": 0.8,
            },
            "require_connected_device_graph": True,
        }

    def save_spec(self, path: str | Path) -> Path:
        destination = Path(path).resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        value = self.to_dict()
        parse_cluster_spec(value)
        destination.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")
        return destination

    def export(self, output_dir: str | Path) -> dict[str, object]:
        spec = parse_cluster_spec(self.to_dict())
        try:
            return generate_cluster(spec, output_dir)
        finally:
            self._built_cache.clear()

    def _instance(self, instance_id: str) -> dict[str, object]:
        if instance_id not in self._instances:
            raise SpecError(f"unknown instance {instance_id!r}")
        return self._instances[instance_id]

    @staticmethod
    def _assert_compatible_ports(left: PortState, right: PortState) -> None:
        if left.layer != right.layer:
            raise SpecError(f"cannot connect different layers {left.layer} and {right.layer}")
        if not math.isclose(
            left.width_um,
            right.width_um,
            rel_tol=0.0,
            abs_tol=1e-6,
        ):
            raise SpecError(f"cannot connect widths {left.width_um} and {right.width_um} um")

    def _assert_terminal_available(self, instance_id: str, port_name: str) -> None:
        terminal = (instance_id, port_name)
        for net in self._nets:
            for item in net["terminals"]:
                if (item["instance"], item["port"]) == terminal:
                    raise SpecError(
                        f"terminal {instance_id}.{port_name} is already used by net {net['id']}"
                    )
        for external in self._external_ports:
            item = external["terminal"]
            if (item["instance"], item["port"]) == terminal:
                raise SpecError(
                    f"terminal {instance_id}.{port_name} is already exposed as "
                    f"{external['name']}"
                )

    def _placement(self, instance_id: str) -> dict[str, object]:
        # Placement is mutable editor state.  Return the stored object so that
        # move/rotate/align operations survive into the exported specification.
        placement = self._instance(instance_id)["placement"]
        if not isinstance(placement, dict):
            raise SpecError(f"invalid placement state for instance {instance_id!r}")
        return placement

    def _built(self, instance_id: str) -> BuiltDevice:
        if instance_id not in self._built_cache:
            instance = self._instance(instance_id)
            self._built_cache[instance_id] = build_device(
                str(instance["component"]),
                dict(instance["parameters"]),
                self.technology,
            )
        return self._built_cache[instance_id]
