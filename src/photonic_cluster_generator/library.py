from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from typing import Callable, Mapping

import gdsfactory as gf
from gdsfactory.components.waveguides.crossing_waveguide import crossing_linear_taper

from .models import Layer, SpecError, TechnologySpec


@dataclass(frozen=True)
class BuiltDevice:
    component: gf.Component
    family: str
    parameters: Mapping[str, object]
    port_layers: Mapping[str, Layer]
    source_layers: tuple[Layer, ...] = ()
    template_id: str | None = None
    source_path: str | None = None


def _material_layer(material: str, technology: TechnologySpec) -> Layer:
    normalized = material.lower()
    if normalized == "si":
        return technology.si_layer
    if normalized == "sin":
        return technology.sin_layer
    raise SpecError(f"unsupported material {material!r}; expected 'si' or 'sin'")


def _default_width(material: str, technology: TechnologySpec) -> float:
    return (
        technology.default_si_width_um
        if material.lower() == "si"
        else technology.default_sin_width_um
    )


def cross_section(
    layer: Layer,
    width_um: float,
    radius_um: float,
):
    return gf.cross_section.strip(
        width=width_um,
        layer=layer,
        radius=radius_um,
        radius_min=max(radius_um / 2.0, 0.1),
    )


def cross_section_factory(layer: Layer, width_um: float, radius_um: float) -> Callable:
    def factory(width: float = width_um, **_: object):
        return cross_section(layer, float(width), radius_um)

    return factory


def _only(parameters: Mapping[str, object], allowed: set[str], component: str) -> None:
    unknown = sorted(set(parameters) - allowed)
    if unknown:
        raise SpecError(f"{component}: unsupported parameters {unknown}")


def _positive(value: object, field_name: str, *, allow_zero: bool = False) -> float:
    result = float(value)
    valid = math.isfinite(result) and (result >= 0 if allow_zero else result > 0)
    if not valid:
        qualifier = "non-negative" if allow_zero else "positive"
        raise SpecError(f"{field_name} must be a finite {qualifier} number")
    return result


