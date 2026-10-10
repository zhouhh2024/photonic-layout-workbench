from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import gdstk

from photonic_cluster_generator.assembly import Assembly, DeviceLibrary
from photonic_cluster_generator.editor.server import (
    _load_workspace_draft,
    _save_workspace_draft,
    build_device_preview,
    build_editor_library,
    export_editor_design,
    snap_editor_instances,
)
from photonic_cluster_generator.library import build_device
from photonic_cluster_generator.models import SpecError, TechnologySpec
from photonic_cluster_generator.onboarding.registry import DeviceRegistry
from photonic_cluster_generator.onboarding.scanner import scan_gds_file
from photonic_cluster_generator.onboarding.server import OnboardingSession, _PAGE, _summary


ROOT = Path(__file__).resolve().parents[1]


def _register_public_test_device(
    root: Path,
    *,
    multilayer: bool = False,
) -> tuple[Path, Path, object]:
    """Create and register synthetic geometry that is safe to redistribute."""
    source = root / ("multilayer_transition.gds" if multilayer else "straight.gds")
    library = gdstk.Library()
    cell = library.new_cell("PUBLIC_TEST_DEVICE")
    if multilayer:
        cell.add(gdstk.rectangle((0.0, -0.5), (5.0, 0.5), layer=263, datatype=0))
        cell.add(gdstk.rectangle((5.0, -0.225), (10.0, 0.225), layer=275, datatype=0))
    else:
        cell.add(gdstk.rectangle((0.0, -0.25), (10.0, 0.25), layer=275, datatype=0))
    library.write_gds(source)

    scan = scan_gds_file(source)
    requested = (
        (("o1", (263, 0), 180, 1.0), ("o2", (275, 0), 0, 0.45))
        if multilayer
        else (("o1", (275, 0), 180, 0.5), ("o2", (275, 0), 0, 0.5))
    )
    selected = []
    for name, layer, orientation, width in requested:
        candidate = next(
            item
            for item in scan.candidates
            if item.layer == layer
            and item.orientation_deg == orientation
            and abs(item.width_um - width) < 1e-9
        )
        selected.append(
            {
                "name": name,
                "candidate_id": candidate.candidate_id,
                "center_um": list(candidate.center_um),
                "width_um": candidate.width_um,
                "orientation_deg": candidate.orientation_deg,
                "layer": list(candidate.layer),
            }
        )
    registry = DeviceRegistry(root / "registry")
    registry.register(
        scan,
        template_id="test_transition" if multilayer else "test_straight",
        family="test_transition" if multilayer else "test_waveguide",
        ports=selected,
    )
    return registry.catalog_path, source, scan


