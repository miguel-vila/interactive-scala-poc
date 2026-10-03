#!/usr/bin/env python3
"""Compile once per cell/revision and run its full, bounded Cartesian grid."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import os
import sys

from scala_diff import (ContractError, check_effects, dropped_hint, emit, execute_driver,
                        grid_rows, literal, read_json, resolve_cell, row_key, validate_budget, validate_preflight,
                        write_json)


def run_grid(preflight, cells, confirmations=(), jobs=None):
    cells = [resolve_cell(c) for c in cells]
    if len({c["cellId"] for c in cells}) != len(cells):
        raise ContractError("Repeated cellId")
    check_effects(cells, confirmations)
    validate_budget(cells)
    validate_preflight(preflight, cells)
    if jobs is None:
        jobs = min(4, os.cpu_count() or 1)
    if jobs < 1:
        raise ContractError("--jobs must be at least 1")
    runs = {}
    with ThreadPoolExecutor(max_workers=jobs) as executor:
        heads = {executor.submit(execute_driver, preflight, cell, "head"): cell
                 for cell in cells}
        bases = {}
        for future in as_completed(heads):
            cell = heads[future]
            cell_id = cell["cellId"]
            run = future.result()
            runs[(cell_id, "head")] = run
            if (preflight.get("base", {}).get("builds") and
                    not any(r["kind"] == "compileError" for r in run["results"].values())):
                bases[executor.submit(execute_driver, preflight, cell, "base")] = cell_id
        for future in as_completed(bases):
            runs[(bases[future], "base")] = future.result()
    output, dropped, effects = {}, [], []
    for cell in cells:
        recorded, sources = {}, {}
        for revision in ("head", "base"):
            info = preflight.get(revision)
            if not info or not info["builds"]:
                continue
            run = runs[(cell["cellId"], revision)]
            sources[revision] = run["driverSource"]
            if revision == "head" and any(r["kind"] == "compileError" for r in run["results"].values()):
                dropped.append({"cellId": cell["cellId"], "diagnostic": run["diagnostic"],
                                "hint": dropped_hint(run["diagnostic"], preflight)})
                break
            for key, result in run["results"].items():
                recorded.setdefault(key, {})[revision] = result
        else:
            for result in recorded.values():
                result["differs"] = "base" in result and result["base"] != result["head"]
            output[cell["cellId"]] = {"call": cell["snippet"], "setup": cell.get("setup", ""),
                                      "imports": cell.get("imports", []),
                                      "params": cell["params"], "effect": cell["effect"],
                                      "function": cell.get("function"), "results": recorded,
                                      "rows": [{"key": row_key(row), "values": row,
                                                "bindings": "\n".join(f"val {p['name']}: {p['type']} = {literal(v, p['type'])}" for p, v in zip(cell["params"], row))}
                                               for row in grid_rows(cell)],
                                      "driverSource": sources}
            if cell["effect"] != "pure":
                effects.append({"cellId": cell["cellId"], "function": cell["function"],
                                "effect": cell["effect"], "confirmed": True})
    provenance = dict(preflight)
    provenance["droppedCells"] = dropped
    provenance["effects"] = effects
    return {"cells": output, "provenance": provenance}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preflight", required=True)
    parser.add_argument("--cells", required=True)
    parser.add_argument("--confirm-effect", action="append", default=[])
    parser.add_argument("--dropped", help="JSON array of cells rejected by the constructability probe")
    parser.add_argument("--output", help="Write the full grid to this JSON file and print only counts")
    parser.add_argument("--jobs", type=int, help="Maximum concurrent Scala CLI runs (default: CPU count, up to 4)")
    args = parser.parse_args()
    try:
        specs = read_json(args.cells)
        if isinstance(specs, dict):
            specs = specs["cells"]
        output = run_grid(read_json(args.preflight), specs, args.confirm_effect, args.jobs)
        if args.dropped:
            output["provenance"]["droppedCells"] += read_json(args.dropped)
        if args.output:
            write_json(args.output, output)
            emit({"cells": len(output["cells"]),
                  "rows": sum(len(cell["rows"]) for cell in output["cells"].values()),
                  "differingRows": sum(bool(row["differs"]) for cell in output["cells"].values()
                                       for row in cell["results"].values()),
                  "droppedCells": len(output["provenance"]["droppedCells"])}, compact=True)
        else:
            emit(output)
        return 0 if output["cells"] else 1
    except (ContractError, OSError, KeyError, ValueError) as error:
        emit({"ok": False, "diagnostic": str(error)}, compact=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
