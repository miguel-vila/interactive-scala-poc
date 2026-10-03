"""Real Scala acceptance checks. Named fixture effect approval must precede --effects."""
import argparse
import json
from pathlib import Path
import signal
import subprocess
import sys
import time
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from test_pipeline import core, grid, probe, load

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = Path.cwd() / ".validation"
APPROVED = ["demo.Demo.ioDecode", "demo.Demo.resourceDecode", "demo.Demo.streamDecode", "demo.Demo.slow"]


def save(name, value):
    (OUTPUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2))


def validate_live(page, preflight, effects_allowed=False):
    command = [sys.executable, str(ROOT / "scripts/kernel.py"), "--page", str(page),
               "--preflight", str(preflight), "--no-bloop", "--temp-dir", str(OUTPUT)]
    if effects_allowed:
        command.append("--allow-effects")
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        started = json.loads(process.stdout.readline())
        assert started["ok"], started
        parsed = urlsplit(started["url"])
        origin = f"{parsed.scheme}://{parsed.netloc}"
        token = parsed.fragment[2:]

        def request(path, method="GET", body=None):
            data = json.dumps(body).encode() if body is not None else None
            headers = {"Authorization": "Bearer " + token}
            if data is not None:
                headers["Content-Type"] = "application/json"
            with urlopen(Request(origin + path, data=data, headers=headers, method=method), timeout=35) as response:
                return json.load(response)

        def run(source, timeout=5):
            run_id = request("/api/runs", "POST", {"cellId": "scratch", "source": source,
                                                   "timeoutSeconds": timeout})["runId"]
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                result = request("/api/runs/" + run_id + "?wait=10")
                if result["state"] in ("done", "cancelled"):
                    return result
            raise AssertionError("Live run did not finish")

        if not effects_allowed:
            pure = run('demo.Demo.decode("!!!", 1)')
            assert pure["revisions"]["base"]["kind"] == "throwable", pure
            assert pure["revisions"]["head"]["render"] == 'Left("InvalidBase64")', pure
            bad = run('val n: Int = "bad"')
            assert bad["revisions"]["head"]["kind"] == "compileError", bad
            assert bad["revisions"]["head"]["lines"] == [1], bad
            timed = run('Thread.sleep(3000); 1', .2)
            assert timed["revisions"]["head"]["kind"] == "timeout", timed
            refused = run('cats.effect.IO.pure(1)')
            assert refused["revisions"]["head"]["kind"] == "refused", refused
            run_id = request("/api/runs", "POST", {"cellId": "scratch", "source": "Thread.sleep(30000); 1"})["runId"]
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                current = request("/api/runs/" + run_id + "?wait=1")
                if current["revisions"]["head"]["state"] == "running":
                    break
            request("/api/runs/" + run_id + "/cancel", "POST", {})
            cancelled = request("/api/runs/" + run_id + "?wait=10")
            assert cancelled["state"] == "cancelled", cancelled
        else:
            allowed = run('cats.effect.IO.pure(1)')
            assert allowed["revisions"]["head"]["kind"] == "value", allowed
            assert allowed["revisions"]["head"]["render"] == "1", allowed
        print("live kernel passed:", "effects allowed" if effects_allowed else "pure, compile, timeout, refused, cancel", flush=True)
    finally:
        process.send_signal(signal.SIGTERM) if process.poll() is None else None
        try:
            process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()


