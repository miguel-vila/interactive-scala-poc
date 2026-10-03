#!/usr/bin/env python3
"""Serve a generated page and run edited Scala against its recorded revisions."""
import argparse
from collections import deque
import copy
from datetime import datetime, timezone
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hmac
import json
import math
import os
from pathlib import Path
import re
import shutil
import secrets
import signal
import sys
import tempfile
import threading
import time
from urllib.parse import quote, urlsplit, parse_qs

from scala_diff import (ContractError, compile_driver, compiler_lines, fs2_core,
                        live_driver_source, read_json, run_compiled)


PROVENANCE = re.compile(r'<script type="application/json" id="live-provenance">(.*?)</script>', re.S)
CELL_ID = re.compile(r'[a-z][a-z0-9-]*')
RUN_PATH = re.compile(r'/api/runs/([a-f0-9]{32})(/cancel)?')


def load_page(path):
    content = Path(path).read_text()
    match = PROVENANCE.search(content)
    if not match:
        raise ContractError("Page has no live-provenance block; rebuild it with the current builder")
    return content, json.loads(match.group(1))


def resolve_preflight(page_info, provided, parent):
    expected = page_info["head"]
    base = page_info.get("base")
    if provided:
        report = read_json(provided)
        actual = report.get("head") or {}
        actual_base = report.get("base") or {}
        expected_pair = (expected["sha"], base["sha"] if base else None)
        actual_pair = (actual.get("sha"), actual_base.get("sha") if report.get("base") else None)
        if expected_pair != actual_pair or expected.get("workingTreeHash") != actual.get("workingTreeHash"):
            raise ContractError(f"Page shas {expected_pair}, preflight shas {actual_pair}; rebuild or select the matching preflight")
        if report.get("module") != page_info.get("module"):
            raise ContractError("Page module and preflight module differ")
        revisions = [r for r in (report.get("head"), report.get("base")) if r and r.get("builds")]
        if not report.get("ok") or not actual.get("builds"):
            raise ContractError(actual.get("diagnostic", "Head preflight failed"))
        if report.get("tempDir") and Path(report["tempDir"]).is_dir() and all(Path(r["classpathFile"]).is_file() for r in revisions):
            return report
        print("Preflight worktree or classpath is gone; sbt is rebuilding it.", file=sys.stderr, flush=True)
    else:
        print("No preflight supplied; sbt is rebuilding the page revisions.", file=sys.stderr, flush=True)
    if expected.get("workingTreeHash"):
        raise ContractError("This page uses a working-tree snapshot and its preflight files are gone; rebuild the page")
    from preflight import preflight
    command = page_info.get("toolchain", {}).get("command", ["scala-cli"])
    cli_version = command[command.index("--cli-version") + 1] if "--cli-version" in command else None
    args = argparse.Namespace(project_dir=page_info["projectDir"], module=page_info["module"],
                              head=expected["sha"], base=base["sha"] if base else None,
                              cli_version=cli_version, temp_dir=parent)
    report = preflight(args)
    if not report.get("ok"):
        raise ContractError(report.get("head", {}).get("diagnostic", "Head preflight failed"))
    return report


