from __future__ import annotations

import math


def _same_point(
    left: tuple[float, float],
    right: tuple[float, float],
    *,
    tolerance: float = 1e-12,
) -> bool:
    return math.isclose(left[0], right[0], rel_tol=0.0, abs_tol=tolerance) and math.isclose(
        left[1], right[1], rel_tol=0.0, abs_tol=tolerance
    )


def _strictly_collinear_middle(
    previous: tuple[float, float],
    current: tuple[float, float],
    following: tuple[float, float],
) -> bool:
    incoming = current[0] - previous[0], current[1] - previous[1]
    outgoing = following[0] - current[0], following[1] - current[1]
    incoming_length = math.hypot(*incoming)
    outgoing_length = math.hypot(*outgoing)
    if incoming_length <= 1e-15 or outgoing_length <= 1e-15:
        return True
    cross = incoming[0] * outgoing[1] - incoming[1] * outgoing[0]
    scale = max(1.0, incoming_length * outgoing_length)
    same_direction = incoming[0] * outgoing[0] + incoming[1] * outgoing[1] > 0.0
    return abs(cross) <= 1e-12 * scale and same_direction


def exact_preview_points(points: object) -> list[list[float]]:
    """Return an exact polygon outline with only redundant points removed.

    Fixed-stride sampling is unsafe for polygons: skipping a corner makes SVG
    connect non-adjacent vertices with a diagonal and can show a false gap at a
    physical port.  This reducer removes only consecutive duplicates and
    strictly collinear middle points.  Every turn and every curved vertex is
    retained, so the rendered outline follows the source polygon boundary.
    """
    values: list[tuple[float, float]] = []
    for x, y in points:
        point = float(x), float(y)
        if not values or not _same_point(values[-1], point):
            values.append(point)
    if len(values) > 1 and _same_point(values[0], values[-1]):
        values.pop()
    if len(values) <= 3:
        return [[round(x, 6), round(y, 6)] for x, y in values]

    while len(values) > 3:
        keep = [
            point
            for index, point in enumerate(values)
            if not _strictly_collinear_middle(
                values[index - 1],
                point,
                values[(index + 1) % len(values)],
            )
        ]
        if len(keep) < 3 or len(keep) == len(values):
            break
        values = keep
    return [[round(x, 6), round(y, 6)] for x, y in values]
