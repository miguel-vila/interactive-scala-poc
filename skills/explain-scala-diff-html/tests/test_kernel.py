import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from test_pipeline import load, SCRIPTS

builder = load("build-page")
FIXTURE_BIN = Path(__file__).parent / "fixtures/bin"


def page_grid(root, sha="head-sha"):
    values = list(range(5))
    return {"cells": {"example": {"call": "1 + 1", "params": [],
            "rows": [{"key": str(n), "values": []} for n in values],
            "results": {str(n): {"base": {"kind": "value", "render": "base"},
                                 "head": {"kind": "value", "render": "head"}, "differs": True} for n in values},
            "driverSource": {"head": "head", "base": "base"}, "effect": "pure"}},
            "provenance": {"module": "core", "projectDir": str(root), "scalaVersion": "3.5.0",
                           "head": {"sha": sha, "builds": True},
                           "base": {"sha": "base-sha", "builds": True}}}


class KernelHTTP(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        (root / "cp-head.txt").write_text("/fake/head.jar\n")
        (root / "cp-base.txt").write_text("/fake/base.jar\n")
        self.preflight = root / "preflight.json"
        self.preflight.write_text(json.dumps({"ok": True, "module": "core", "tempDir": str(root),
            "head": {"sha": "head-sha", "builds": True, "classpathFile": str(root / "cp-head.txt"),
                     "scalaVersion": "3.5.0", "catsEffect": {"present": False}},
            "base": {"sha": "base-sha", "builds": True, "classpathFile": str(root / "cp-base.txt"),
                     "scalaVersion": "3.5.0", "catsEffect": {"present": False}},
            "toolchain": {"command": ["scala-cli"]}}))
        self.page = root / "example.html"
        narrative = {"title": "Example", "background": "<p>Context</p>",
                     "intuition": "<p>Idea</p>",
                     "code": '<pre>1 + 1</pre><div class="scala-cell" data-cell="example"></div>'}
        self.page.write_text(builder.build_page(page_grid(root), narrative))
        self.process = None
        self.detached_pid = None

    def tearDown(self):
        if self.detached_pid:
            try:
                os.kill(self.detached_pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        if self.process:
            if self.process.poll() is None:
                self.process.send_signal(signal.SIGTERM)
            try:
                self.process.communicate(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.communicate()

    def start(self, *extra):
        env = dict(os.environ, PATH=str(FIXTURE_BIN) + os.pathsep + os.environ["PATH"])
        self.process = subprocess.Popen([sys.executable, str(SCRIPTS / "kernel.py"),
                                         "--page", str(self.page), "--preflight", str(self.preflight),
                                         "--temp-dir", self.temp.name, *extra],
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
        line = self.process.stdout.readline()
        response = json.loads(line)
        if response["ok"]:
            parsed = urlsplit(response["url"])
            self.origin = f"{parsed.scheme}://{parsed.netloc}"
            self.token = parsed.fragment[2:]
        return response

    def request(self, path, method="GET", body=None, token=None, headers=None):
        headers = {"Authorization": "Bearer " + (self.token if token is None else token),
                   **(headers or {})}
        data = json.dumps(body).encode() if body is not None else None
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = Request(self.origin + path, data=data, method=method, headers=headers)
        try:
            with urlopen(request, timeout=5) as response:
                raw = response.read()
                return response.status, json.loads(raw) if path.startswith("/api/") else raw
        except HTTPError as error:
            with error:
                return error.code, json.loads(error.read())

    def wait_run(self, run_id, state="done", seconds=4):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            _, result = self.request("/api/runs/" + run_id + "?wait=.1")
            if result["state"] == state:
                return result
        self.fail("run did not reach " + state)

    def test_auth_headers_page_and_both_revisions(self):
        started = self.start()
        self.assertTrue(started["ok"])
        self.assertEqual(self.request("/api/status", token="wrong")[0], 401)
        self.assertEqual(self.request("/api/status", headers={"Host": "attacker.invalid"})[0], 400)
        self.assertEqual(self.request("/api/status", headers={"Origin": "http://attacker.invalid"})[0], 403)
        self.assertEqual(self.request("/api/runs", "POST", {"cellId": "absent", "source": "1"})[0], 400)
        self.assertEqual(self.request("/api/runs", "POST", {"cellId": "example", "source": "//> using dep x"})[0], 400)
        self.assertEqual(self.request("/api/runs", "POST", {"cellId": "example", "source": "x" * 65537})[0], 413)
        self.assertIn(b"live-provenance", self.request("/example.html")[1])
        self.assertEqual(self.request("/anything.html")[0], 404)
        status = self.request("/api/status")[1]
        self.assertFalse(status["effectsAllowed"])
        accepted = self.request("/api/runs", "POST", {"cellId": "example", "source": "PRINT_HELLO"})
        self.assertEqual(accepted[0], 202)
        result = self.wait_run(accepted[1]["runId"])
        self.assertEqual(result["revisions"]["base"]["render"], "base")
        self.assertEqual(result["revisions"]["head"]["render"], "head")
        self.assertIn("hello from fake JVM", result["revisions"]["head"]["output"])

    def test_compile_lines_cancel_and_idle_exit(self):
        self.start("--idle-minutes", ".02")
        run_id = self.request("/api/runs", "POST", {"cellId": "example", "source": "val n = 1\nBAD_COMPILE"})[1]["runId"]
        result = self.wait_run(run_id)
        self.assertEqual(result["revisions"]["head"]["kind"], "compileError")
        self.assertEqual(result["revisions"]["head"]["lines"], [2])
        self.assertIn("Live.scala:", result["revisions"]["head"]["diagnostic"])
        run_id = self.request("/api/runs", "POST", {"cellId": "example", "source": "SLOW_RUN"})[1]["runId"]
        self.wait_run(run_id, "running")
        started = time.monotonic()
        self.assertEqual(self.request("/api/runs/" + run_id + "/cancel", "POST", {})[0], 200)
        cancelled = self.wait_run(run_id, "cancelled")
        self.assertLess(time.monotonic() - started, 1)
        self.assertEqual(cancelled["revisions"]["head"]["kind"], "cancelled")
        run_id = self.request("/api/runs", "POST", {"cellId": "example", "source": "SLOW_RUN",
                                                        "timeoutSeconds": .2})[1]["runId"]
        timed = self.wait_run(run_id)
        self.assertEqual(timed["revisions"]["head"]["kind"], "timeout")
        self.process.wait(timeout=3)

    def test_queue_limit_and_queued_cancel(self):
        self.start()
        first = self.request("/api/runs", "POST", {"cellId": "example", "source": "SLOW_RUN"})[1]["runId"]
        self.wait_run(first, "running")
        queued = [self.request("/api/runs", "POST", {"cellId": "example", "source": "SLOW_RUN"})[1]["runId"]
                  for _ in range(4)]
        self.assertEqual(self.request("/api/runs", "POST", {"cellId": "example", "source": "1"})[0], 429)
        self.assertEqual(self.request("/api/runs/" + queued[0] + "/cancel", "POST", {})[0], 200)
        self.assertEqual(self.wait_run(queued[0], "cancelled")["revisions"]["head"]["kind"], "cancelled")
        for run_id in [first, *queued[1:]]:
            self.request("/api/runs/" + run_id + "/cancel", "POST", {})

    def test_sha_mismatch_refuses_start(self):
        report = json.loads(self.preflight.read_text())
        report["head"]["sha"] = "other-sha"
        self.preflight.write_text(json.dumps(report))
        failure = self.start()
        self.assertFalse(failure["ok"])
        self.assertIn("Page shas", failure["diagnostic"])

    def test_failed_base_uses_head_only(self):
        report = json.loads(self.preflight.read_text())
        report["base"].update(builds=False, diagnostic="base did not compile")
        self.preflight.write_text(json.dumps(report))
        started = self.start()
        self.assertTrue(started["ok"])
        self.assertEqual(started["warning"], "base did not compile")
        self.assertIsNone(self.request("/api/status")[1]["revisions"]["base"])
        run_id = self.request("/api/runs", "POST", {"cellId": "example", "source": "1"})[1]["runId"]
        result = self.wait_run(run_id)
        self.assertEqual(list(result["revisions"]), ["head"])

    def test_skill_launcher_keeps_kernel_running_after_it_exits(self):
        env = dict(os.environ, PATH=str(FIXTURE_BIN) + os.pathsep + os.environ["PATH"])
        launched = subprocess.run([sys.executable, str(SCRIPTS / "start-kernel.py"),
                                   "--page", str(self.page), "--preflight", str(self.preflight),
                                   "--temp-dir", self.temp.name],
                                  capture_output=True, text=True, env=env, timeout=5)
        self.assertEqual(launched.returncode, 0, launched.stdout + launched.stderr)
        started = json.loads(launched.stdout)
        self.detached_pid = started["pid"]
        self.assertTrue(Path(started["log"]).exists())
        self.assertTrue(Path(started["launcherLog"]).exists())
        page_url = started["url"].split("#", 1)[0]
        with urlopen(page_url, timeout=3) as response:
            self.assertIn(b"live-provenance", response.read())


if __name__ == "__main__":
    unittest.main()
