from __future__ import annotations

from collections import Counter
import json
import math
from pathlib import Path
from typing import Mapping

import gdstk
from PIL import Image, ImageDraw, ImageFont
from shapely.geometry import Point, Polygon
from shapely.ops import unary_union


class ValidationError(RuntimeError):
    """Raised when a written GDS does not preserve the intended hierarchy or routes."""


def _child_cell_name(reference: gdstk.Reference) -> str:
    return reference.cell.name if isinstance(reference.cell, gdstk.Cell) else str(reference.cell)


def _route_geometry(cell: gdstk.Cell, layer: int, datatype: int):
    polygons = cell.get_polygons(
        apply_repetitions=True,
        include_paths=True,
        depth=None,
        layer=layer,
        datatype=datatype,
    )
    shapes = [Polygon(polygon.points) for polygon in polygons if len(polygon.points) >= 3]
    if not shapes:
        raise ValidationError(
            f"route cell {cell.name!r} contains no polygons on layer {layer}/{datatype}"
        )
    return unary_union(shapes)


def _reference_geometry(reference: gdstk.Reference, layer: int, datatype: int):
    polygons = reference.get_polygons(
        apply_repetitions=True,
        include_paths=True,
        depth=None,
        layer=layer,
        datatype=datatype,
    )
    shapes = [Polygon(polygon.points) for polygon in polygons if len(polygon.points) >= 3]
    return unary_union(shapes) if shapes else None


def _rotation_degrees(reference: gdstk.Reference) -> float:
    radians = float(reference.rotation or 0.0)
    return math.degrees(radians) % 360.0


def _match_device_references(
    top: gdstk.Cell,
    manifest: Mapping[str, object],
) -> dict[str, gdstk.Reference]:
    route_cells = {
        str(dict(net)["route_cell"])
        for net in manifest["nets"]
        if dict(net).get("route_cell") is not None
    }
    remaining = [
        reference
        for reference in top.references
        if _child_cell_name(reference) not in route_cells
    ]
    matched: dict[str, gdstk.Reference] = {}
    for raw_instance in manifest["instances"]:
        instance = dict(raw_instance)
        placement = dict(instance["placement"])
        expected_cell = str(instance["source_cell"])
        expected_origin = (float(placement["x_um"]), float(placement["y_um"]))
        expected_rotation = float(placement["rotation_deg"]) % 360.0
        index = next(
            (
                position
                for position, reference in enumerate(remaining)
                if _child_cell_name(reference) == expected_cell
                and math.dist(reference.origin, expected_origin) <= 0.001
                and math.isclose(
                    _rotation_degrees(reference),
                    expected_rotation,
                    rel_tol=0.0,
                    abs_tol=1e-6,
                )
            ),
            None,
        )
        if index is None:
            raise ValidationError(
                f"cannot find disk GDS reference for instance {instance['id']!r} "
                f"({expected_cell} at {expected_origin}, rotation {expected_rotation})"
            )
        matched[str(instance["id"])] = remaining.pop(index)
    if remaining:
        raise ValidationError(
            "disk GDS contains unexpected direct device references: "
            + ", ".join(_child_cell_name(reference) for reference in remaining)
        )
    return matched


