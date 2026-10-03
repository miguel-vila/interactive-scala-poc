#!/usr/bin/env python3
"""Compile and run one default call per revision, with named effect consent."""
import argparse
from pathlib import Path
import sys

from scala_diff import (ContractError, check_effects, dropped_hint, emit, execute_driver,
                        read_json, resolve_cell, validate_preflight, write_json)


def append_cell(path, cell):
    existing = read_json(path) if Path(path).exists() else []
    cells = existing["cells"] if isinstance(existing, dict) and "cells" in existing else existing
    if not isinstance(cells, list) or any(not isinstance(entry, dict) for entry in cells):
        raise ContractError("--append-to must contain a JSON array or an object with a cells array")
    cells = list(cells)
    for index, entry in enumerate(cells):
        if entry.get("cellId") == cell["cellId"]:
            cells[index] = cell
            break
    else:
        cells.append(cell)
    write_json(path, {**existing, "cells": cells} if isinstance(existing, dict) else cells)


def probe(preflight, cell, confirmations=()):
    cell = resolve_cell(cell)
    check_effects([cell], confirmations)
    validate_preflight(preflight, [cell])
    revisions = {}
    for name in ("head", "base"):
        if not preflight.get(name) or not preflight[name].get("builds"):
            continue
        output = execute_driver(preflight, cell, name, probe=True)
        result = next(iter(output["results"].values()))
        admissible = result["kind"] == "value"
        diagnostic = None if admissible else output.get("diagnostic") or result["render"]
        revisions[name] = {"admissible": admissible, "diagnostic": diagnostic,
                           "hint": dropped_hint(diagnostic, preflight)}
    head = revisions["head"]
    return {"cellId": cell["cellId"], "admissible": head["admissible"], "diagnostic": head["diagnostic"],
            "hint": head["hint"], "params": cell["params"], "cell": cell, "revisions": revisions}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preflight", required=True)
    parser.add_argument("--cell", required=True)
    parser.add_argument("--confirm-effect", action="append", default=[])
    parser.add_argument("--output", help="Write the resolved cell to this JSON file")
    parser.add_argument("--append-to", help="Append or replace an admissible cell in this cells JSON file")
    args = parser.parse_args()
    try:
        result = probe(read_json(args.preflight), read_json(args.cell), args.confirm_effect)
        if args.output:
            write_json(args.output, result["cell"])
        if args.append_to and result["admissible"]:
            append_cell(args.append_to, result["cell"])
        summary = {"cellId": result["cellId"], "admissible": result["admissible"]}
        if not result["admissible"]:
            summary["diagnostic"] = result["diagnostic"]
        emit(summary, compact=True)
        return 0 if result["admissible"] else 1
    except (ContractError, OSError, KeyError, ValueError) as error:
        emit({"admissible": False, "diagnostic": str(error)}, compact=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