def _named_component(prefix: str, parameters: Mapping[str, object]) -> str:
    digest = hashlib.sha1(
        json.dumps(dict(parameters), sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:10]
    return f"{prefix}_{digest}"[:32]


def build_device(
    component_type: str,
    parameters: Mapping[str, object],
    technology: TechnologySpec,
) -> BuiltDevice:
    kind = component_type.lower()
    values = dict(parameters)
    if kind == "registered_gds":
        _only(values, {"catalog_path", "template_id"}, kind)
        catalog_path = str(values.get("catalog_path", ""))
        template_id = str(values.get("template_id", ""))
        if not catalog_path or not template_id:
            raise SpecError("registered_gds requires catalog_path and template_id")
        from .onboarding.registry import import_registered_template

        component, family, port_layers, _, source_layers, source_path = (
            import_registered_template(catalog_path, template_id)
        )
        return BuiltDevice(
            component=component,
            family=family,
            parameters=values,
            port_layers=port_layers,
            source_layers=source_layers,
            template_id=template_id,
            source_path=source_path,
        )
    material = str(values.get("material", "si"))
    layer = _material_layer(material, technology)
    width = _positive(
        values.get("width_um", _default_width(material, technology)),
        f"{kind}.width_um",
    )
    radius = _positive(
        values.get("radius_um", technology.default_bend_radius_um),
        f"{kind}.radius_um",
    )
    xs = cross_section(layer, width, radius)
    xs_factory = cross_section_factory(layer, width, radius)

    if kind == "straight":
        _only(values, {"material", "length_um", "width_um", "radius_um"}, kind)
        component = gf.components.straight(
            length=_positive(values.get("length_um", 20.0), "straight.length_um"),
            cross_section=xs,
        )
        family = "straight_waveguide"
    elif kind == "bend_euler":
        _only(values, {"material", "radius_um", "angle_deg", "width_um"}, kind)
        component = gf.components.bend_euler(
            radius=radius,
            angle=float(values.get("angle_deg", 90.0)),
            cross_section=xs,
        )
        family = "bend"
    elif kind == "s_bend":
        _only(values, {"material", "length_um", "offset_um", "width_um", "radius_um"}, kind)
        component = gf.components.bend_s(
            size=(
                _positive(values.get("length_um", 30.0), "s_bend.length_um"),
                float(values.get("offset_um", 5.0)),
            ),
            cross_section=xs,
            allow_min_radius_violation=True,
        )
        family = "s_bend"
    elif kind == "taper":
        _only(values, {"material", "length_um", "width1_um", "width2_um", "radius_um"}, kind)
        component = gf.components.taper(
            length=_positive(values.get("length_um", 12.0), "taper.length_um"),
            width1=_positive(values.get("width1_um", width), "taper.width1_um"),
            width2=_positive(values.get("width2_um", 1.0), "taper.width2_um"),
            layer=layer,
            with_bbox=False,
        )
        family = "taper"
    elif kind in {"mmi_1x2", "mmi_2x2"}:
        _only(
            values,
            {
                "material",
                "port_width_um",
                "width_taper_um",
                "length_taper_um",
                "length_mmi_um",
                "width_mmi_um",
                "gap_mmi_um",
                "radius_um",
            },
            kind,
        )
        factory = gf.components.mmi1x2 if kind == "mmi_1x2" else gf.components.mmi2x2
        component = factory(
            width=_positive(values.get("port_width_um", width), f"{kind}.port_width_um"),
            width_taper=_positive(values.get("width_taper_um", 1.0), f"{kind}.width_taper_um"),
            length_taper=_positive(values.get("length_taper_um", 8.0), f"{kind}.length_taper_um"),
            length_mmi=_positive(values.get("length_mmi_um", 8.0), f"{kind}.length_mmi_um"),
            width_mmi=_positive(values.get("width_mmi_um", 3.2), f"{kind}.width_mmi_um"),
            gap_mmi=_positive(
                values.get("gap_mmi_um", 0.4), f"{kind}.gap_mmi_um", allow_zero=True
            ),
            cross_section=xs_factory,
        )
        family = kind
    elif kind == "directional_coupler":
        _only(
            values,
            {"material", "gap_um", "coupling_length_um", "dy_um", "dx_um", "width_um", "radius_um"},
            kind,
        )
        component = gf.components.coupler(
            gap=_positive(
                values.get("gap_um", 0.3), "directional_coupler.gap_um", allow_zero=True
            ),
            length=_positive(
                values.get("coupling_length_um", 15.0),
                "directional_coupler.coupling_length_um",
            ),
            dy=_positive(values.get("dy_um", 6.0), "directional_coupler.dy_um"),
            dx=_positive(values.get("dx_um", 10.0), "directional_coupler.dx_um"),
            cross_section=xs_factory,
            allow_min_radius_violation=True,
        )
        family = "directional_coupler"
    elif kind == "crossing":
        _only(
            values,
            {"material", "width_um", "center_width_um", "taper_length_um", "radius_um"},
            kind,
        )
        component = crossing_linear_taper(
            width1=_positive(values.get("center_width_um", 3.0), "crossing.center_width_um"),
            width2=width,
            length=_positive(values.get("taper_length_um", 5.0), "crossing.taper_length_um"),
            cross_section=xs_factory,
        )
        family = "crossing"
    elif kind == "ring_single":
        _only(
            values,
            {"material", "gap_um", "radius_um", "length_x_um", "length_y_um", "width_um"},
            kind,
        )
        component = gf.components.ring_single(
            gap=_positive(values.get("gap_um", 0.2), "ring_single.gap_um", allow_zero=True),
            radius=radius,
            length_x=_positive(
                values.get("length_x_um", 4.0), "ring_single.length_x_um", allow_zero=True
            ),
            length_y=_positive(
                values.get("length_y_um", 0.6), "ring_single.length_y_um", allow_zero=True
            ),
            cross_section=xs_factory,
        )
        family = "ring_resonator"
    elif kind == "si_sin_transition":
        _only(values, {"length_um", "si_width_um", "sin_width_um", "tip_width_um"}, kind)
        length = _positive(values.get("length_um", 30.0), "si_sin_transition.length_um")
        si_width = _positive(
            values.get("si_width_um", technology.default_si_width_um),
            "si_sin_transition.si_width_um",
        )
        sin_width = _positive(
            values.get("sin_width_um", technology.default_sin_width_um),
            "si_sin_transition.sin_width_um",
        )
        tip = _positive(values.get("tip_width_um", 0.15), "si_sin_transition.tip_width_um")
        name = _named_component("si_sin_transition", values)
        component = gf.Component(name)
        component.add_polygon(
            [(0.0, -sin_width / 2), (length, -tip / 2), (length, tip / 2), (0.0, sin_width / 2)],
            layer=technology.sin_layer,
        )
        component.add_polygon(
            [(0.0, -tip / 2), (length, -si_width / 2), (length, si_width / 2), (0.0, tip / 2)],
            layer=technology.si_layer,
        )
        component.add_port(
            name="o1",
            center=(0.0, 0.0),
            width=sin_width,
            orientation=180,
            layer=technology.sin_layer,
        )
        component.add_port(
            name="o2",
            center=(length, 0.0),
            width=si_width,
            orientation=0,
            layer=technology.si_layer,
        )
        family = "si_sin_transition"
        port_layers = {"o1": technology.sin_layer, "o2": technology.si_layer}
        return BuiltDevice(
            component,
            family,
            values,
            port_layers,
            source_layers=tuple(sorted({technology.sin_layer, technology.si_layer})),
        )
    else:
        supported = (
            "straight, bend_euler, s_bend, taper, mmi_1x2, mmi_2x2, "
            "directional_coupler, crossing, ring_single, si_sin_transition, "
            "registered_gds"
        )
        raise SpecError(f"unsupported component {component_type!r}; supported: {supported}")

    port_layers = {port.name: layer for port in component.ports}
    return BuiltDevice(component, family, values, port_layers, source_layers=(layer,))