def _physical_isolation_evidence(
    top: gdstk.Cell,
    manifest: Mapping[str, object],
    route_geometries: Mapping[str, object],
) -> dict[str, object]:
    references = _match_device_references(top, manifest)
    instance_records = {str(dict(item)["id"]): dict(item) for item in manifest["instances"]}
    device_geometries: dict[tuple[str, tuple[int, int]], object] = {}
    for instance_id, reference in references.items():
        source_layers = instance_records[instance_id].get("source_layers", ())
        if source_layers:
            layers = {
                tuple(int(value) for value in layer)
                for layer in source_layers
            }
        else:
            port_records = dict(instance_records[instance_id]["ports"])
            layers = {
                tuple(int(value) for value in dict(port)["layer"])
                for port in port_records.values()
            }
        for layer, datatype in layers:
            geometry = _reference_geometry(reference, layer, datatype)
            if geometry is not None and not geometry.is_empty:
                device_geometries[(instance_id, (layer, datatype))] = geometry

    device_pairs = []
    net_records = {str(dict(item)["id"]): dict(item) for item in manifest["nets"]}
    direct_contacts = {
        (
            frozenset(
                str(dict(terminal)["instance"])
                for terminal in record["terminals"]
            ),
            tuple(int(value) for value in record["layer"]),
        )
        for record in net_records.values()
        if record.get("kind") == "direct"
    }
    device_keys = list(device_geometries)
    for left_index, (left_id, left_layer) in enumerate(device_keys):
        left_geometry = device_geometries[(left_id, left_layer)]
        for right_id, right_layer in device_keys[left_index + 1 :]:
            if left_id == right_id or left_layer != right_layer:
                continue
            right_geometry = device_geometries[(right_id, right_layer)]
            distance = float(left_geometry.distance(right_geometry))
            overlap_area = float(left_geometry.intersection(right_geometry).area)
            intended_direct = (frozenset((left_id, right_id)), left_layer) in direct_contacts
            passed = overlap_area <= 1e-6 and (
                distance <= 0.003 if intended_direct else distance > 0.001
            )
            device_pairs.append(
                {
                    "instance_a": left_id,
                    "instance_b": right_id,
                    "layer": list(left_layer),
                    "distance_um": distance,
                    "overlap_area_um2": overlap_area,
                    "intended_direct_contact": intended_direct,
                    "passed": passed,
                }
            )
            if not passed:
                expectation = "directly contact" if intended_direct else "remain isolated"
                failure = (
                    "invalid direct contact"
                    if intended_direct
                    else "unexpected touch or overlap"
                )
                raise ValidationError(
                    f"device instances {left_id!r} and {right_id!r} must {expectation} on "
                    f"layer {left_layer}; {failure}: distance={distance} um, "
                    f"overlap={overlap_area} um^2"
                )

    direct_connection_evidence = []
    for net_id, record in net_records.items():
        if record.get("kind") != "direct":
            continue
        left_terminal, right_terminal = [dict(value) for value in record["terminals"]]
        layer = tuple(int(value) for value in record["layer"])
        left_geometry = device_geometries[(str(left_terminal["instance"]), layer)]
        right_geometry = device_geometries[(str(right_terminal["instance"]), layer)]
        endpoint = Point(*[float(value) for value in record["endpoints_um"][0]])
        distance = float(left_geometry.distance(right_geometry))
        overlap_area = float(left_geometry.intersection(right_geometry).area)
        endpoint_distances = [
            float(left_geometry.distance(endpoint)),
            float(right_geometry.distance(endpoint)),
        ]
        passed = (
            distance <= 0.003
            and overlap_area <= 1e-6
            and max(endpoint_distances) <= 0.003
        )
        direct_connection_evidence.append(
            {
                "net": net_id,
                "layer": list(layer),
                "distance_um": distance,
                "overlap_area_um2": overlap_area,
                "endpoint_distances_um": endpoint_distances,
                "passed": passed,
            }
        )
        if not passed:
            raise ValidationError(
                f"direct net {net_id!r} is not a clean port contact after disk round-trip"
            )

    route_device_pairs = []
    for net_id, (route_layer, route_geometry) in route_geometries.items():
        endpoint_instances = {
            str(dict(terminal)["instance"])
            for terminal in net_records[net_id]["terminals"]
        }
        for (instance_id, device_layer), device_geometry in device_geometries.items():
            if device_layer != route_layer:
                continue
            distance = float(route_geometry.distance(device_geometry))
            overlap_area = float(route_geometry.intersection(device_geometry).area)
            is_endpoint = instance_id in endpoint_instances
            passed = overlap_area <= 1e-6 and (
                (is_endpoint and distance <= 0.003)
                or (not is_endpoint and distance > 0.001)
            )
            route_device_pairs.append(
                {
                    "net": net_id,
                    "instance": instance_id,
                    "is_endpoint_device": is_endpoint,
                    "layer": list(route_layer),
                    "distance_um": distance,
                    "overlap_area_um2": overlap_area,
                    "passed": passed,
                }
            )
            if not passed:
                relationship = "endpoint" if is_endpoint else "unrelated"
                raise ValidationError(
                    f"route {net_id!r} has an invalid contact with {relationship} device "
                    f"{instance_id!r} on layer {route_layer}; distance={distance} um, "
                    f"overlap={overlap_area} um^2"
                )
    return {
        "passed": True,
        "device_pairs": device_pairs,
        "direct_connections": direct_connection_evidence,
        "route_device_pairs": route_device_pairs,
    }


