import importlib.util
import argparse
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import scala_diff as core
import preflight


def load(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), SCRIPTS / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


grid = load("run-grid")
probe = load("probe-types")


class Contracts(unittest.TestCase):
    def cell(self, **fields):
        return dict({"cellId": "decode", "module": "core", "snippet": "Demo.decode(input)",
                     "params": [{"name": "input", "type": "String", "values": ["ok", "bad"]}]}, **fields)

    def test_effect_gate_before_any_execution_in_both_entrypoints(self):
        cell = self.cell(effect="IO", function="Demo.decode")
        with patch.object(core, "command") as run:
            for execute, arg in ((grid.run_grid, [cell]), (probe.probe, cell)):
                with self.assertRaisesRegex(core.ContractError, "Demo.decode"):
                    execute({}, arg)
            run.assert_not_called()

    def test_page_and_cell_budgets_refuse_before_execution(self):
        too_big = core.resolve_cell(self.cell(params=[{"name": "n", "type": "Int", "values": list(range(49))}]))
        with self.assertRaisesRegex(core.ContractError, "48-row"):
            core.validate_budget([too_big])
        cells = [core.resolve_cell(self.cell(cellId="cell-" + str(i), params=[{"name": "n", "type": "Int", "values": list(range(41))}])) for i in range(5)]
        with self.assertRaisesRegex(core.ContractError, "200-row"):
            core.validate_budget(cells)

    def test_normalization_preserves_literals_and_cache_configuration(self):
        a = self.cell(snippet='Demo.decode("a  b") // comment')
        b = self.cell(snippet='Demo . decode ( "a  b" )')
        self.assertEqual(core.cache_key(a, "sha"), core.cache_key(b, "sha"))
        self.assertNotEqual(core.cache_key(a, "sha"), core.cache_key(self.cell(snippet='Demo.decode("a b")'), "sha"))
        self.assertNotEqual(core.cache_key(a, "sha"), core.cache_key(a, "other"))

    def test_keys_do_not_collide_with_pipes(self):
        self.assertNotEqual(core.row_key(("a|b", "c")), core.row_key(("a", "b|c")))
        self.assertEqual(core.row_key((3, "dGVzdA==")), "3|dGVzdA==")

    def test_compound_literals_and_enum_expansion(self):
        self.assertEqual(core.literal([], "List[Int]"), "List[Int]()")
        self.assertEqual(core.literal([["a", 1]], "Map[String, Int]"), 'Map[String, Int]("a" -> 1)')
        self.assertEqual(core.literal(None, "Option[Int]"), "None")
        cell = core.resolve_cell(self.cell(params=[{"name": "mode", "type": "Mode", "enumCases": ["Mode.Fast", "Mode.Slow"]}]))
        self.assertEqual(cell["params"][0]["values"][1]["scala"], "Mode.Slow")

    def test_ce_version_detection_in_exported_classpath(self):
        self.assertEqual(preflight.cats_effect("/tmp/cats-effect_3-3.5.4.jar")["major"], 3)
        self.assertEqual(preflight.cats_effect("/tmp/cats-effect_2.13-2.5.5.jar")["major"], 2)
        self.assertFalse(preflight.cats_effect("/tmp/cats-core_3-2.12.0.jar")["present"])
        self.assertEqual(core.fs2_core("/tmp/fs2-core_3-3.11.0.jar"), {"present": True, "version": "3.11.0"})
        self.assertFalse(core.fs2_core("/tmp/fs2-io_3-3.11.0.jar")["present"])

    def test_live_driver_effect_instances_and_line_offset(self):
        for major in (2, 3):
            revision = {"catsEffect": {"present": True, "major": major},
                        "fs2": {"present": True, "version": "3.0.0"}}
            snippet = "val n = 1\nn + 2"
            refused, line = core.live_driver_source(snippet, revision, False)
            allowed, _ = core.live_driver_source(snippet, revision, True)
            self.assertEqual(refused.splitlines()[line - 1:line + 1], snippet.splitlines())
            self.assertIn("throw new Refused", refused)
            self.assertIn("Runner[Stream[IO, A]]", refused)
            self.assertIn(".unsafeRunSync()", allowed)
            self.assertIn("ContextShift" if major == 2 else "unsafe.implicits.global", allowed)
            self.assertEqual(core.compiler_lines(f"Live.scala:{line + 1}: error", line, 2), [2])
        plain, _ = core.live_driver_source("1", {"catsEffect": {"present": False}, "fs2": {"present": False}})
        self.assertNotIn("import cats.effect.IO", plain)
        self.assertNotIn("Runner[Stream", plain)

    def test_sbt_output_parser(self):
        self.assertEqual(preflight.modules_from("[info] In file:/tmp/demo/\n[info]   * root\n[info]     core\n"), ["root", "core"])

    def test_preflight_shuts_down_each_sbt_client_after_success_or_failed_export(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            subprocess.run(["git", "init", "-q", str(source)], check=True)
            (source / "build.sbt").write_text('scalaVersion := "3.3.3"\n')
            subprocess.run(["git", "-C", str(source), "add", "build.sbt"], check=True)
            subprocess.run(["git", "-C", str(source), "-c", "user.name=Test",
                            "-c", "user.email=test@example.com", "commit", "-qm", "initial"], check=True)
            sha = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
            fake_bin = Path(__file__).parent / "fixtures" / "bin"
            for failure in (None, "head", "cli"):
                log = root / f"{failure or 'success'}.log"
                env = {"PATH": str(fake_bin) + os.pathsep + os.environ["PATH"], "FAKE_SBT_LOG": str(log)}
                if failure == "head":
                    env["FAKE_SBT_FAIL_EXPORT"] = failure
                if failure == "cli":
                    env["FAKE_SCALA_CLI_UNSUPPORTED"] = "3.3.3"
                with patch.dict(os.environ, env):
                    report = preflight.preflight(argparse.Namespace(project_dir=str(source), module="root",
                                            base=sha, head=sha, cli_version=None, temp_dir=str(root)))
                self.assertEqual(report["ok"], failure != "head")
                compatibility_warnings = [warning for warning in report["warnings"]
                                          if "Scala CLI could not compile" in warning]
                if failure == "cli":
                    self.assertEqual(len(compatibility_warnings), 1)
                    self.assertIn("head, base: Scala CLI could not compile Scala 3.3.3", compatibility_warnings[0])
                    self.assertIn("Scala 3.3.3 is unsupported by this Scala CLI", compatibility_warnings[0])
                    self.assertIn("--cli-version <release>", compatibility_warnings[0])
                else:
                    self.assertFalse(compatibility_warnings)
                calls = [json.loads(line) for line in log.read_text().splitlines()]
                self.assertEqual([call["action"] for call in calls],
                                 ["projects", "shutdown", "export", "shutdown", "export", "shutdown"])
                self.assertFalse(list(Path(report["tempDir"]).rglob(".fake-sbt-server")))

    def test_control_clone_has_worktree_and_index_for_linked_builds(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, control, build = (root / name for name in ("source", "control", "build"))

            def git(*args):
                return subprocess.run(["git", *map(str, args)], check=True,
                                      capture_output=True, text=True).stdout.strip()

            git("init", "-q", source)
            (source / "build.sbt").write_text('scalaVersion := "3.3.3"\n')
            git("-C", source, "add", "build.sbt")
            git("-C", source, "-c", "user.name=Test", "-c", "user.email=test@example.com",
                "commit", "-qm", "initial")
            sha = git("-C", source, "rev-parse", "HEAD")

            preflight.create_control_clone(source, control, sha)
            self.assertEqual(git("-C", control, "rev-parse", "HEAD"), sha)
            self.assertTrue((control / "build.sbt").is_file())
            self.assertTrue((control / ".git" / "index").is_file())
            git("-C", control, "worktree", "add", "--detach", build, sha)
            self.assertTrue((build / "build.sbt").is_file())

    def test_cell_cannot_add_untracked_dependencies(self):
        with self.assertRaisesRegex(core.ContractError, "directives"):
            core.resolve_cell(self.cell(setup='//> using dep com.lihaoyi::pprint:0.9.0'))

    def test_driver_effect_adapters_keep_unsafe_out_of_snippet(self):
        for effect in ("IO", "Resource", "Stream"):
            cell = core.resolve_cell(self.cell(effect=effect, function="Demo.decode"))
            for major in (2, 3):
                source = core.driver_source(cell, {"catsEffect": {"major": major}}, core.grid_rows(cell))
                self.assertIn("unsafeRunSync()", source)
                self.assertIn(".timeout(5.seconds)", source)
                self.assertNotIn("unsafeRunSync", cell["snippet"])
                self.assertIn("ContextShift" if major == 2 else "unsafe.implicits.global", source)

    def test_head_only_and_throwables_are_data(self):
        pf = {"ok": True, "module": "core", "head": {"builds": True}, "base": {"builds": False, "diagnostic": "original"}}
        result = {"results": {"ok": {"kind": "value", "render": "ok"}, "bad": {"kind": "throwable", "render": "bad"}}, "driverSource": "driver", "diagnostic": None}
        with patch.object(grid, "execute_driver", return_value=result) as run:
            output = grid.run_grid(pf, [self.cell()])
        self.assertEqual(run.call_count, 1)
        self.assertFalse(output["cells"]["decode"]["results"]["bad"]["differs"])
        self.assertEqual(output["provenance"]["base"]["diagnostic"], "original")

    def test_parallel_grid_matches_sequential_with_fake_scala_cli(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for revision in ("head", "base"):
                (root / f"{revision}.txt").write_text(f"/fake/{revision}.jar\n")
            cells = [self.cell(cellId="first"),
                     self.cell(cellId="rejected", snippet="GRID_REJECT"),
                     self.cell(cellId="effect", effect="IO", function="Demo.effect")]
            preflight_report = {"ok": True, "module": "core", "scalaVersion": "3.5.0",
                                "toolchain": {"command": [str(Path(__file__).parent / "fixtures/bin/scala-cli")]},
                                "catsEffect": {"present": True, "major": 3},
                                **{revision: {"builds": True, "classpathFile": str(root / f"{revision}.txt")}
                                   for revision in ("head", "base")}}
            outputs = []
            for jobs in (1, 3):
                log = root / f"jobs-{jobs}.jsonl"
                preflight_report["cacheDir"] = str(root / f"cache-{jobs}")
                with patch.dict(os.environ, {"FAKE_GRID_LOG": str(log)}):
                    outputs.append(grid.run_grid(preflight_report, cells, ["Demo.effect"], jobs=jobs))
                calls = [json.loads(line) for line in log.read_text().splitlines()]
                self.assertEqual(len(calls), 5)
                self.assertFalse(any(call["revision"] == "base" and call["rejected"] for call in calls))
                if jobs == 3:
                    self.assertTrue(any(a["start"] < b["end"] and b["start"] < a["end"]
                                        for i, a in enumerate(calls) for b in calls[i + 1:]))
            self.assertEqual(outputs[0]["cells"], outputs[1]["cells"])
            for field in ("droppedCells", "effects"):
                self.assertEqual(outputs[0]["provenance"][field], outputs[1]["provenance"][field])
            self.assertEqual(list(outputs[1]["cells"]), ["first", "effect"])
            self.assertEqual([entry["cellId"] for entry in outputs[1]["provenance"]["droppedCells"]],
                             ["rejected"])
            self.assertEqual([entry["cellId"] for entry in outputs[1]["provenance"]["effects"]], ["effect"])

    def test_grid_rejects_nonpositive_jobs_before_execution(self):
        with patch.object(grid, "execute_driver") as run:
            with self.assertRaisesRegex(core.ContractError, "--jobs"):
                grid.run_grid({"ok": True, "module": "core", "head": {"builds": True}},
                              [self.cell()], jobs=0)
            run.assert_not_called()

    def test_grid_cli_writes_full_grid_and_prints_counts(self):
        sample = {"cells": {"decode": {"rows": [{"key": "ok"}, {"key": "bad"}], "results": {
            "ok": {"differs": True}, "bad": {"differs": False}}}},
            "provenance": {"droppedCells": [{"cellId": "rejected"}]}}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "preflight.json").write_text("{}")
            (root / "cells.json").write_text("[]")
            args = ["run-grid.py", "--preflight", str(root / "preflight.json"),
                    "--cells", str(root / "cells.json"), "--output", str(root / "grid.json")]
            stdout = io.StringIO()
            with patch.object(grid, "run_grid", return_value=sample), patch.object(sys, "argv", args), redirect_stdout(stdout):
                self.assertEqual(grid.main(), 0)
            self.assertEqual(core.read_json(root / "grid.json"), sample)
            self.assertEqual(json.loads(stdout.getvalue()), {"cells": 1, "rows": 2,
                                                             "differingRows": 1, "droppedCells": 1})
            self.assertEqual(len(stdout.getvalue().splitlines()), 1)
            stdout = io.StringIO()
            with patch.object(grid, "run_grid", return_value=sample), patch.object(sys, "argv", args[:-2]), redirect_stdout(stdout):
                self.assertEqual(grid.main(), 0)
            self.assertEqual(json.loads(stdout.getvalue()), sample)

    def test_probe_cli_saves_resolved_cell_and_replaces_admissible_cell(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "preflight.json").write_text("{}")
            cell = self.cell(params=[{"name": "input", "type": "String", "values": ["new"]}])
            (root / "cell.json").write_text(json.dumps(cell))
            (root / "cells.json").write_text(json.dumps({"cells": [self.cell(), self.cell(cellId="other")]}))
            args = ["probe-types.py", "--preflight", str(root / "preflight.json"),
                    "--cell", str(root / "cell.json"), "--output", str(root / "resolved.json"),
                    "--append-to", str(root / "cells.json")]
            result = {"cellId": "decode", "admissible": True, "diagnostic": None, "cell": cell}
            stdout = io.StringIO()
            with patch.object(probe, "probe", return_value=result), patch.object(sys, "argv", args), redirect_stdout(stdout):
                self.assertEqual(probe.main(), 0)
            self.assertEqual(core.read_json(root / "resolved.json"), cell)
            cells = core.read_json(root / "cells.json")["cells"]
            self.assertEqual([c["cellId"] for c in cells], ["decode", "other"])
            self.assertEqual(cells[0], cell)
            self.assertEqual(json.loads(stdout.getvalue()), {"cellId": "decode", "admissible": True})
            self.assertEqual(len(stdout.getvalue().splitlines()), 1)
            probe.append_cell(root / "new-cells.json", self.cell(cellId="new"))
            self.assertEqual([c["cellId"] for c in core.read_json(root / "new-cells.json")], ["new"])

    def test_rejected_probe_does_not_append_cell(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "preflight.json").write_text("{}")
            cell = self.cell()
            (root / "cell.json").write_text(json.dumps(cell))
            args = ["probe-types.py", "--preflight", str(root / "preflight.json"),
                    "--cell", str(root / "cell.json"), "--output", str(root / "resolved.json"),
                    "--append-to", str(root / "cells.json")]
            result = {"cellId": "decode", "admissible": False, "diagnostic": "compiler rejected", "cell": cell}
            stdout = io.StringIO()
            with patch.object(probe, "probe", return_value=result), patch.object(sys, "argv", args), redirect_stdout(stdout):
                self.assertEqual(probe.main(), 1)
            self.assertEqual(core.read_json(root / "resolved.json"), cell)
            self.assertFalse((root / "cells.json").exists())
            self.assertEqual(json.loads(stdout.getvalue()), {"cellId": "decode", "admissible": False,
                                                             "diagnostic": "compiler rejected"})


if __name__ == "__main__":
    unittest.main()