class Kernel:
    def __init__(self, page_path, page, provenance, preflight, root, effects_allowed=False,
                 idle_minutes=30, max_timeout=60, no_bloop=False):
        self.page_path = Path(page_path)
        self.page = page.encode()
        self.cell_ids = set(re.findall(r'data-cell="([a-z][a-z0-9-]*)"', page)) | {"scratch"}
        self.provenance = provenance
        self.preflight = preflight
        self.revisions = {}
        for name in ("head", "base"):
            item = preflight.get(name)
            if item and item.get("builds"):
                revision = dict(item)
                if "fs2" not in revision:
                    revision["fs2"] = fs2_core(Path(revision["classpathFile"]).read_text().strip())
                self.revisions[name] = revision
        self.root = Path(root)
        self.effects_allowed = effects_allowed
        self.idle_seconds = idle_minutes * 60
        self.max_timeout = max_timeout
        self.no_bloop = no_bloop
        self.token = secrets.token_urlsafe(32)
        self.runs = {}
        self.queue = deque()
        self.warm = {name: False for name in self.revisions}
        self.condition = threading.Condition()
        self.last_api = time.monotonic()
        self.stopping = threading.Event()
        self.active_cancel = None
        self.log_file = self.root / "kernel.log"
        self.log_file.touch()
        self.server = None

    def log(self, path, cell="-", outcome="-", duration=0):
        record = f"{datetime.now(timezone.utc).isoformat()} {path} {cell} {outcome} {duration:.0f}ms\n"
        with self.condition:
            with self.log_file.open("a") as handle:
                handle.write(record)
            sys.stderr.write(record)
            sys.stderr.flush()

    def revision(self, name):
        return self.revisions.get(name)

    def status(self):
        with self.condition:
            revisions = {name: ({key: self.revisions[name].get(key) for key in
                                 ("sha", "scalaVersion", "catsEffect", "fs2")} if name in self.revisions else None)
                         for name in ("head", "base")}
            return {"ok": True, "page": {"basename": self.page_path.name, "path": str(self.page_path),
                    "sha256": hashlib.sha256(self.page).hexdigest()},
                    "revisions": revisions,
                    "effectsAllowed": self.effects_allowed, "defaultTimeoutSeconds": 5,
                    "maxTimeoutSeconds": self.max_timeout,
                    "busy": self.active_cancel is not None, "queued": len(self.queue), "warm": dict(self.warm),
                    "warnings": self.preflight.get("warnings", [])}

    def submit(self, cell_id, source, timeout):
        with self.condition:
            if len(self.queue) >= 4:
                raise OverflowError("Run queue is full")
            run_id = secrets.token_hex(16)
            revisions = {name: {"state": "queued"} for name in ("head", "base") if self.revision(name)}
            record = {"runId": run_id, "cellId": cell_id, "source": source,
                      "timeout": timeout, "state": "queued", "revisions": revisions,
                      "cancel": threading.Event(), "version": 0}
            self.runs[run_id] = record
            self.queue.append(run_id)
            self.condition.notify_all()
            return run_id

    def public_run(self, run):
        return copy.deepcopy({key: run[key] for key in ("runId", "state", "revisions")})

    def get_run(self, run_id, wait):
        with self.condition:
            run = self.runs.get(run_id)
            if not run:
                return None
            version = run["version"]
            if wait and run["state"] not in ("done", "cancelled"):
                self.condition.wait_for(lambda: run["version"] != version or self.stopping.is_set(), wait)
            return self.public_run(run)

    def cancel(self, run_id):
        with self.condition:
            run = self.runs.get(run_id)
            if not run:
                return False
            run["cancel"].set()
            if run["state"] == "queued":
                self.queue.remove(run_id)
                run["state"] = "cancelled"
                for revision in run["revisions"].values():
                    revision.update(state="cancelled", kind="cancelled", render="Run cancelled")
            run["version"] += 1
            self.condition.notify_all()
            return True

    def update(self, run, name, **fields):
        with self.condition:
            run["revisions"][name].update(fields)
            if fields.get("state") in ("compiling", "running"):
                run["state"] = fields["state"]
            run["version"] += 1
            self.condition.notify_all()

    def worker(self):
        for name in ("head", "base"):
            revision = self.revision(name)
            if not revision or self.stopping.is_set():
                continue
            try:
                source, _ = live_driver_source("1", revision)
                result = compile_driver(self.preflight.get("toolchain", {}).get("command", ["scala-cli"]),
                                        revision, source, self.root / "live" / name, self.stopping, self.no_bloop)
                with self.condition:
                    self.warm[name] = result["code"] == 0
            except (OSError, ContractError) as error:
                self.log("warm/" + name, outcome=str(error))
            with self.condition:
                self.condition.notify_all()
        while not self.stopping.is_set():
            with self.condition:
                self.condition.wait_for(lambda: self.queue or self.stopping.is_set(), 1)
                if self.stopping.is_set():
                    return
                if not self.queue:
                    continue
                run = self.runs[self.queue.popleft()]
                self.active_cancel = run["cancel"]
            started = time.monotonic()
            try:
                self.execute(run)
            except Exception as error:
                self.log("worker", run["cellId"], str(error))
                with self.condition:
                    for revision in run["revisions"].values():
                        if revision["state"] not in ("done", "cancelled"):
                            revision.update(state="done", kind="throwable", render=str(error))
                    run["state"] = "done"
                    run["version"] += 1
                    self.condition.notify_all()
            finally:
                with self.condition:
                    self.active_cancel = None
                    self.condition.notify_all()
                self.log("run/" + run["runId"], run["cellId"], run["state"],
                         (time.monotonic() - started) * 1000)

    def execute(self, run):
        for name in ("head", "base"):
            revision = self.revision(name)
            if not revision:
                continue
            if run["cancel"].is_set():
                self.update(run, name, state="cancelled", kind="cancelled", render="Run cancelled")
                continue
            started = time.monotonic()
            source, line = live_driver_source(run["source"], revision,
                                               self.effects_allowed, run["timeout"])
            self.update(run, name, state="compiling")
            workspace = self.root / "live" / name
            compiled = compile_driver(self.preflight.get("toolchain", {}).get("command", ["scala-cli"]),
                                      revision, source, workspace, run["cancel"], self.no_bloop)
            if compiled["code"] == 0 and not compiled["cancelled"]:
                self.update(run, name, state="running")
            result = run_compiled(revision, compiled, workspace, run["timeout"], run["cancel"])
            result.update(state="cancelled" if result["kind"] == "cancelled" else "done",
                          durationMs=round((time.monotonic() - started) * 1000),
                          driverSource=source)
            if result["kind"] == "compileError":
                result["lines"] = compiler_lines(result["diagnostic"], line, len(run["source"].splitlines()))
            self.update(run, name, **result)
        with self.condition:
            run["state"] = "cancelled" if run["cancel"].is_set() else "done"
            run["version"] += 1
            self.condition.notify_all()

    def stop(self):
        self.stopping.set()
        with self.condition:
            if self.active_cancel:
                self.active_cancel.set()
            self.condition.notify_all()
        if self.server:
            threading.Thread(target=self.server.shutdown, daemon=True).start()


