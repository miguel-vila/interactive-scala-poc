#!/usr/bin/env python3
"""Compile and run one default call per revision, with named effect consent."""
import argparse
import sys

from scala_diff import (ContractError, check_effects, dropped_hint, emit, execute_driver,
                        read_json, resolve_cell, validate_preflight)


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
    args = parser.parse_args()
    try:
        result = probe(read_json(args.preflight), read_json(args.cell), args.confirm_effect)
        emit(result)
        return 0 if result["admissible"] else 1
    except (ContractError, OSError, KeyError, ValueError) as error:
        emit({"admissible": False, "diagnostic": str(error)})
        return 1


if __name__ == "__main__":
    sys.exit(main())
