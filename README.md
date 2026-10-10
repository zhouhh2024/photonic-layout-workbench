# Photonic Cluster Generator

**English** | [简体中文](README.zh-CN.md)

A unified workbench for photonic device registration, interactive layout assembly, and pre-LVS validation.

The project works directly with hierarchical, multilayer GDS geometry. It does not use cell names, port labels, or text markers to infer device geometry. Standalone devices are registered through geometry-derived port candidates and manual confirmation, then assembled alongside parametric devices into connected cluster layouts.

## Features

- **Device registration**: scan standalone GDS polygons, generate layer-specific port candidates, and confirm real ports manually.
- **Versioned device library**: identify source layouts by SHA-256 and preserve previous revisions when a template is updated.
- **Layout assembly**: drag and drop devices, snap ports, route connections, and move or rotate connected groups as rigid bodies.
- **Multilayer canvas**: show or hide geometry, ports, and connections independently by `(layer/datatype)`.
- **Workspace drafts**: automatically save templates, parameters, poses, connections, and layer visibility, then restore them after a restart.
- **Validation evidence**: reopen exported GDS files and verify hierarchy, ports, connectivity, collisions, and netlist consistency while producing annotated images.

Device registration and layout assembly run in one web service and share the same `device_registry`. After registering a device, refresh the library in the assembly view.

## Environment Setup

### Recommended: create a dedicated Conda environment

Conda or Miniconda and Windows PowerShell are required. Node.js is used only for checking web-script syntax and is not required to run the workbench.

```powershell
Set-Location <PhotonicClusterGenerator-directory>
conda env create -f environment.yml
conda activate photonic-cluster-generator
```

`environment.yml` pins the direct dependency versions used for the complete test suite:

| Component | Version |
|---|---:|
| Python | 3.10 |
| gdsfactory | 8.32.2 |
| gdstk | 1.0.0 |
| NumPy | 2.2.6 |
| Pillow | 12.0.0 |
| Shapely | 2.1.2 |

### Use an existing Conda environment

An existing `gdsfactory` environment can also be used:

```powershell
conda activate gdsfactory
python -m pip install -e .
```

The launch scripts prefer the interpreter in the active `CONDA_PREFIX` and contain no machine-specific absolute paths. An interpreter can also be selected explicitly:

```powershell
.\run_tests.ps1 -Python "C:\path\to\python.exe"
```

## Quick Start

After activating the environment, run:

```powershell
.\run_photonic_workspace.ps1
```

Open <http://127.0.0.1:8765/> in a browser.

To open the device registration view directly:

```powershell
.\run_photonic_workspace.ps1 -StartPage register
```

Keep the PowerShell window open while the service is running. If port 8765 is occupied by an earlier process, close the old workbench before starting another instance.

## Device Registration

1. Open **Device Registration** in the top navigation bar.
2. Enter a directory containing standalone device GDS files and scan it.
3. Select a device and inspect its geometry and port candidates layer by layer.
4. Select the real ports and assign their names.
5. Select an existing device family or enter a new family name.
6. Assign a unique template ID and save the registration.

Port candidates are derived from polygon boundaries on each layer. Candidate generation and registration do not read cell names, port objects, or text labels. A manually confirmed port must also pass checks for boundary placement, inward cross-section coverage, width, and orientation in 90-degree increments.

Directory scanning evaluates the actual polygon content of each top-level cell. Editor context roots that contain no positive-area polygon geometry—such as text-only or zero-area placeholders—are ignored automatically. If a GDS file contains multiple top-level cells with valid geometry, each is shown as a separate candidate and retains its explicit top-cell selection. Cell names are not used to determine the device body.

### Device identity and revisions

- A **template ID** identifies one specific GDS template version.
- A **device family** is a functional category and enables switching between physical GDS variants from the same family during assembly.
- Registered identity is based on the GDS content SHA-256, not the filename.
- Updating an existing template requires explicit confirmation; the previous record is archived under `versions`.
- The original source directory is always read-only and is never modified by registration.

Registration data is stored in:

```text
device_registry/
├── catalog.json
└── templates/
    └── <template_id>__<sha256>.gds
```