def handler_for(kernel):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def headers_common(self, content_type):
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; img-src data:; frame-ancestors 'none'")

        def reply(self, code, value, content_type="application/json; charset=utf-8"):
            data = json.dumps(value).encode() if not isinstance(value, bytes) else value
            self.send_response(code)
            self.headers_common(content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def route(self):
            start = time.monotonic()
            path = urlsplit(self.path).path
            cell = "-"
            outcome = "-"
            try:
                port = kernel.server.server_port
                host = self.headers.get("Host", "")
                if host not in (f"127.0.0.1:{port}", f"localhost:{port}"):
                    outcome = "bad-host"
                    return self.reply(400, {"ok": False})
                origin = self.headers.get("Origin")
                if origin and origin != "http://" + host:
                    outcome = "foreign-origin"
                    return self.reply(403, {"ok": False})
                if not path.startswith("/api/"):
                    if self.command == "GET" and path == "/" + quote(kernel.page_path.name):
                        outcome = "page"
                        return self.reply(200, kernel.page, "text/html; charset=utf-8")
                    outcome = "not-found"
                    return self.reply(404, {"ok": False})
                header = self.headers.get("Authorization", "")
                if not header.isascii() or not hmac.compare_digest(header, "Bearer " + kernel.token):
                    outcome = "unauthorized"
                    return self.reply(401, {"ok": False})
                with kernel.condition:
                    kernel.last_api = time.monotonic()
                if self.command == "GET" and path == "/api/status":
                    outcome = "status"
                    return self.reply(200, kernel.status())
                match = RUN_PATH.fullmatch(path)
                if match:
                    with kernel.condition:
                        cell = kernel.runs.get(match.group(1), {}).get("cellId", "-")
                if self.command == "GET" and match and not match.group(2):
                    try:
                        requested_wait = float(parse_qs(urlsplit(self.path).query).get("wait", [0])[0])
                        if not math.isfinite(requested_wait):
                            raise ValueError("wait must be finite")
                        wait = min(30, max(0, requested_wait))
                    except ValueError:
                        return self.reply(400, {"ok": False})
                    run = kernel.get_run(match.group(1), wait)
                    outcome = "run" if run else "not-found"
                    return self.reply(200 if run else 404, run or {"ok": False})
                if self.command == "POST":
                    content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
                    if content_type != "application/json":
                        outcome = "media-type"
                        return self.reply(415, {"ok": False})
                    try:
                        size = int(self.headers.get("Content-Length", "-1"))
                    except ValueError:
                        size = -1
                    if size > 65536:
                        outcome = "too-large"
                        self.close_connection = True
                        return self.reply(413, {"ok": False})
                    if size < 0:
                        return self.reply(400, {"ok": False})
                    try:
                        body = json.loads(self.rfile.read(size))
                    except (ValueError, UnicodeDecodeError):
                        return self.reply(400, {"ok": False})
                    if path == "/api/runs":
                        cell = body.get("cellId") if isinstance(body, dict) else None
                        source = body.get("source") if isinstance(body, dict) else None
                        timeout = body.get("timeoutSeconds", 5) if isinstance(body, dict) else None
                        if (not isinstance(cell, str) or not CELL_ID.fullmatch(cell) or cell not in kernel.cell_ids or
                            not isinstance(source, str) or not source.strip() or
                            re.search(r'(?m)^\s*//>\s*using\b', source) or
                            not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or
                            not 0 < timeout <= kernel.max_timeout):
                            outcome = "bad-run"
                            return self.reply(400, {"ok": False, "diagnostic": "Invalid cellId, source, directives, or timeout"})
                        try:
                            run_id = kernel.submit(cell, source, timeout)
                        except OverflowError:
                            outcome = "queue-full"
                            return self.reply(429, {"ok": False})
                        outcome = "accepted"
                        return self.reply(202, {"runId": run_id})
                    if match and match.group(2):
                        found = kernel.cancel(match.group(1))
                        outcome = "cancelled" if found else "not-found"
                        return self.reply(200 if found else 404, {"ok": found})
                outcome = "method" if path in ("/api/status", "/api/runs") or match else "not-found"
                return self.reply(405 if outcome == "method" else 404, {"ok": False})
            finally:
                kernel.log(path, cell, outcome, (time.monotonic() - start) * 1000)

        do_GET = route
        do_POST = route

    return Handler


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
    args = parser.parse_args()
    try:
        if (not math.isfinite(args.idle_minutes) or not math.isfinite(args.max_timeout_seconds) or
            args.idle_minutes <= 0 or args.max_timeout_seconds <= 0 or not 0 <= args.port <= 65535):
            raise ContractError("Idle minutes, maximum timeout, and port must be valid positive values")
        page_path = Path(args.page).resolve()
        page, provenance = load_page(page_path)
        report = resolve_preflight(provenance, args.preflight, args.temp_dir)
        for tool in (report.get("toolchain", {}).get("command", ["scala-cli"])[0], "java"):
            if not shutil.which(tool):
                raise ContractError(f"Missing tool: {tool}")
        root = Path(tempfile.mkdtemp(prefix="scala-diff-live-", dir=args.temp_dir)).resolve()
        kernel = Kernel(page_path, page, provenance, report, root, args.allow_effects,
                        args.idle_minutes, args.max_timeout_seconds, args.no_bloop)
        server = ThreadingHTTPServer(("127.0.0.1", args.port), handler_for(kernel))
        server.daemon_threads = True
        kernel.server = server
        url = f"http://127.0.0.1:{server.server_port}/{quote(page_path.name)}#k={kernel.token}"
        print(json.dumps({"ok": True, "url": url, "pid": os.getpid(), "tempDir": str(root),
                          "log": str(kernel.log_file), "effectsAllowed": args.allow_effects,
                          "idleMinutes": args.idle_minutes,
                          "warning": (report.get("base") or {}).get("diagnostic") if report.get("base") and not report["base"].get("builds") else None,
                          "revisions": {name: {"sha": report[name]["sha"]} for name in
                                        ("head", "base") if report.get(name) and report[name].get("builds")}}), flush=True)
        threading.Thread(target=kernel.worker, daemon=True).start()

        def idle_watch():
            while not kernel.stopping.wait(.1):
                if time.monotonic() - kernel.last_api > kernel.idle_seconds:
                    kernel.stop()
        threading.Thread(target=idle_watch, daemon=True).start()
        signal.signal(signal.SIGINT, lambda *_: kernel.stop())
        signal.signal(signal.SIGTERM, lambda *_: kernel.stop())
        server.serve_forever(poll_interval=.1)
        server.server_close()
        return 0
    except (ContractError, OSError, KeyError, ValueError) as error:
        print(json.dumps({"ok": False, "diagnostic": str(error)}), flush=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
