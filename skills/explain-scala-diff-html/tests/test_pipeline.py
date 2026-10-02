import importlib.util
import json
from pathlib import Path
import sys
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

    def test_sbt_output_parser(self):
        self.assertEqual(preflight.modules_from("[info] In file:/tmp/demo/\n[info]   * root\n[info]     core\n"), ["root", "core"])

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


if __name__ == "__main__":
    unittest.main()