def main():
    global OUTPUT
    parser = argparse.ArgumentParser()
    parser.add_argument("--effects", action="store_true")
    parser.add_argument("--live", action="store_true", help="Exercise the kernel through its local HTTP API")
    parser.add_argument("--preflight", default=str(OUTPUT / "preflight.json"))
    parser.add_argument("--output-dir", default=str(OUTPUT))
    args = parser.parse_args()
    OUTPUT = Path(args.output_dir).resolve()
    OUTPUT.mkdir(parents=True, exist_ok=True)
    if args.live:
        validate_live(OUTPUT / "2026-10-01-explanation-scala-diff.html", args.preflight)
        validate_live(OUTPUT / "2026-10-01-explanation-scala-diff.html", args.preflight, True)
        return
    pf = core.read_json(args.preflight)
    cells = core.read_json(ROOT / "tests/fixtures/cells.json")
    for cell in cells:
        cell["module"] = pf["module"]
    for cell in cells:
        result = probe.probe(pf, cell)
        assert result["admissible"], result["diagnostic"]
        print("probe passed:", cell["cellId"], flush=True)
    bad = dict(cells[0], cellId="bad-input", params=[
        {"name": "input", "type": "String", "values": [{"scala": "new Object()", "label": "object"}]},
        {"name": "retries", "type": "Int", "values": [1]}])
    rejection = probe.probe(pf, bad)
    assert not rejection["admissible"]
    assert "String" in rejection["diagnostic"] and "Object" in rejection["diagnostic"]
    save("rejected-probe.json", rejection)
    print("compiler rejection preserved verbatim", flush=True)
    if args.effects:
        effect_cells = core.read_json(ROOT / "tests/fixtures/effects.json")
        for cell in effect_cells:
            cell["module"] = pf["module"]
        cells += effect_cells
    output = grid.run_grid(pf, cells, APPROVED if args.effects else [])
    output["provenance"]["droppedCells"].append({"cellId": rejection["cellId"], "diagnostic": rejection["diagnostic"]})
    save("grid.json", output)
    assert len(output["cells"]) == len(cells), output["provenance"]["droppedCells"]
    results = output["cells"]["decode-retry"]["results"]
    assert results["1|!!!"]["base"]["kind"] == "throwable"
    assert results["1|!!!"]["head"] == {"kind": "value", "render": 'Left("InvalidBase64")'}
    assert results["1|!!!"]["differs"]
    assert not results["1|dGVzdA=="]["differs"]
    if args.effects:
        assert output["cells"]["slow-timeout"]["results"]["[true]"]["head"]["kind"] == "timeout"
        assert output["cells"]["slow-timeout"]["results"]["[false]"]["head"] == {"kind": "value", "render": '"done"'}
        assert output["cells"]["stream-decode"]["results"]["hello"]["head"]["render"] == 'List("hello", "hello", "hello")'
        assert output["cells"]["resource-decode"]["results"]["hello"]["head"]["render"] == '"resource:hello"'
        assert len(output["provenance"]["effects"]) == 4
    print("grid passed:", sum(len(c["rows"]) for c in output["cells"].values()), "rows across", len(cells), "cells", flush=True)
    page_builder = load("build-page")
    narrative = core.read_json(ROOT / "tests/fixtures/narrative.json")
    if args.effects:
        narrative["code"] += "<h3>Effect adapters</h3><p>The same harness supports IO, a resource lifetime, a finite prefix of an infinite stream, and a bounded wait.</p>"
        narrative["code"] += "".join('<div class="scala-cell" data-cell="' + cell["cellId"] + '"></div>' for cell in cells if cell.get("effect", "pure") != "pure")
    page = page_builder.build_page(output, narrative)
    (OUTPUT / "2026-10-01-explanation-scala-diff.html").write_text(page)
    assert page.count("correct: true") == 5
    assert "shuffled(q.options)" in page
    for question in page_builder.verified_quiz(output):
        evidence = question["evidence"]
        actual = output["cells"][evidence["cellId"]]["results"][evidence["rowKey"]]["head"]
        assert next(opt["text"] for opt in question["options"] if opt.get("correct")) == page_builder.answer(actual)
    head_only = json.loads(json.dumps(output))
    head_only["provenance"]["base"]["builds"] = False
    head_only["provenance"]["base"]["diagnostic"] = "Recorded base compilation failure"
    for cell in head_only["cells"].values():
        for result in cell["results"].values():
            result.pop("base")
            result["differs"] = False
        cell["driverSource"].pop("base")
    page = page_builder.build_page(head_only, narrative)
    assert "The base revision did not build" in page
    (OUTPUT / "head-only.html").write_text(page)
    head_only["provenance"]["base"] = None
    (OUTPUT / "no-diff.html").write_text(page_builder.build_page(head_only, narrative))
    print("page, quiz evidence, head-only and no-diff checks passed", flush=True)


if __name__ == "__main__":
    main()
