#!/usr/bin/env python3
"""Link this checkout's skill into an agent's local skills directory, offline."""
import argparse
import json
from pathlib import Path
import sys


SKILL_NAME = "explain-scala-diff-html"
SOURCE = Path(__file__).resolve().parent / "skills" / SKILL_NAME


def install(destination):
    for name in ("SKILL.md", "references/html-scaffold.html", "references/console.html"):
        if not (SOURCE / name).is_file():
            raise ValueError(f"Incomplete skill checkout; missing {SOURCE / name}")
    destination = Path(destination).expanduser().absolute()
    target = destination / SKILL_NAME
    if target.is_symlink() and target.resolve() == SOURCE.resolve():
        return {"ok": True, "path": str(target), "source": str(SOURCE), "alreadyInstalled": True}
    if target.exists() or target.is_symlink():
        raise ValueError(f"Refusing to replace an existing installation: {target}. "
                         "Move it aside or choose --dest <skills-directory>.")
    destination.mkdir(parents=True, exist_ok=True)
    target.symlink_to(SOURCE, target_is_directory=True)
    return {"ok": True, "path": str(target), "source": str(SOURCE), "alreadyInstalled": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent", choices=("codex", "claude-code"), default="codex")
    parser.add_argument("--dest", help="Override the agent's skills directory")
    args = parser.parse_args()
    destination = args.dest or Path.home() / (".agents" if args.agent == "codex" else ".claude") / "skills"
    try:
        print(json.dumps(install(destination), indent=2))
        return 0
    except (OSError, ValueError) as error:
        print(json.dumps({"ok": False, "diagnostic": str(error)}, indent=2))
        return 1


if __name__ == "__main__":
    sys.exit(main())