class BlackBoxOnboardingTests(unittest.TestCase):
    def test_web_editor_uses_core_snap_and_exports_real_gds(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            catalog_path, _, _ = _register_public_test_device(root)
            library = build_editor_library(catalog_path)
            straight = next(
                item for item in library if item["key"] == "user:test_straight"
            )
            preview = build_device_preview(straight)
            self.assertEqual({port["name"] for port in preview["ports"]}, {"o1", "o2"})
            self.assertIn("275/0", preview["layers"])
            instances = [
                {
                    "id": "straight_a",
                    "component": "registered_gds",
                    "parameters": straight["parameters"],
                    "placement": {"x_um": 0.0, "y_um": 0.0, "rotation_deg": 0},
                },
                {
                    "id": "straight_b",
                    "component": "registered_gds",
                    "parameters": straight["parameters"],
                    "placement": {"x_um": 11.0, "y_um": 11.0, "rotation_deg": 0},
                },
            ]
            snapped = snap_editor_instances(
                {
                    "instances": instances,
                    "moving_instance": "straight_b",
                    "moving_port": "o1",
                    "target_instance": "straight_a",
                    "target_port": "o2",
                }
            )
            instances[1]["placement"] = snapped["placement"]
            self.assertEqual(snapped["placement"]["rotation_deg"], 0)
            self.assertAlmostEqual(snapped["placement"]["x_um"], 10.0)
            self.assertAlmostEqual(snapped["placement"]["y_um"], 0.0)
            result = export_editor_design(
                {
                    "cluster_name": "WEB_EDITOR_TEST",
                    "instances": instances,
                    "nets": [
                        {
                            "id": "N_1",
                            "terminals": [
                                {"instance": "straight_a", "port": "o2"},
                                {"instance": "straight_b", "port": "o1"},
                            ],
                            "route": {"direct": True},
                        }
                    ],
                },
                directory,
            )
            self.assertTrue(result["validation"]["passed"])
            self.assertTrue(Path(result["gds"]).is_file())
            self.assertTrue(Path(result["preview"]).is_file())

    def test_web_editor_page_javascript_has_valid_syntax(self) -> None:
        node = shutil.which("node")
        if node is None:
            self.skipTest("Node.js is unavailable for JavaScript syntax validation")
        page = (
            ROOT
            / "src"
            / "photonic_cluster_generator"
            / "editor"
            / "static"
            / "index.html"
        ).read_text(encoding="utf-8")
        script = page.split("<script>", 1)[1].split("</script>", 1)[0]
        checked = subprocess.run(
            [node, "--check", "-"],
            input=script,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )
        self.assertEqual(checked.returncode, 0, checked.stderr)
        self.assertIn('href="/register"', page)
        self.assertIn('id="refreshLibrary"', page)
        self.assertIn("const PORT_RADIUS_PX=3", page)
        self.assertIn("updatePortMarkers()", page)
        self.assertNotIn('r="1.35"', page)
        self.assertNotIn('type="number"', page)
        self.assertIn('inputmode="decimal"', page)
        self.assertIn("LABEL_FONT_PX=9", page)
        self.assertIn("function connectedInstanceIds(startId)", page)
        self.assertIn("function rigidTransform(", page)
        self.assertIn("q('#rotate').onclick=rotateSelectedGroup", page)
        self.assertNotIn("请先删除连接再旋转器件", page)
        self.assertIn("NET_LABEL_FONT_PX=8", page)
        self.assertIn("function nextIndexedName(prefix,names)", page)
        self.assertIn("function reindexNets()", page)
        self.assertNotIn("netSeq", page)
        self.assertNotIn("instanceSeq", page)
        self.assertIn("宽度 ${formatMicrons(port.width_um)} μm", page)
        self.assertIn('id="saveDraft"', page)
        self.assertIn("function scheduleDraftSave()", page)
        self.assertIn("api('/api/draft')", page)
        self.assertIn('id="layerControls"', page)
        self.assertIn("function renderLayerControls()", page)
        self.assertIn("filter(port=>isLayerVisible(port.layer))", page)
        self.assertIn('data-port-layer="${port.layer.join', page)

    def test_transition_preview_exposes_two_assembly_layers_and_port_layers(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            catalog_path, _, _ = _register_public_test_device(
                Path(directory), multilayer=True
            )
            item = next(
                value
                for value in build_editor_library(catalog_path)
                if value["key"] == "user:test_transition"
            )
            preview = build_device_preview(item, item["parameters"])
            self.assertEqual(set(preview["layers"]), {"263/0", "275/0"})
            ports = {port["name"]: port for port in preview["ports"]}
            self.assertEqual(ports["o1"]["layer"], [263, 0])
            self.assertEqual(ports["o1"]["width_um"], 1.0)
            self.assertEqual(ports["o2"]["layer"], [275, 0])
            self.assertEqual(ports["o2"]["width_um"], 0.45)

    def test_workspace_draft_round_trip_is_compact_and_validated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "workspace_draft.json"
            saved = _save_workspace_draft(
                path,
                {
                    "cluster_name": "DRAFT_TEST",
                    "instances": [
                        {
                            "id": "mmi_1",
                            "library_key": "parametric:mmi_1x2",
                            "parameters": {"length_mmi_um": 5.5},
                            "placement": {
                                "x_um": 10,
                                "y_um": -2,
                                "rotation_deg": 90,
                            },
                        },
                        {
                            "id": "bend_1",
                            "library_key": "parametric:bend_euler",
                            "parameters": {"radius_um": 12},
                            "placement": {
                                "x_um": 20,
                                "y_um": -2,
                                "rotation_deg": 180,
                            },
                        },
                    ],
                    "nets": [
                        {
                            "id": "N_1",
                            "terminals": [
                                {"instance": "mmi_1", "port": "o2"},
                                {"instance": "bend_1", "port": "o1"},
                            ],
                            "route": {"direct": True},
                        }
                    ],
                    "hidden_layers": ["263/0"],
                },
            )
            loaded = _load_workspace_draft(path)
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded["cluster_name"], "DRAFT_TEST")
            self.assertEqual(len(loaded["instances"]), 2)
            self.assertEqual(loaded["nets"][0]["id"], "N_1")
            self.assertEqual(loaded["hidden_layers"], ["263/0"])
            self.assertNotIn("preview", path.read_text(encoding="utf-8"))
            self.assertEqual(saved["saved_at"], loaded["saved_at"])

            invalid = dict(saved)
            invalid["nets"] = [
                {
                    "id": "N_1",
                    "terminals": [
                        {"instance": "missing", "port": "o1"},
                        {"instance": "bend_1", "port": "o1"},
                    ],
                    "route": {},
                }
            ]
            with self.assertRaisesRegex(SpecError, "invalid terminal"):
                _save_workspace_draft(path, invalid)

    def test_code_assembly_snaps_real_gds_ports_and_exports_verified_gds(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            catalog_path, _, _ = _register_public_test_device(root)
            library = DeviceLibrary(catalog_path)
            self.assertIn("test_straight", library.template_ids())
            assembly = Assembly("CODE_SNAP_TEST", library=library)
            assembly.add_registered("device_a", "test_straight", at=(5.0, 7.0))
            assembly.add_registered("device_b", "test_straight")
            with self.assertRaisesRegex(SpecError, "immutable"):
                assembly.set_parameters("device_a", length_um=20.0)
            assembly.snap("device_b", "o1", "device_a", "o2", net_id="N_SNAP")
            assembly.external("IN", "device_a", "o1")
            assembly.external("OUT", "device_b", "o2")
            left = assembly.port("device_a", "o2")
            right = assembly.port("device_b", "o1")
            self.assertAlmostEqual(left.center_um[0], right.center_um[0], places=6)
            self.assertAlmostEqual(left.center_um[1], right.center_um[1], places=6)
            self.assertAlmostEqual((left.orientation_deg - right.orientation_deg) % 360, 180.0)
            result = assembly.export(root / "output")
            manifest = json.loads(Path(result["manifest"]).read_text(encoding="utf-8"))
            self.assertTrue(result["validation"]["passed"])
            self.assertEqual(manifest["nets"][0]["kind"], "direct")
            self.assertIsNone(manifest["nets"][0]["route_cell"])
            self.assertEqual(result["validation"]["routes"]["N_SNAP"]["child_reference_count"], 0)
            self.assertTrue(result["validation"]["direct_connections"][0]["passed"])

    def test_rejected_property_edit_is_transactional(self) -> None:
        assembly = Assembly("PROPERTY_EDIT_TEST")
        assembly.add_parametric("bus", "straight", length_um=20.0)
        assembly.set_parameters("bus", length_um=30.0)
        self.assertEqual(assembly.to_dict()["instances"][0]["parameters"]["length_um"], 30.0)
        with self.assertRaisesRegex(SpecError, "positive"):
            assembly.set_parameters("bus", length_um=-1.0)
        self.assertEqual(assembly.to_dict()["instances"][0]["parameters"]["length_um"], 30.0)

    def test_geometry_scanner_finds_multilayer_ports_without_labels(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            _, source, scan = _register_public_test_device(
                Path(directory), multilayer=True
            )
            library = gdstk.read_gds(source)
            self.assertEqual(scan.source_layers, ((263, 0), (275, 0)))
            self.assertEqual(sum(len(cell.labels) for cell in library.cells), 0)
            candidates = {
                (item.layer, item.center_um, item.width_um, item.orientation_deg)
                for item in scan.candidates
            }
            self.assertIn(((263, 0), (0.0, 0.0), 1.0, 180), candidates)
            self.assertIn(((275, 0), (10.0, 0.0), 0.45, 0), candidates)

    def test_hierarchical_gds_can_be_registered_and_reimported_as_black_box(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "hierarchical_device.gds"
            library = gdstk.Library()
            child = library.new_cell("BODY")
            child.add(gdstk.rectangle((0.0, -0.25), (10.0, 0.25), layer=275, datatype=0))
            top = library.new_cell("DEVICE_TOP")
            top.add(gdstk.Reference(child, origin=(5.0, 2.0)))
            library.write_gds(source)

            scan = scan_gds_file(source)
            self.assertEqual(scan.hierarchy_cell_count, 2)
            by_orientation = {
                candidate.orientation_deg: candidate
                for candidate in scan.candidates
                if candidate.layer == (275, 0) and candidate.width_um == 0.5
            }
            selected = []
            for name, orientation in (("o1", 180), ("o2", 0)):
                candidate = by_orientation[orientation]
                selected.append(
                    {
                        "name": name,
                        "candidate_id": candidate.candidate_id,
                        "center_um": list(candidate.center_um),
                        "width_um": candidate.width_um,
                        "orientation_deg": candidate.orientation_deg,
                        "layer": list(candidate.layer),
                    }
                )
            registry = DeviceRegistry(root / "registry")
            registry.register(
                scan,
                template_id="hierarchical_straight",
                family="test_waveguide",
                ports=selected,
            )
            workspace_library = build_editor_library(registry.catalog_path)
            self.assertTrue(
                any(
                    item["key"] == "user:hierarchical_straight"
                    for item in workspace_library
                )
            )
            built = build_device(
                "registered_gds",
                {
                    "catalog_path": str(registry.catalog_path),
                    "template_id": "hierarchical_straight",
                },
                TechnologySpec(),
            )
            self.assertEqual(set(port.name for port in built.component.ports), {"o1", "o2"})
            self.assertEqual(built.source_layers, ((275, 0),))
            self.assertGreater(len(list(built.component.insts)), 0)
            catalog = json.loads(registry.catalog_path.read_text(encoding="utf-8"))
            self.assertEqual(catalog["registry_revision"], 1)
            self.assertEqual(catalog["templates"]["hierarchical_straight"]["top_cell"], "DEVICE_TOP")

            with self.assertRaisesRegex(SpecError, "explicit replacement confirmation"):
                registry.register(
                    scan,
                    template_id="hierarchical_straight",
                    family="test_waveguide",
                    ports=selected,
                )
            registry.register(
                scan,
                template_id="hierarchical_straight",
                family="updated_test_waveguide",
                ports=selected,
                replace_existing=True,
            )
            updated_catalog = json.loads(
                registry.catalog_path.read_text(encoding="utf-8")
            )
            self.assertEqual(updated_catalog["registry_revision"], 2)
            self.assertEqual(
                updated_catalog["templates"]["hierarchical_straight"]["family"],
                "updated_test_waveguide",
            )
            archived = updated_catalog["versions"]["hierarchical_straight"]
            self.assertEqual(len(archived), 1)
            self.assertEqual(archived[0]["record"]["family"], "test_waveguide")

    def test_onboarding_ui_uses_free_port_number_and_compact_layer_paths(self) -> None:
        self.assertIn("function nextIndexedName(prefix,names)", _PAGE)
        self.assertIn("function nextPortName()", _PAGE)
        self.assertIn("nextIndexedName('o'", _PAGE)
        self.assertNotIn("selected.size+1", _PAGE)
        self.assertIn('<path d="${d}"', _PAGE)
        self.assertIn('id="sourceFolder"', _PAGE)
        self.assertIn("api('/api/source'", _PAGE)
        self.assertIn('href="/"', _PAGE)
        self.assertIn('id="fitDevice"', _PAGE)
        self.assertIn("svg.onwheel", _PAGE)
        self.assertIn("grid-template-rows:auto minmax(0,1fr) auto", _PAGE)
        self.assertIn('<header><h1>光子版图工作台</h1>', _PAGE)
        self.assertIn('list="familyOptions"', _PAGE)
        self.assertIn('<datalist id="familyOptions">', _PAGE)
        self.assertIn("registry.families", _PAGE)
        side = _PAGE.split('<aside class="side">', 1)[1].split("</aside>", 1)[0]
        self.assertNotIn('class="module-nav"', side)
        node = shutil.which("node")
        if node is not None:
            script = _PAGE.split("<script>", 1)[1].split("</script>", 1)[0]
            checked = subprocess.run(
                [node, "--check", "-"],
                input=script,
                text=True,
                encoding="utf-8",
                capture_output=True,
                check=False,
            )
            self.assertEqual(checked.returncode, 0, checked.stderr)

    def test_onboarding_source_folder_is_selected_at_runtime_transactionally(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source_devices"
            source.mkdir()
            library = gdstk.Library()
            cell = library.new_cell("RUNTIME_SELECTED_DEVICE")
            cell.add(gdstk.rectangle((0.0, -0.25), (12.0, 0.25), layer=275))
            library.write_gds(source / "runtime_device.gds")

            registry = root / "project_local_registry"
            session = OnboardingSession(registry)
            initial = session.state()
            self.assertIsNone(initial["source_folder"])
            self.assertEqual(initial["device_count"], 0)
            self.assertEqual(Path(initial["registry_folder"]), registry.resolve())

            loaded = session.load_source(source)
            self.assertEqual(Path(loaded["source_folder"]), source.resolve())
            self.assertEqual(loaded["device_count"], 1)
            scan_id = session.scans()[0].device_id
            self.assertEqual(session.get_scan(scan_id).relative_path, "runtime_device.gds")
            same_name_wrong_hash = {
                "templates": {
                    "other_device": {
                        "sha256": "not-the-current-gds-hash",
                        "onboarding": {"relative_source": "runtime_device.gds"},
                    }
                }
            }
            self.assertFalse(_summary(session.scans()[0], same_name_wrong_hash)["registered"])

            with self.assertRaisesRegex(SpecError, "does not exist"):
                session.load_source(root / "missing")
            preserved = session.state()
            self.assertEqual(Path(preserved["source_folder"]), source.resolve())
            self.assertEqual(preserved["device_count"], 1)

    def test_registered_template_uses_generic_loader(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            catalog_path, _, _ = _register_public_test_device(Path(directory))
            catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
            record = catalog["templates"]["test_straight"]
            built = build_device(
                "registered_gds",
                {"catalog_path": str(catalog_path), "template_id": "test_straight"},
                TechnologySpec(),
            )
            self.assertEqual(
                set(built.source_layers),
                {tuple(layer) for layer in record["source_layers"]},
            )
            self.assertEqual(
                set(port.name for port in built.component.ports),
                set(record["ports"]),
            )


if __name__ == "__main__":
    unittest.main()