def _connected_route_evidence(
    cell: gdstk.Cell,
    layer: int,
    datatype: int,
    endpoints: list[list[float]],
) -> dict[str, object]:
    if not cell.references:
        raise ValidationError(
            f"route cell {cell.name!r} has no bend/straight child references; "
            "the expected routing hierarchy was not retained"
        )
    geometry = _route_geometry(cell, layer, datatype)
    if geometry.geom_type == "Polygon":
        components = [geometry]
    elif geometry.geom_type == "MultiPolygon":
        components = list(geometry.geoms)
    else:
        components = [item for item in getattr(geometry, "geoms", ()) if item.geom_type == "Polygon"]
    tolerance_um = 0.003
    endpoint_components = []
    endpoint_distances = []
    for coordinates in endpoints:
        point = Point(float(coordinates[0]), float(coordinates[1]))
        distances = [component.distance(point) for component in components]
        if not distances:
            raise ValidationError(f"route cell {cell.name!r} has no polygonal connected components")
        best = min(range(len(distances)), key=distances.__getitem__)
        endpoint_components.append(best)
        endpoint_distances.append(float(distances[best]))
    passed = (
        endpoint_components[0] == endpoint_components[1]
        and max(endpoint_distances) <= tolerance_um
    )
    if not passed:
        raise ValidationError(
            f"route cell {cell.name!r} does not connect both declared endpoints; "
            f"component indexes={endpoint_components}, distances_um={endpoint_distances}"
        )
    return {
        "passed": True,
        "child_reference_count": len(cell.references),
        "child_cells": [_child_cell_name(reference) for reference in cell.references],
        "polygon_component_count": len(components),
        "endpoint_component_indexes": endpoint_components,
        "endpoint_distances_um": endpoint_distances,
        "tolerance_um": tolerance_um,
    }


