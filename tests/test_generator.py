from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

import gdstk

from photonic_cluster_generator.generator import generate_cluster
from photonic_cluster_generator.library import build_device
from photonic_cluster_generator.models import SpecError, TechnologySpec, parse_cluster_spec
from photonic_cluster_generator.validator import ValidationError


ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "lvs_mzi_cluster.json"


class GeneratorTests(unittest.TestCase):
    def test_generates_hierarchical_connected_gds_and_lvs_netlist(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = generate_cluster(EXAMPLE, directory)
            manifest = json.loads(Path(result["manifest"]).read_text(encoding="utf-8"))
            library = gdstk.read_gds(result["gds"])

            self.assertTrue(result["validation"]["passed"])
            self.assertTrue(result["validation"]["hierarchy_retained"])
            self.assertEqual([cell.name for cell in library.top_level()], ["LVS_MZI_CLUSTER"])
            self.assertGreater(len(library.cells), 1)
            self.assertEqual(len(manifest["instances"]), 3)
            self.assertEqual(len(manifest["nets"]), 3)
            self.assertTrue(
                all(net["passed"] for net in result["validation"]["routes"].values())
            )
            self.assertTrue(
                all(
                    net["child_reference_count"] > 0
                    for net in result["validation"]["routes"].values()
                )
            )
            self.assertTrue(
                all(pair["passed"] for pair in result["validation"]["route_isolation"])
            )
            self.assertEqual(
                result["validation"]["external_port_evidence"]["labels"]["labels"],
                ["IN", "OUT"],
            )
            self.assertEqual(
                result["validation"]["external_port_evidence"]["pin_markers"]["polygon_count"],
                2,
            )

            netlist = Path(result["netlist"]).read_text(encoding="utf-8")
            self.assertIn(".subckt LVS_MZI_CLUSTER IN OUT", netlist)
            self.assertEqual(netlist.count("\nX"), 3)
            self.assertIn("Xsplitter IN N_UPPER_LEFT N_LOWER mmi_1x2", netlist)
            self.assertIn(
                "Xupper_phase N_UPPER_LEFT N_UPPER_RIGHT straight_waveguide",
                netlist,
            )
            self.assertIn("Xcombiner OUT N_LOWER N_UPPER_RIGHT mmi_1x2", netlist)
            self.assertIn(".ends LVS_MZI_CLUSTER", netlist)
            self.assertGreater(Path(result["preview"]).stat().st_size, 1000)
            self.assertTrue(Path(result["validation_report"]).is_file())

    def test_all_declared_standard_devices_build(self) -> None:
        technology = TechnologySpec()
        supported = (
            "straight",
            "bend_euler",
            "s_bend",
            "taper",
            "mmi_1x2",
            "mmi_2x2",
            "directional_coupler",
            "crossing",
            "ring_single",
            "si_sin_transition",
        )
        for kind in supported:
            with self.subTest(kind=kind):
                built = build_device(kind, {}, technology)
                self.assertGreater(len(list(built.component.ports)), 0)
                self.assertGreater(len(built.component.get_polygons()), 0)

    def test_non_collinear_waypoint_route_keeps_bends_and_physical_length(self) -> None:
        data = {
            "schema_version": "1.0.0",
            "cluster_name": "WAYPOINT_CLUSTER",
            "instances": [
                {
                    "id": "left",
                    "component": "straight",
                    "parameters": {"length_um": 10.0, "width_um": 0.6},
                    "placement": {"x_um": 0.0, "y_um": 0.0},
                },
                {
                    "id": "right",
                    "component": "straight",
                    "parameters": {"length_um": 10.0, "width_um": 0.6},
                    "placement": {"x_um": 80.0, "y_um": 30.0, "rotation_deg": 180},
                },
            ],
            "nets": [
                {
                    "id": "N_BENT",
                    "terminals": [
                        {"instance": "left", "port": "o2"},
                        {"instance": "right", "port": "o2"},
                    ],
                    "route": {"waypoints_um": [[30.0, 0.0], [30.0, 30.0]]},
                }
            ],
            "external_ports": [
                {"name": "IN", "terminal": {"instance": "left", "port": "o1"}},
                {"name": "OUT", "terminal": {"instance": "right", "port": "o1"}},
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            result = generate_cluster(parse_cluster_spec(data), directory)
            manifest = json.loads(Path(result["manifest"]).read_text(encoding="utf-8"))
            route = manifest["nets"][0]
            self.assertAlmostEqual(route["length_straights_um"], 50.0, places=3)
            self.assertAlmostEqual(route["length_um"], 83.274, places=3)
            self.assertEqual(
                result["validation"]["routes"]["N_BENT"]["child_reference_count"],
                5,
            )

    def test_rejects_overlapping_device_instances_after_disk_roundtrip(self) -> None:
        data = {
            "schema_version": "1.0.0",
            "cluster_name": "OVERLAP_CLUSTER",
            "instances": [
                {
                    "id": "a",
                    "component": "straight",
                    "parameters": {"length_um": 10.0},
                    "placement": {"x_um": 0.0, "y_um": 0.0},
                },
                {
                    "id": "b",
                    "component": "straight",
                    "parameters": {"length_um": 10.0},
                    "placement": {"x_um": 0.0, "y_um": 0.0},
                },
            ],
            "nets": [],
            "external_ports": [
                {"name": "A1", "terminal": {"instance": "a", "port": "o1"}},
                {"name": "A2", "terminal": {"instance": "a", "port": "o2"}},
                {"name": "B1", "terminal": {"instance": "b", "port": "o1"}},
                {"name": "B2", "terminal": {"instance": "b", "port": "o2"}},
            ],
            "require_connected_device_graph": False,
        }
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValidationError, "touch or overlap"):
                generate_cluster(parse_cluster_spec(data), directory)
            self.assertFalse((Path(directory) / "OVERLAP_CLUSTER.gds").exists())
            self.assertEqual(list(Path(directory).glob(".*.candidate.*")), [])

    def test_rejects_crossing_routes_without_explicit_crossing_device(self) -> None:
        data = {
            "schema_version": "1.0.0",
            "cluster_name": "ROUTE_SHORT_CLUSTER",
            "instances": [
                {"id": "left", "component": "straight", "parameters": {"length_um": 10.0}, "placement": {"x_um": -50.0, "y_um": 0.0}},
                {"id": "right", "component": "straight", "parameters": {"length_um": 10.0}, "placement": {"x_um": 50.0, "y_um": 0.0, "rotation_deg": 180}},
                {"id": "bottom", "component": "straight", "parameters": {"length_um": 10.0}, "placement": {"x_um": 0.0, "y_um": -50.0, "rotation_deg": 90}},
                {"id": "top", "component": "straight", "parameters": {"length_um": 10.0}, "placement": {"x_um": 0.0, "y_um": 50.0, "rotation_deg": 270}}
            ],
            "nets": [
                {"id": "HORIZONTAL", "terminals": [{"instance": "left", "port": "o2"}, {"instance": "right", "port": "o2"}]},
                {"id": "VERTICAL", "terminals": [{"instance": "bottom", "port": "o2"}, {"instance": "top", "port": "o2"}]}
            ],
            "external_ports": [
                {"name": "WEST", "terminal": {"instance": "left", "port": "o1"}},
                {"name": "EAST", "terminal": {"instance": "right", "port": "o1"}},
                {"name": "SOUTH", "terminal": {"instance": "bottom", "port": "o1"}},
                {"name": "NORTH", "terminal": {"instance": "top", "port": "o1"}}
            ],
            "require_connected_device_graph": False
        }
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValidationError, "routes .* touch or overlap"):
                generate_cluster(parse_cluster_spec(data), directory)

    def test_explicit_si_sin_transition_connects_two_material_layers(self) -> None:
        data = {
            "schema_version": "1.0.0",
            "cluster_name": "SI_SIN_CLUSTER",
            "instances": [
                {
                    "id": "sin_wg",
                    "component": "straight",
                    "parameters": {"material": "sin", "length_um": 10.0, "width_um": 1.0},
                    "placement": {"x_um": 0.0, "y_um": 0.0},
                },
                {
                    "id": "transition",
                    "component": "si_sin_transition",
                    "parameters": {"length_um": 30.0, "sin_width_um": 1.0, "si_width_um": 0.6},
                    "placement": {"x_um": 30.0, "y_um": 0.0},
                },
                {
                    "id": "si_wg",
                    "component": "straight",
                    "parameters": {"material": "si", "length_um": 10.0, "width_um": 0.6},
                    "placement": {"x_um": 80.0, "y_um": 0.0},
                },
            ],
            "nets": [
                {
                    "id": "N_SIN",
                    "terminals": [
                        {"instance": "sin_wg", "port": "o2"},
                        {"instance": "transition", "port": "o1"},
                    ],
                },
                {
                    "id": "N_SI",
                    "terminals": [
                        {"instance": "transition", "port": "o2"},
                        {"instance": "si_wg", "port": "o1"},
                    ],
                },
            ],
            "external_ports": [
                {"name": "SIN_IN", "terminal": {"instance": "sin_wg", "port": "o1"}},
                {"name": "SI_OUT", "terminal": {"instance": "si_wg", "port": "o2"}},
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            result = generate_cluster(parse_cluster_spec(data), directory)
            self.assertTrue(result["validation"]["passed"])
            layers = {
                tuple(net["layer"])
                for net in json.loads(Path(result["manifest"]).read_text(encoding="utf-8"))["nets"]
            }
            self.assertEqual(layers, {(263, 0), (275, 0)})

    def test_rejects_terminal_reuse(self) -> None:
        data = json.loads(EXAMPLE.read_text(encoding="utf-8"))
        data["nets"].append(
            {
                "id": "BAD_REUSE",
                "terminals": [
                    {"instance": "splitter", "port": "o2"},
                    {"instance": "combiner", "port": "o2"},
                ],
            }
        )
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(SpecError, "is reused by nets"):
                generate_cluster(parse_cluster_spec(data), directory)

    def test_rejects_dangling_device_port(self) -> None:
        data = json.loads(EXAMPLE.read_text(encoding="utf-8"))
        data["nets"] = data["nets"][:-1]
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(SpecError, "unassigned"):
                generate_cluster(parse_cluster_spec(data), directory)

    def test_rejects_direct_cross_material_net(self) -> None:
        data = {
            "schema_version": "1.0.0",
            "cluster_name": "BAD_CROSS_MATERIAL",
            "instances": [
                {
                    "id": "si",
                    "component": "straight",
                    "parameters": {"material": "si", "length_um": 10.0, "width_um": 0.6},
                    "placement": {"x_um": 0.0, "y_um": 0.0},
                },
                {
                    "id": "sin",
                    "component": "straight",
                    "parameters": {"material": "sin", "length_um": 10.0, "width_um": 1.0},
                    "placement": {"x_um": 30.0, "y_um": 0.0},
                },
            ],
            "nets": [
                {
                    "id": "BAD",
                    "terminals": [
                        {"instance": "si", "port": "o2"},
                        {"instance": "sin", "port": "o1"},
                    ],
                }
            ],
            "external_ports": [
                {"name": "IN", "terminal": {"instance": "si", "port": "o1"}},
                {"name": "OUT", "terminal": {"instance": "sin", "port": "o2"}},
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(SpecError, "different layers"):
                generate_cluster(parse_cluster_spec(data), directory)


if __name__ == "__main__":
    unittest.main()
