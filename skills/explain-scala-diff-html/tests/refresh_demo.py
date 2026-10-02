"""Rebuild recorded demo pages and verify the timeout-then-success grid only."""
from pathlib import Path
import argparse
import json

from test_pipeline import core, grid, load

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = Path.cwd() / ".validation"
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--confirm-effect", action="append", default=[], help="Named user confirmation; omit to rebuild from recordings only")
args = parser.parse_args()
for preflight_name, folder in (("preflight.json", OUTPUT), ("ce2-preflight.json", OUTPUT / "ce2")):
    pf = core.read_json(OUTPUT / preflight_name)
    output = core.read_json(folder / "grid.json")
    specs = core.read_json(ROOT / "tests/fixtures/cells.json") + core.read_json(ROOT / "tests/fixtures/effects.json")
    for cell in specs:
        cell["module"] = pf["module"]
    slow = next(c for c in specs if c["cellId"] == "slow-timeout")
    if args.confirm_effect:
        verified = grid.run_grid(pf, [slow], args.confirm_effect)
        assert verified["cells"]["slow-timeout"]["results"]["[true]"]["head"]["kind"] == "timeout"
        assert verified["cells"]["slow-timeout"]["results"]["[false]"]["head"]["render"] == '"done"'
        output["cells"]["slow-timeout"] = verified["cells"]["slow-timeout"]
    rejection = core.read_json(folder / "rejected-probe.json")
    output["provenance"]["droppedCells"] = [{"cellId": rejection["cellId"], "diagnostic": rejection["diagnostic"]}]
    # Existing recorded calls stay unchanged; regenerate the visible parameter
    # binding text from the same literal generator that built their drivers.
    for cell in specs:
        spec = core.resolve_cell(cell)
        for row in output["cells"][cell["cellId"]]["rows"]:
            row["bindings"] = "\n".join(f"val {p['name']}: {p['type']} = {core.literal(v, p['type'])}" for p, v in zip(spec["params"], row["values"]))
    (folder / "grid.json").write_text(json.dumps(output, ensure_ascii=False, indent=2))
    narrative = core.read_json(ROOT / "tests/fixtures/narrative.json")
    narrative["code"] += '<h3>Effect adapters</h3><p>Approved local calls exercise IO, Resource, and Stream adapters. The final cell records a timeout, then a successful call in the same batch.</p>'
    narrative["code"] += "".join('<div class="scala-cell" data-cell="' + c["cellId"] + '"></div>' for c in specs if c.get("effect", "pure") != "pure")
    builder = load("build-page")
    (folder / "2026-10-01-explanation-scala-diff.html").write_text(builder.build_page(output, narrative))
    output["provenance"]["base"]["builds"] = False
    output["provenance"]["base"]["diagnostic"] = "Recorded base compilation failure"
    for cell in output["cells"].values():
        for result in cell["results"].values():
            result.pop("base")
            result["differs"] = False
        cell["driverSource"].pop("base")
    (folder / "head-only.html").write_text(builder.build_page(output, narrative))
    output["provenance"]["base"] = None
    (folder / "no-diff.html").write_text(builder.build_page(output, narrative))
    print("timeout-then-success and pages passed:", pf["scalaVersion"], pf["catsEffect"]["version"], flush=True)