def validate_written_gds(
    gds_path: str | Path,
    manifest: Mapping[str, object],
) -> dict[str, object]:
    path = Path(gds_path).resolve()
    library = gdstk.read_gds(path)
    top_levels = library.top_level()
    top_names = [cell.name for cell in top_levels]
    expected_top = str(manifest["top_cell"])
    if top_names != [expected_top]:
        raise ValidationError(
            f"expected exactly one top-level cell {expected_top!r}, found {top_names}"
        )
    if len(library.cells) <= 1:
        raise ValidationError("written GDS is flat; expected a multi-cell hierarchy")
    if not math.isclose(float(library.unit), 1e-6, rel_tol=0.0, abs_tol=1e-15):
        raise ValidationError(f"expected GDS user unit 1e-6 m, found {library.unit}")
    if float(library.precision) > 1e-9:
        raise ValidationError(
            f"GDS precision {library.precision} m is coarser than the required 1 nm"
        )

    cells = {cell.name: cell for cell in library.cells}
    top = cells[expected_top]
    actual_children = Counter(_child_cell_name(reference) for reference in top.references)
    expected_children = Counter(
        {str(key): int(value) for key, value in dict(manifest["expected_top_direct_child_counts"]).items()}
    )
    if actual_children != expected_children:
        raise ValidationError(
            "top-level direct references differ after disk round-trip: "
            f"expected={dict(expected_children)}, actual={dict(actual_children)}"
        )

    instance_layer_fidelity: dict[str, object] = {}
    for raw_instance in manifest["instances"]:
        instance = dict(raw_instance)
        instance_id = str(instance["id"])
        source_cell = str(instance["source_cell"])
        if source_cell not in cells:
            raise ValidationError(
                f"instance {instance_id!r} source cell {source_cell!r} is missing"
            )
        actual_layers = {
            (int(polygon.layer), int(polygon.datatype))
            for polygon in cells[source_cell].get_polygons(
                apply_repetitions=True,
                include_paths=True,
                depth=None,
            )
        }
        expected_layers = {
            tuple(int(value) for value in layer)
            for layer in instance.get("source_layers", [])
        }
        if actual_layers != expected_layers:
            raise ValidationError(
                f"instance {instance_id!r} did not retain its exact source layers; "
                f"expected={sorted(expected_layers)}, actual={sorted(actual_layers)}"
            )
        instance_layer_fidelity[instance_id] = {
            "passed": True,
            "source_cell": source_cell,
            "layers": [list(layer) for layer in sorted(actual_layers)],
        }

    output = dict(manifest["output"])
    technology = dict(manifest["technology"])
    external_names = [str(dict(port)["name"]) for port in manifest["external_ports"]]
    label_evidence: dict[str, object] = {"enabled": bool(output["write_port_labels"])}
    if output["write_port_labels"]:
        label_layer, label_type = [int(value) for value in technology["label_layer"]]
        actual_labels = sorted(
            label.text
            for label in top.labels
            if int(label.layer) == label_layer and int(label.texttype) == label_type
        )
        expected_labels = sorted(external_names)
        if actual_labels != expected_labels:
            raise ValidationError(
                "external port labels differ after disk round-trip: "
                f"expected={expected_labels}, actual={actual_labels}"
            )
        label_evidence.update({"passed": True, "labels": actual_labels})
    pin_evidence: dict[str, object] = {"enabled": bool(output["write_pin_markers"])}
    if output["write_pin_markers"]:
        pin_layer, pin_type = [int(value) for value in technology["pin_layer"]]
        pin_polygons = top.get_polygons(
            apply_repetitions=True,
            include_paths=True,
            depth=0,
            layer=pin_layer,
            datatype=pin_type,
        )
        if len(pin_polygons) != len(external_names):
            raise ValidationError(
                f"expected {len(external_names)} top-level pin markers, found {len(pin_polygons)}"
            )
        pin_evidence.update({"passed": True, "polygon_count": len(pin_polygons)})

    route_results: dict[str, object] = {}
    route_geometries: dict[str, object] = {}
    for raw_net in manifest["nets"]:
        net = dict(raw_net)
        if net.get("kind") == "direct":
            route_results[str(net["id"])] = {
                "passed": True,
                "kind": "direct",
                "child_reference_count": 0,
                "child_cells": [],
                "endpoint_distances_um": [0.0, 0.0],
                "tolerance_um": 0.003,
            }
            continue
        cell_name = str(net["route_cell"])
        if cell_name not in cells:
            raise ValidationError(f"route cell {cell_name!r} is missing after disk round-trip")
        layer, datatype = [int(value) for value in net["layer"]]
        route_geometries[str(net["id"])] = (
            (layer, datatype),
            _route_geometry(cells[cell_name], layer, datatype),
        )
        route_results[str(net["id"])] = _connected_route_evidence(
            cells[cell_name],
            layer,
            datatype,
            list(net["endpoints_um"]),
        )

    isolation_results = []
    route_ids = list(route_geometries)
    for left_index, left_id in enumerate(route_ids):
        left_layer, left_geometry = route_geometries[left_id]
        for right_id in route_ids[left_index + 1 :]:
            right_layer, right_geometry = route_geometries[right_id]
            if left_layer != right_layer:
                continue
            distance = float(left_geometry.distance(right_geometry))
            overlap_area = float(left_geometry.intersection(right_geometry).area)
            isolation_results.append(
                {
                    "net_a": left_id,
                    "net_b": right_id,
                    "layer": list(left_layer),
                    "distance_um": distance,
                    "overlap_area_um2": overlap_area,
                    "passed": distance > 0.001 and overlap_area <= 1e-6,
                }
            )
            if distance <= 0.001 or overlap_area > 1e-6:
                raise ValidationError(
                    f"routes {left_id!r} and {right_id!r} touch or overlap on layer "
                    f"{left_layer}; distance={distance} um, overlap={overlap_area} um^2. "
                    "Insert an explicit crossing device or change the route waypoints."
                )

    physical_isolation = _physical_isolation_evidence(top, manifest, route_geometries)

    return {
        "passed": True,
        "gds_path": str(path),
        "top_level_cells": top_names,
        "cell_count": len(library.cells),
        "top_direct_reference_count": len(top.references),
        "top_direct_child_counts": dict(actual_children),
        "hierarchy_retained": len(library.cells) > 1 and bool(top.references),
        "instance_layer_fidelity": instance_layer_fidelity,
        "gds_unit_m": float(library.unit),
        "gds_precision_m": float(library.precision),
        "external_port_evidence": {
            "labels": label_evidence,
            "pin_markers": pin_evidence,
        },
        "routes": route_results,
        "route_isolation": isolation_results,
        "direct_connections": physical_isolation["direct_connections"],
        "physical_isolation": physical_isolation,
    }