This directory is local project data. It contains GDS copies, port coordinates, hashes, and revision history, is excluded by `.gitignore`, and must not be committed to a public repository. Use access-controlled private storage if a backup is required.

## Layout Assembly

The assembly module loads two types of devices:

- standard parametric devices;
- manually registered devices from `device_registry`.

### Basic operation

1. Drag a device from the left panel or double-click it to place it at the canvas center.
2. Use the layer bar in the upper-left corner to show or hide each `(layer/datatype)`.
3. Click two unused ports to connect them, or drag ports near each other for automatic snapping.
4. Dragging a connected device moves its entire connected component while preserving relative poses.
5. **Rotate 90°** applies a rigid-body rotation to the entire connected component.
6. Right-click a device to edit its position, rotation, and variable parameters.
7. Set a layout name and select **Export and Validate GDS**.

Device and connection names appear only while hovering over the relevant device. Labels and port markers have a fixed on-screen size and do not scale with canvas zoom.

For non-parametric registered GDS devices, the properties panel shows every port's name, layer, and width. Their geometry is never numerically stretched; parameter changes are represented by switching to another physical template in the same family.

The browser preview does not sample polygon vertices at a fixed stride. Preview preparation removes only strictly collinear redundant points, preserving every corner, port boundary, and curved vertex. Export and validation always use the original geometry and never depend on browser preview data.

### Inspect multilayer devices

After a multilayer device is registered, the assembly view creates layer controls from the polygons in the local catalog. Each control synchronizes the visibility of polygons, ports, and associated connections without embedding device data in source code.

### Draft recovery

Canvas changes are saved automatically. The right panel also provides manual save, restore, and clear actions. The draft is stored at:

```text
outputs/web_workspace/workspace_draft.json
```

The draft stores only template references, parameters, poses, connections, and layer state. Geometry previews are regenerated from the current device library during recovery. Writes use a temporary file and atomic replacement to avoid leaving incomplete JSON after interruption.

## Output and Validation

Web assembly results are written to:

```text
outputs/web_workspace/<layout_name>_<timestamp>_<id>/
```

| File | Contents |
|---|---|
| `*.gds` | Hierarchical GDS preserving device and routing cell references |
| `*.cir` | Structural reference netlist derived from the same connection definitions |
| `editor_spec.json` | Traceable assembly input |
| `manifest.json` | Devices, ports, nets, hierarchy, and template hashes |
| `validation.json` | Results from reopening and validating the written GDS |
| `*_annotated.png` | Annotated cluster image with devices, nets, and external ports |
| `*_device_atlas.png` | Multilayer geometry and port atlas for every device |

Validation reopens the GDS written to disk instead of trusting the browser preview. Checks include:

- template SHA-256 values and original layer sets;
- device and route hierarchy references;
- port centers, orientations, layers, and widths;
- reused or missing ports and invalid direct cross-layer connections;
- device overlaps, same-layer net shorts, and route crossings;
- consistency among external pins, labels, GDS, netlist, and manifest.

A candidate export is promoted to the final output only after read-back validation succeeds. Unconnected ports are written automatically as external ports.

## Code-First Assembly

```powershell
.\run_code_assembly_demo.ps1
```

```python
from photonic_cluster_generator import Assembly, DeviceLibrary

library = DeviceLibrary("device_registry/catalog.json")
layout = Assembly("MY_CLUSTER", library=library)

layout.add_registered("device_a", "<local_template_id>", at=(0, 0))
layout.add_registered("device_b", "<local_template_id>")
layout.snap("device_b", "o1", "device_a", "o2", net_id="N_1")
layout.external("IN", "device_a", "o1")
layout.external("OUT", "device_b", "o2")

result = layout.export("outputs/my_cluster")
```

`snap` selects an allowed rotation and translates the complete device so that the physical port centers coincide.

## Command-Line Interface

After installing the package in editable mode:

```powershell
photonic-cluster --help
```

The module can also be invoked directly:

```powershell
python -m photonic_cluster_generator.cli workspace `
  --registry "device_registry/catalog.json" `
  --output-root "outputs/web_workspace" `
  --host 127.0.0.1 `
  --port 8765
```

