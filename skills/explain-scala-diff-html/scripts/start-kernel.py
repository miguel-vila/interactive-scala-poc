#!/usr/bin/env python3
"""Start the local live kernel in the background and print its connection JSON."""
import argparse
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--page", required=True)
    parser.add_argument("--preflight")
    parser.add_argument("--allow-effects", action="store_true")
    parser.add_argument("--idle-minutes", type=float, default=30)
    parser.add_argument("--max-timeout-seconds", type=float, default=60)
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--no-bloop", action="store_true")
    parser.add_argument("--temp-dir")
    parser.add_argument("--startup-timeout-seconds", type=float, default=900)
    args = parser.parse_args()
    if not math.isfinite(args.startup_timeout_seconds) or args.startup_timeout_seconds <= 0:
        print(json.dumps({"ok": False, "diagnostic": "Startup timeout must be positive"}))
        return 1

    command = [sys.executable, str(Path(__file__).with_name("kernel.py")),
               "--page", args.page, "--idle-minutes", str(args.idle_minutes),
               "--max-timeout-seconds", str(args.max_timeout_seconds),
               "--port", str(args.port)]
    for flag, value in (("--preflight", args.preflight), ("--temp-dir", args.temp_dir)):
        if value:
            command += [flag, value]
    if args.allow_effects:
        command.append("--allow-effects")
    if args.no_bloop:
        command.append("--no-bloop")

    launch_dir = Path(tempfile.mkdtemp(prefix="scala-diff-launch-", dir=args.temp_dir)).resolve()
    output_path = launch_dir / "startup.jsonl"
    error_path = launch_dir / "launcher.log"
    with output_path.open("wb") as output, error_path.open("wb") as errors:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                                   stdout=output, stderr=errors,
                                   start_new_session=True)
    deadline = time.monotonic() + args.startup_timeout_seconds
    while True:
        contents = output_path.read_text(errors="replace")
        if "\n" in contents:
            try:
                response = json.loads(contents.split("\n", 1)[0])
            except ValueError as error:
                response = {"ok": False, "diagnostic": f"Invalid kernel startup response: {error}"}
            response["launcherLog"] = str(error_path)
            print(json.dumps(response), flush=True)
            return 0 if response.get("ok") else 1
        if process.poll() is not None:
            diagnostic = error_path.read_text(errors="replace")[-4000:] or "Kernel exited before reporting startup"
            print(json.dumps({"ok": False, "diagnostic": diagnostic,
                              "launcherLog": str(error_path)}), flush=True)
            return 1
        if time.monotonic() >= deadline:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            print(json.dumps({"ok": False, "diagnostic": "Kernel startup timed out",
                              "launcherLog": str(error_path)}), flush=True)
            return 1
        time.sleep(.05)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except OSError as error:
        print(json.dumps({"ok": False, "diagnostic": str(error)}), flush=True)
        sys.exit(1)
