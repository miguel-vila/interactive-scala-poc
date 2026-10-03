from pathlib import Path
import unittest

from test_pipeline import load

builder = load("build-page")
ROOT = Path(__file__).resolve().parents[1]


class PageContracts(unittest.TestCase):
    def grid(self):
        values = list(range(5))
        return {"cells": {"test-cell": {"call": "Demo.test(n)", "params": [{"name": "n", "type": "Int", "values": values}],
                    "rows": [{"key": str(n), "values": [n]} for n in values],
                    "results": {str(n): {"base": {"kind": "value", "render": "old"}, "head": {"kind": "value", "render": str(n)}, "differs": True} for n in values},
                    "driverSource": {"head": "head driver", "base": "base driver"}, "effect": "pure"}},
                "provenance": {"module": "core", "scalaVersion": "3.5.0", "head": {"sha": "head", "builds": True}, "base": {"sha": "base", "builds": True}, "droppedCells": []}}

    def narrative(self):
        return {"title": "Example", "background": "<p>Context</p>", "intuition": "<p>Idea</p>", "code": '<pre>Demo.test(n)</pre><div class="scala-cell" data-cell="test-cell"></div>'}

    def test_missing_scaffold_reports_expected_path(self):
        with self.assertRaisesRegex(builder.ContractError, "HTML scaffold missing"):
            builder.build_page(self.grid(), self.narrative(), ROOT / "missing-scaffold.html")

    def test_quiz_answers_are_exact_outputs(self):
        grid = self.grid()
        quiz = builder.verified_quiz(grid)
        self.assertEqual(len(quiz), 5)
        for q in quiz:
            evidence = q["evidence"]
            recorded = grid["cells"][evidence["cellId"]]["results"][evidence["rowKey"]]["head"]
            self.assertEqual(next(o["text"] for o in q["options"] if o.get("correct")), builder.answer(recorded))

    def test_embedded_strings_cannot_close_script_tags(self):
        grid = self.grid()
        grid["cells"]["test-cell"]["results"]["0"]["head"]["render"] = '</script><img src=x onerror=alert(1)>'
        page = builder.build_page(grid, self.narrative())
        self.assertNotIn('<img src=x', page)
        self.assertEqual(page.count("correct: true"), 5)

    def test_generated_drivers_stay_out_of_reader_page(self):
        grid = self.grid()
        page = builder.build_page(grid, self.narrative())
        self.assertEqual(grid["cells"]["test-cell"]["driverSource"]["head"], "head driver")
        self.assertNotIn("head driver", page)
        self.assertNotIn("base driver", page)
        self.assertNotIn("Generated live driver", page)

    def test_head_only_is_visibly_labelled_and_diagnostics_preserved(self):
        grid = self.grid()
        grid["provenance"]["base"] = {"sha": "base", "builds": False, "diagnostic": "compiler <verbatim>"}
        for row in grid["cells"]["test-cell"]["results"].values():
            row.pop("base")
            row["differs"] = False
        page = builder.build_page(grid, self.narrative())
        self.assertIn("The base revision did not build", page)
        self.assertIn("compiler &lt;verbatim&gt;", page)

    def test_network_capable_narrative_is_rejected(self):
        narrative = self.narrative()
        narrative["code"] += '<script>fetch("https://example.invalid")</script>'
        with self.assertRaisesRegex(builder.ContractError, "Network"):
            builder.build_page(self.grid(), narrative)

    def test_live_fragment_and_provenance_are_appended_once_after_validation(self):
        page = builder.build_page(self.grid(), self.narrative())
        self.assertEqual(page.count('id="live-provenance"'), 1)
        self.assertEqual(page.count('Live mode activates only'), 1)
        self.assertLess(page.index('id="live-provenance"'), page.index('Live mode activates only'))
        self.assertIn('"pageVersion": 1', page)
        self.assertIn('"sha": "head"', page)
        self.assertLess(page.index('id="live-provenance"'), page.rindex('</body>'))

    def test_live_fragment_only_fetches_relative_api_paths(self):
        fragment = (ROOT / "references/live.html").read_text()
        self.assertIn('location.protocol === "http:"', fragment)
        self.assertIn('if (location.protocol !== "http:" || !token) return;', fragment)
        self.assertEqual(fragment.count('fetch('), 1)
        self.assertIn('fetch("/api/" + route', fragment)
        self.assertNotIn('https:', fragment)
        self.assertNotIn('ws:', fragment)


if __name__ == "__main__":
    unittest.main()
