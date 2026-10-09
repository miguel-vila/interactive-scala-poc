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

from tests.support import HTML_SKILL, LIVE_SKILL, FIXTURES, load

builder = load(HTML_SKILL, "build-page")
FIXTURE_BIN = FIXTURES / "bin"
SCRIPTS = LIVE_SKILL / "scripts"


def page_grid(root, sha="head-sha"):
    values = list(range(5))
    return {"cells": {"example": {"call": "1 + 1", "imports": [], "setup": "", "params": [],
            "rows": [{"key": str(n), "values": []} for n in values],
            "results": {str(n): {"base": {"kind": "value", "render": "base"},
                                 "head": {"kind": "value", "render": "head"}, "differs": True} for n in values},
            "driverSource": {"head": "head", "base": "base"}, "effect": "pure"}},
            "provenance": {"module": "core", "projectDir": str(root), "scalaVersion": "3.5.0",
                           "head": {"sha": sha, "builds": True},
                           "base": {"sha": "base-sha", "builds": True}}}


class KernelFixture(unittest.TestCase):
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

    def start(self, *extra, with_preflight=True):
        env = dict(os.environ, PATH=str(FIXTURE_BIN) + os.pathsep + os.environ["PATH"])
        command = [sys.executable, str(SCRIPTS / "kernel.py"), "--page", str(self.page)]
        if with_preflight:
            command += ["--preflight", str(self.preflight)]
        command += ["--temp-dir", self.temp.name, *extra]
        self.process = subprocess.Popen(command,
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


class KernelHTTP(KernelFixture):
    def test_auth_headers_page_and_both_revisions(self):
        started = self.start()
        self.assertTrue(started["ok"])
        self.assertEqual(self.request("/api/status", token="wrong")[0], 401)
        self.assertEqual(self.request("/api/status", headers={"Host": "attacker.invalid"})[0], 400)
        self.assertEqual(self.request("/api/status", headers={"Origin": "http://attacker.invalid"})[0], 403)
        self.assertEqual(self.request("/api/runs", "POST", {"cellId": "absent", "source": "1"})[0], 400)
        self.assertEqual(self.request("/api/runs", "POST", {"cellId": "example", "source": "//> using dep x"})[0], 400)
        self.assertEqual(self.request("/api/runs", "POST", {"cellId": "example", "source": "x" * 65537})[0], 413)
        self.assertIn(b"page-provenance", self.request("/example.html")[1])
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

    def test_recorded_preflight_starts_working_tree_page_without_flag(self):
        report = json.loads(self.preflight.read_text())
        report["head"]["workingTreeHash"] = "snapshot-hash"
        self.preflight.write_text(json.dumps(report))
        grid = page_grid(Path(self.temp.name))
        grid["provenance"]["head"]["workingTreeHash"] = "snapshot-hash"
        narrative = {"title": "Example", "background": "<p>Context</p>",
                     "intuition": "<p>Idea</p>",
                     "code": '<pre>1 + 1</pre><div class="scala-cell" data-cell="example"></div>'}
        self.page.write_text(builder.build_page(grid, narrative, preflight=self.preflight))
        started = self.start(with_preflight=False)
        self.assertTrue(started["ok"], started)
        self.assertEqual(self.request("/api/status")[1]["revisions"]["head"]["sha"], "head-sha")

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
            self.assertIn(b"page-provenance", response.read())

class SplitContracts(KernelFixture):
    def test_fragment_is_served_once_without_changing_disk(self):
        import hashlib
        import shlex
        original = self.page.read_bytes()
        self.assertNotIn(b'.live-panel', original)
        started = self.start('--no-bloop', '--allow-effects', '--max-timeout-seconds', '15')
        self.assertTrue(started['ok'], started)
        served = self.request('/example.html')[1].decode()
        self.assertEqual(served.count('Live mode activates only'), 1)
        self.assertLess(served.index('Live mode activates only'), served.rindex('</body>'))
        self.assertLess(served.index('const data = JSON.parse(document.getElementById("cell-" + id).textContent)'), served.index('Live mode activates only'))
        self.assertEqual(self.page.read_bytes(), original)
        status = self.request('/api/status')[1]
        self.assertEqual(status['page']['sha256'], hashlib.sha256(original).hexdigest())
        command = shlex.split(status['restartCommand'])
        self.assertIn(str(SCRIPTS / 'kernel.py'), command)
        self.assertIn(str(self.page.resolve()), command)
        self.assertIn(str(self.preflight.resolve()), command)
        for flag in ('--no-bloop', '--allow-effects', '--max-timeout-seconds'):
            self.assertIn(flag, command)

    def test_missing_and_invalid_preflights_refuse_without_rebuild(self):
        module = load(LIVE_SKILL, 'kernel')
        _, info = module.load_page(self.page)
        report = json.loads(self.preflight.read_text())
        cases = [None, self.preflight.with_name('absent.json')]
        for mutation in ('module', 'tempDir', 'classpathFile', 'builds', 'workingTreeHash'):
            changed = json.loads(json.dumps(report))
            if mutation == 'module': changed['module'] = 'wrong'
            elif mutation == 'tempDir': changed['tempDir'] = '/missing/worktree'
            elif mutation == 'classpathFile': changed['base']['classpathFile'] = '/missing/classpath'
            elif mutation == 'builds': changed['head']['builds'] = False
            else: changed['head']['workingTreeHash'] = 'wrong'
            path = self.preflight.with_name(mutation + '.json')
            path.write_text(json.dumps(changed))
            cases.append(path)
        for path in cases:
            with self.subTest(path=path), self.assertRaises(module.ContractError) as error:
                module.resolve_preflight(info, path)
            for expected in ('head-sha', 'base-sha', 'core', 'preflight.py', '--preflight'):
                self.assertIn(expected, str(error.exception))
        info['head']['workingTreeHash'] = 'snapshot'
        with self.assertRaisesRegex(module.ContractError, 'original preflight'):
            module.resolve_preflight(info, None)

    def test_old_and_unknown_page_versions_are_refused(self):
        module = load(LIVE_SKILL, 'kernel')
        original = self.page.read_text()
        for page in (original.replace('page-provenance', 'live-provenance'),
                     original.replace('"pageVersion": 2', '"pageVersion": 99')):
            self.page.write_text(page)
            with self.assertRaisesRegex(module.ContractError, '(?i)rebuild.*current explain-scala-diff-html'):
                module.load_page(self.page)

    def test_html_builder_supplies_live_dom_contract(self):
        from html.parser import HTMLParser
        class DOM(HTMLParser):
            def __init__(self):
                super().__init__()
                self.tags = []
            def handle_starttag(self, tag, attrs):
                self.tags.append((tag, dict(attrs)))
        page = self.page.read_text()
        dom = DOM()
        dom.feed(page)
        self.assertIn(('div', {'class': 'scala-cell', 'data-cell': 'example'}), dom.tags)
        self.assertIn(('section', {'id': 'code'}), dom.tags)
        self.assertIn(('script', {'type': 'application/json', 'id': 'cell-example'}), dom.tags)
        data = json.loads(page.split('id="cell-example">', 1)[1].split('</script>', 1)[0])
        for key in ('call', 'imports', 'setup', 'params', 'rows', 'results'):
            self.assertIn(key, data)
        for variable in ('--line', '--ink', '--panel', '--del', '--muted'):
            self.assertIn(variable, page)
        for cls in ('scala-banner', 'scala-status'):
            self.assertIn(cls, page)
        self.assertEqual(page.count('id="page-provenance"'), 1)

    def test_fragment_fetches_only_relative_api_paths(self):
        fragment = (LIVE_SKILL / 'references/live.html').read_text()
        self.assertIn('if (location.protocol !== "http:" || !token) return;', fragment)
        self.assertEqual(fragment.count('fetch('), 1)
        self.assertIn('fetch("/api/" + route', fragment)
        self.assertNotIn('https:', fragment)
        self.assertNotIn('ws:', fragment)



if __name__ == "__main__":
    unittest.main()
