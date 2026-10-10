from __future__ import annotations

import argparse
import json
from pathlib import Path

from .editor.server import run_workspace
from .generator import generate_cluster
from .onboarding.registry import load_registry_catalog
from .onboarding.scanner import scan_gds_folder
from .validator import load_manifest, validate_written_gds


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="photonic-cluster",
        description="Generate and verify hierarchical photonic GDS clusters from JSON specifications.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    generate = commands.add_parser("generate", help="Generate GDS, netlist, manifest, and preview")
    generate.add_argument("spec", type=Path)
    generate.add_argument("--output-dir", type=Path, required=True)
    validate = commands.add_parser("validate", help="Re-read a GDS and validate it against a manifest")
    validate.add_argument("gds", type=Path)
    validate.add_argument("--manifest", type=Path, required=True)
    scan = commands.add_parser(
        "scan-ports",
        help="Generate geometry-only port candidates for every GDS layer",
    )
    scan.add_argument("source_folder", type=Path)
    scan.add_argument("--output", type=Path, required=True)
    workspace = commands.add_parser(
        "workspace",
        help="Open the unified device-registration and layout-assembly Web workspace",
    )
    workspace.add_argument("--output-root", type=Path, required=True)
    workspace.add_argument("--registry", type=Path, required=True)
    workspace.add_argument("--host", default="127.0.0.1")
    workspace.add_argument("--port", type=int, default=0)
    workspace.add_argument("--no-browser", action="store_true")
    workspace.add_argument("--start-page", choices=["assembly", "register"], default="assembly")
    listing = commands.add_parser("list-registry", help="List registered black-box devices")
    listing.add_argument("catalog", type=Path)
    return parser


def main() -> int:
    arguments = _parser().parse_args()
    if arguments.command == "generate":
        result = generate_cluster(arguments.spec, arguments.output_dir)
    elif arguments.command == "validate":
        result = validate_written_gds(arguments.gds, load_manifest(arguments.manifest))
    elif arguments.command == "scan-ports":
        scans = scan_gds_folder(arguments.source_folder)
        result = {
            "source_folder": str(arguments.source_folder.resolve()),
            "device_count": len(scans),
            "devices": [scan.to_dict(include_preview=False) for scan in scans],
        }
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(
            json.dumps(result, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        result = {"output": str(arguments.output.resolve()), "device_count": len(scans)}
    elif arguments.command == "workspace":
        run_workspace(
            arguments.output_root,
            user_catalog=arguments.registry,
            host=arguments.host,
            port=arguments.port,
            open_browser=not arguments.no_browser,
            start_page=arguments.start_page,
        )
        return 0
    else:
        catalog = load_registry_catalog(arguments.catalog)
        result = {
            "catalog": str(arguments.catalog.resolve()),
            "registry_revision": catalog.get("registry_revision"),
            "templates": [
                {
                    "template_id": template_id,
                    "family": record.get("family"),
                    "ports": list(dict(record.get("ports", {}))),
                    "source_layers": record.get("source_layers", []),
                }
                for template_id, record in sorted(dict(catalog["templates"]).items())
            ],
        }
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