def render_annotated_preview(
    gds_path: str | Path,
    manifest: Mapping[str, object],
    output_path: str | Path,
    image_size: tuple[int, int] = (1800, 650),
) -> Path:
    library = gdstk.read_gds(Path(gds_path))
    top = next(cell for cell in library.top_level() if cell.name == manifest["top_cell"])
    polygons = top.get_polygons(apply_repetitions=True, include_paths=True, depth=None)
    all_points = [point for polygon in polygons for point in polygon.points]
    if not all_points:
        raise ValidationError("cannot render preview: top cell has no polygons")
    min_x = min(point[0] for point in all_points)
    max_x = max(point[0] for point in all_points)
    min_y = min(point[1] for point in all_points)
    max_y = max(point[1] for point in all_points)
    margin = 70
    width, height = image_size
    scale = min(
        (width - 2 * margin) / max(max_x - min_x, 1e-9),
        (height - 2 * margin) / max(max_y - min_y, 1e-9),
    )

    def transform(x: float, y: float) -> tuple[float, float]:
        return (
            margin + (x - min_x) * scale,
            height - margin - (y - min_y) * scale,
        )

    technology = dict(manifest["technology"])
    layer_colors = {
        tuple(technology["si_layer"]): (47, 129, 247, 210),
        tuple(technology["sin_layer"]): (40, 200, 155, 210),
        tuple(technology["pin_layer"]): (255, 194, 68, 230),
    }
    image = Image.new("RGBA", image_size, (18, 22, 29, 255))
    draw = ImageDraw.Draw(image, "RGBA")
    for polygon in polygons:
        layer_key = (int(polygon.layer), int(polygon.datatype))
        color = layer_colors.get(layer_key, (130, 140, 155, 120))
        draw.polygon([transform(float(x), float(y)) for x, y in polygon.points], fill=color)

    try:
        font = ImageFont.truetype("arial.ttf", 14)
        title_font = ImageFont.truetype("arial.ttf", 16)
    except OSError:
        font = ImageFont.load_default()
        title_font = font

    cells = {cell.name: cell for cell in library.cells}
    route_colors = (
        (255, 112, 92, 255),
        (192, 124, 255, 255),
        (74, 224, 210, 255),
        (255, 165, 70, 255),
    )
    for index, raw_net in enumerate(manifest["nets"]):
        net = dict(raw_net)
        color = route_colors[index % len(route_colors)]
        if net.get("kind") == "direct":
            endpoint = list(net["endpoints_um"])[0]
            text_x, text_y = transform(float(endpoint[0]), float(endpoint[1]))
            draw.ellipse(
                (text_x - 4, text_y - 4, text_x + 4, text_y + 4),
                fill=color,
            )
            draw.text(
                (text_x + 6, text_y - 18),
                str(net["id"]),
                fill=color,
                font=font,
            )
            continue
        layer, datatype = [int(value) for value in net["layer"]]
        route_cell = cells[str(net["route_cell"])]
        for polygon in route_cell.get_polygons(
            apply_repetitions=True,
            include_paths=True,
            depth=None,
            layer=layer,
            datatype=datatype,
        ):
            points = [transform(float(x), float(y)) for x, y in polygon.points]
            draw.line(points + [points[0]], fill=color, width=2)
        endpoints = list(net["endpoints_um"])
        center_x = (float(endpoints[0][0]) + float(endpoints[1][0])) / 2.0
        center_y = (float(endpoints[0][1]) + float(endpoints[1][1])) / 2.0
        text_x, text_y = transform(center_x, center_y)
        vertical_offset = -24 if index % 2 == 0 else 8
        draw.text(
            (text_x - 30, text_y + vertical_offset),
            str(net["id"]),
            fill=color,
            font=font,
        )

    for raw_instance in manifest["instances"]:
        instance = dict(raw_instance)
        left, bottom, right, top_y = [float(value) for value in instance["bbox_um"]]
        x1, y1 = transform(left, top_y)
        x2, y2 = transform(right, bottom)
        draw.rectangle((x1, y1, x2, y2), outline=(255, 218, 90, 255), width=2)
        draw.text((x1 + 4, y1 + 4), str(instance["id"]), fill=(255, 245, 200, 255), font=font)

    for raw_port in manifest["external_ports"]:
        port = dict(raw_port)
        x, y = transform(*[float(value) for value in port["center_um"]])
        radius_px = 5
        draw.ellipse(
            (x - radius_px, y - radius_px, x + radius_px, y + radius_px),
            fill=(255, 80, 100, 255),
        )
        draw.text((x + 8, y - 8), str(port["name"]), fill=(255, 150, 165, 255), font=font)

    title = f"{manifest['cluster_name']}  |  hierarchical LVS cluster"
    draw.text((margin, 22), title, fill=(240, 244, 252, 255), font=title_font)
    destination = Path(output_path).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    image.convert("RGB").save(destination)
    return destination


