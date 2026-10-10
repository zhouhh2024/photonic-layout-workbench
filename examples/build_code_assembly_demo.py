"""Build a small verified parametric cluster without private device data."""

from __future__ import annotations

from pathlib import Path

from photonic_cluster_generator import Assembly


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "outputs" / "code_assembly_demo"


def main() -> None:
    layout = Assembly("CODE_ASSEMBLY_DEMO")
    layout.add_parametric(
        "input_bus", "straight", material="si", length_um=25.0, width_um=0.5
    )
    layout.add_parametric(
        "bend", "bend_euler", material="si", radius_um=10.0, width_um=0.5
    )
    layout.snap("bend", "o1", "input_bus", "o2", net_id="N_INPUT")
    layout.add_parametric("output_bus", "straight", material="si", length_um=20.0)
    layout.set_parameters("output_bus", length_um=35.0, width_um=0.5)
    layout.snap("output_bus", "o1", "bend", "o2", net_id="N_OUTPUT")

    layout.external("IN", "input_bus", "o1")
    layout.external("OUT", "output_bus", "o2")

    spec_path = layout.save_spec(OUTPUT / "generated_spec.json")
    result = layout.export(OUTPUT)
    print(f"Specification: {spec_path}")
    print(f"GDS:           {result['gds']}")
    print(f"Preview:       {result['preview']}")
    print(f"Validation:    {result['validation_report']}")
    print(f"Passed:        {result['validation']['passed']}")


if __name__ == "__main__":
    main()
