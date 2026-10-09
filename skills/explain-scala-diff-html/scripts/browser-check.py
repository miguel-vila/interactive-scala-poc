#!/usr/bin/env python3
"""Run the browser check with Playwright installed in a reusable user cache."""

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


SKILL_DIR = Path(__file__).resolve().parent.parent


def cache_dir():
    root = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache").expanduser().resolve()
    return root / "explain-scala-diff-html" / "playwright"


def system_chrome():
    override = os.environ.get("CHROME_PATH")
    if override:
        candidate = Path(override).expanduser()
        if not candidate.is_file() or not os.access(candidate, os.X_OK):
            raise RuntimeError(f"CHROME_PATH is not an executable file: {candidate}")
        return str(candidate.resolve())

    candidates = [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        str(Path.home() / "Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
    ]
    candidates.extend(filter(None, (shutil.which(name) for name in (
        "google-chrome", "google-chrome-stable", "chromium", "chromium-browser",
    ))))
    return next((path for path in candidates if Path(path).is_file() and os.access(path, os.X_OK)), None)


def run(command, *, env=None):
    result = subprocess.run(command, env=env, text=True, capture_output=True, check=False)
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"{' '.join(map(str, command))} failed: {detail}")
    return result.stdout


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--page", required=True, type=Path, help="completed HTML page")
    args = parser.parse_args()
    page = args.page.expanduser().resolve()
    if not page.is_file():
        raise RuntimeError(f"HTML page is missing: {page}")

    chrome = system_chrome()
    cache = cache_dir()
    module = cache / "node_modules" / "playwright"
    browsers = cache / "browsers"
    if not (module / "index.js").is_file():
        cache.mkdir(parents=True, exist_ok=True)
        run(["npm", "install", "--prefix", str(cache), "--no-save", "--package-lock=false",
             "--ignore-scripts", "playwright"])

    environment = {**os.environ, "PLAYWRIGHT_BROWSERS_PATH": str(browsers)}
    if chrome is None:
        executable = run([
            "node", "-e", "console.log(require(process.argv[1]).chromium.executablePath())",
            str(module),
        ], env=environment).strip()
        if not Path(executable).is_file():
            run([str(cache / "node_modules" / ".bin" / "playwright"), "install", "chromium"],
                env=environment)

    output = run(["node", str(SKILL_DIR / "scripts" / "browser-check.cjs"), str(module),
                  chrome or "", "--page", str(page)], env=environment)
    result = json.loads(output.strip())
    stem = page.with_suffix("")
    result["screenshots"] = {kind: str(stem.parent / f"{stem.name}-{kind}.png")
                             for kind in ("desktop", "mobile", "console")}
    print(json.dumps(result))


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, ValueError) as error:
        print(json.dumps({"ok": False, "error": str(error)}))
        sys.exit(1)