def render_device_atlas(
    gds_path: str | Path,
    manifest: Mapping[str, object],
    output_path: str | Path,
    *,
    columns: int = 4,
    tile_size: tuple[int, int] = (420, 290),
) -> Path:
    """Render every imported device at its own scale with all source layers and ports."""
    library = gdstk.read_gds(Path(gds_path))
    cells = {cell.name: cell for cell in library.cells}
    instances = [dict(value) for value in manifest["instances"]]
    rows = math.ceil(len(instances) / columns)
    tile_width, tile_height = tile_size
    image = Image.new(
        "RGBA",
        (columns * tile_width, rows * tile_height),
        (15, 19, 26, 255),
    )
    draw = ImageDraw.Draw(image, "RGBA")
    try:
        font = ImageFont.truetype("arial.ttf", 13)
        title_font = ImageFont.truetype("arialbd.ttf", 15)
    except OSError:
        font = ImageFont.load_default()
        title_font = font
    palette = (
        (76, 201, 240, 185),
        (247, 37, 133, 175),
        (184, 243, 90, 175),
        (247, 178, 103, 175),
        (179, 136, 255, 175),
        (82, 214, 166, 175),
        (255, 223, 107, 175),
        (232, 121, 249, 175),
    )

    for index, instance in enumerate(instances):
        column = index % columns
        row = index // columns
        origin_x = column * tile_width
        origin_y = row * tile_height
        draw.rectangle(
            (origin_x + 4, origin_y + 4, origin_x + tile_width - 5, origin_y + tile_height - 5),
            fill=(22, 29, 39, 255),
            outline=(55, 67, 84, 255),
            width=1,
        )
        cell_name = str(instance["source_cell"])
        if cell_name not in cells:
            raise ValidationError(f"atlas source cell is missing: {cell_name}")
        polygons = cells[cell_name].get_polygons(
            apply_repetitions=True,
            include_paths=True,
            depth=None,
        )
        points = [point for polygon in polygons for point in polygon.points]
        if not points:
            continue
        min_x = min(float(point[0]) for point in points)
        max_x = max(float(point[0]) for point in points)
        min_y = min(float(point[1]) for point in points)
        max_y = max(float(point[1]) for point in points)
        header = 58
        margin = 18
        plot_width = tile_width - 2 * margin
        plot_height = tile_height - header - margin
        scale = min(
            plot_width / max(max_x - min_x, 1e-9),
            plot_height / max(max_y - min_y, 1e-9),
        )

        def local_transform(x: float, y: float) -> tuple[float, float]:
            return (
                origin_x + margin + (x - min_x) * scale,
                origin_y + tile_height - margin - (y - min_y) * scale,
            )

        source_layers = [tuple(int(v) for v in layer) for layer in instance["source_layers"]]
        layer_colors = {
            layer: palette[layer_index % len(palette)]
            for layer_index, layer in enumerate(source_layers)
        }
        for polygon in polygons:
            layer = int(polygon.layer), int(polygon.datatype)
            draw.polygon(
                [local_transform(float(x), float(y)) for x, y in polygon.points],
                fill=layer_colors.get(layer, (135, 145, 160, 120)),
            )
        draw.text(
            (origin_x + 12, origin_y + 10),
            f"{instance['id']}  |  {instance['family']}",
            fill=(244, 247, 252, 255),
            font=title_font,
        )
        layer_text = "layers: " + ", ".join(f"{a}/{b}" for a, b in source_layers)
        if len(layer_text) > 63:
            layer_text = layer_text[:60] + "..."
        draw.text(
            (origin_x + 12, origin_y + 33),
            layer_text,
            fill=(160, 174, 194, 255),
            font=font,
        )
        placement = dict(instance["placement"])
        translation = float(placement["x_um"]), float(placement["y_um"])
        rotation = math.radians(float(placement["rotation_deg"]))
        cosine, sine = math.cos(rotation), math.sin(rotation)
        for port_name, raw_port in dict(instance["ports"]).items():
            port = dict(raw_port)
            global_x, global_y = [float(value) for value in port["center_um"]]
            dx, dy = global_x - translation[0], global_y - translation[1]
            local_x = cosine * dx + sine * dy
            local_y = -sine * dx + cosine * dy
            px, py = local_transform(local_x, local_y)
            draw.ellipse((px - 4, py - 4, px + 4, py + 4), fill=(255, 74, 106, 255))
            local_orientation = math.radians(
                (float(port["orientation_deg"]) - float(placement["rotation_deg"])) % 360
            )
            arrow = 13
            end_x = px + arrow * math.cos(local_orientation)
            end_y = py - arrow * math.sin(local_orientation)
            draw.line((px, py, end_x, end_y), fill=(255, 222, 102, 255), width=2)
            draw.text((px + 5, py - 15), str(port_name), fill=(255, 235, 164, 255), font=font)

    destination = Path(output_path).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    image.convert("RGB").save(destination)
    return destination


def load_manifest(path: str | Path) -> dict[str, object]:
    return json.loads(Path(path).read_text(encoding="utf-8"))
