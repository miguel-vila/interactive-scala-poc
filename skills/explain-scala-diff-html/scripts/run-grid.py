#!/usr/bin/env python3
"""Compile once per cell/revision and run its full, bounded Cartesian grid."""
import argparse
import sys

from scala_diff import (ContractError, check_effects, dropped_hint, emit, execute_driver,
                        grid_rows, literal, read_json, resolve_cell, row_key, validate_budget, validate_preflight)


def run_grid(preflight, cells, confirmations=()):
    cells = [resolve_cell(c) for c in cells]
    if len({c["cellId"] for c in cells}) != len(cells):
        raise ContractError("Repeated cellId")
    check_effects(cells, confirmations)
    validate_budget(cells)
    validate_preflight(preflight, cells)
    output, dropped, effects = {}, [], []
    for cell in cells:
        recorded, sources = {}, {}
        for revision in ("head", "base"):
            info = preflight.get(revision)
            if not info or not info["builds"]:
                continue
            run = execute_driver(preflight, cell, revision)
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
    args = parser.parse_args()
    try:
        specs = read_json(args.cells)
        if isinstance(specs, dict):
            specs = specs["cells"]
        output = run_grid(read_json(args.preflight), specs, args.confirm_effect)
        if args.dropped:
            output["provenance"]["droppedCells"] += read_json(args.dropped)
        emit(output)
        return 0 if output["cells"] else 1
    except (ContractError, OSError, KeyError, ValueError) as error:
        emit({"ok": False, "diagnostic": str(error)})
        return 1


if __name__ == "__main__":
    sys.exit(main())