Scan standalone GDS files:

```powershell
python -m photonic_cluster_generator.cli scan-ports `
  "C:\path\to\device_gds_folder" `
  --output "outputs/port_candidates.json"
```

Generate and validate a cluster from a JSON specification:

```powershell
python -m photonic_cluster_generator.cli generate `
  "examples/lvs_mzi_cluster.json" `
  --output-dir "outputs/lvs_mzi_cluster"
```

## Tests

```powershell
.\run_tests.ps1
```

Tests generate publishable synthetic GDS inputs at runtime. Coverage includes multilayer scanning and registration, cross-layer device previews, the dynamic device library, browser scripts, draft persistence, port snapping, rigid-body transforms, parameter transactions, hierarchical export, netlist consistency, visualization output, and failure cases such as collisions, dangling ports, port reuse, and invalid cross-layer connections. Tests do not depend on any private PDK or registered device.

The verified environment currently emits one `ResourceWarning` from gdsfactory 8.32.2 because the dependency reads `generic_tech/layer_views.yaml` without closing the file. The warning does not affect geometry or test results.

## Project Layout

```text
PhotonicClusterGenerator/
├── device_registry/                 # Local versioned library; contents are not committed
├── examples/                        # JSON and Python examples
├── outputs/                         # Local generated results; not committed
├── schemas/                         # Cluster specification schemas
├── scripts/                         # Environment and launch helpers
├── src/photonic_cluster_generator/
│   ├── onboarding/                  # Geometry scanning, port confirmation, registration
│   ├── editor/                      # Unified web service and assembly interface
│   ├── assembly.py                  # Code-first assembly model
│   ├── generator.py                 # Hierarchical GDS generation
│   ├── netlist.py                   # Reference netlist generation
│   └── validator.py                 # Written-GDS read-back validation
├── tests/
├── environment.yml                  # Reproducible Conda environment
├── requirements.txt                 # Pinned direct dependencies
└── pyproject.toml                   # Python package configuration
```

## GitHub Publication

Repository: <https://github.com/zhouhh2024/photonic-layout-workbench>

`.gitignore` excludes caches, virtual environments, build directories, generated outputs, all GDS/OASIS files, `device_registry`, and local PDK directories. A public commit must never contain:

- PDK or device-template GDS files;
- registered device copies or `catalog.json`;
- port coordinates, template hashes, or registration history;
- specialized examples or mapping reports derived directly from non-public device data.

After cloning the public repository, the first launch creates an empty local `device_registry`. Users import their own devices through **Device Registration**, and those records remain local.

Before publishing, check:

```powershell
git status --short
git ls-files -- "*.gds" "*.oas" "*.oasis" "device_registry/**" ":(exclude)device_registry/.gitkeep"
git ls-files | Select-String -Pattern '(^|/)pdk/'
```

The last two commands should produce no output.

## License

Original source code and documentation actually committed to this repository are licensed under the [MIT License](LICENSE). Personal research, modification, and redistribution are permitted provided that the copyright and license notices are retained.

The license does not apply to uncommitted local data, including:

- PDK and device-template GDS/OASIS files;
- device copies, catalogs, port coordinates, hashes, and revision history in `device_registry`;
- layouts, examples, reports, or test results derived directly from non-public device data.

These private assets are not distributed with the repository and receive no redistribution rights through this project. Third-party dependencies remain subject to their own licenses.

First push:

```powershell
git init -b main
git add .
git commit -m "Initial release of Photonic Cluster Generator"
git remote add origin git@github.com:zhouhh2024/photonic-layout-workbench.git
git push -u origin main
```

Subsequent updates:

```powershell
git add .
git commit -m "Describe the change"
git push
```

## Current Limitations

- Port candidates provide geometric recall; port semantics still require manual confirmation.
- The web workbench does not yet provide multi-step undo and redo.
- Routing does not yet include global floorplanning, congestion optimization, or rip-up and reroute for arbitrary-scale layouts.
- The `.cir` file is a structural reference netlist for LVS preparation, not a replacement for a foundry sign-off DRC/LVS rule deck.
