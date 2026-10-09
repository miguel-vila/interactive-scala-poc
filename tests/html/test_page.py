from pathlib import Path
from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
import sys
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from tests.support import HTML_SKILL, load

builder = load(HTML_SKILL, "build-page")
ROOT = HTML_SKILL


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

    def test_direct_acceptance_import_keeps_standard_library_html(self):
        tests_dir = HTML_SKILL.parents[1] / "tests"
        command = [sys.executable, "-c", "import sys; sys.path.insert(0, sys.argv[1]); "
                   "import support; support.load(support.HTML_SKILL, 'build-page')", str(tests_dir)]
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

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

    def test_quiz_spreads_questions_across_recorded_cells(self):
        grid = self.grid()
        grid["cells"]["alpha"] = deepcopy(grid["cells"]["test-cell"])
        grid["cells"]["beta"] = deepcopy(grid["cells"]["test-cell"])
        quiz = builder.verified_quiz(grid)
        self.assertEqual([q["evidence"]["cellId"] for q in quiz],
                         ["alpha", "alpha", "beta", "beta", "test-cell"])
        self.assertEqual(quiz, builder.verified_quiz(grid))

    def test_quiz_uses_more_rows_from_a_cell_after_other_cells_run_out(self):
        grid = self.grid()
        only_row = deepcopy(grid["cells"]["test-cell"])
        only_row["rows"] = only_row["rows"][:1]
        grid["cells"]["second"] = only_row
        quiz = builder.verified_quiz(grid)
        self.assertEqual([q["evidence"]["cellId"] for q in quiz].count("second"), 1)
        self.assertEqual([q["evidence"]["cellId"] for q in quiz].count("test-cell"), 4)

    def test_quiz_prefers_changed_rows_and_appends_explanations_to_every_option(self):
        grid = self.grid()
        grid["cells"]["test-cell"]["results"]["0"]["differs"] = False
        explanation = "The new branch handles this input."
        quiz = builder.verified_quiz(grid, {"test-cell": explanation})
        self.assertEqual([q["evidence"]["rowKey"] for q in quiz], ["1", "2", "3", "4", "0"])
        for question in quiz:
            expected = builder.answer(grid["cells"]["test-cell"]["results"][question["evidence"]["rowKey"]]["head"])
            for option in question["options"]:
                self.assertIn(expected, option["feedback"])
                self.assertTrue(option["feedback"].endswith(explanation))

    def test_explanation_must_name_a_recorded_cell(self):
        narrative = self.narrative()
        narrative["explanations"] = {"missing-cell": "A reason."}
        with self.assertRaisesRegex(builder.ContractError, "unrecorded cell missing-cell"):
            builder.build_page(self.grid(), narrative)

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

    def test_scaffold_authoring_comments_stay_out_of_reader_page(self):
        page = builder.build_page(self.grid(), self.narrative())
        for instruction in ("Copy this file", "fill QUIZ with five questions",
                            "One entry per question", "position of the correct answer"):
            self.assertNotIn(instruction, page)
        self.assertEqual(page.count("correct: true"), 5)

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

    def test_narrative_rejects_section_wrappers_and_h2_headings_with_guidance(self):
        for section in ("background", "intuition", "code"):
            for fragment in ('<section id="code"><p>Nested</p></section>', '<h2>Nested heading</h2>'):
                with self.subTest(section=section, fragment=fragment):
                    narrative = self.narrative()
                    narrative[section] = fragment + narrative[section]
                    with self.assertRaisesRegex(builder.ContractError,
                                                rf"Narrative {section}.*inner HTML.*<section>.*<h2>"):
                        builder.build_page(self.grid(), narrative)

    def test_offline_provenance_and_no_live_runtime(self):
        page = builder.build_page(self.grid(), self.narrative())
        self.assertEqual(page.count('id="page-provenance"'), 1)
        for forbidden in ('fetch(', '.live-panel', 'live-provenance', 'kernelScript'):
            self.assertNotIn(forbidden, page)
        info = json.loads(page.split('id="page-provenance">', 1)[1].split('</script>', 1)[0])
        self.assertEqual(info["pageVersion"], 2)
        self.assertEqual(info["head"]["sha"], "head")
        self.assertIsNone(info["preflight"])
        self.assertLess(page.index('id="page-provenance"'), page.rindex('</body>'))
        builder.validate_page(page, builder.verified_quiz(self.grid()))

    def test_builder_records_absolute_preflight_path(self):
        with tempfile.TemporaryDirectory() as directory:
            preflight = Path(directory) / "preflight.json"
            preflight.write_text(json.dumps(self.grid()["provenance"]))
            page = builder.build_page(self.grid(), self.narrative(), preflight=preflight)
            info = json.loads(page.split('id="page-provenance">', 1)[1].split('</script>', 1)[0])
            self.assertEqual(info["preflight"], str(preflight.resolve()))

    def test_validation_checks_the_finished_document(self):
        with tempfile.TemporaryDirectory() as directory:
            scaffold = Path(directory) / "scaffold.html"
            original = (HTML_SKILL / "references/html-scaffold.html").read_text()
            scaffold.write_text(original.replace('</body>', '<script>fetch("/api/status")</script></body>'))
            with self.assertRaisesRegex(builder.ContractError, "Network"):
                builder.build_page(self.grid(), self.narrative(), scaffold=scaffold)

    def test_builder_cli_prints_only_final_summary(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "grid.json").write_text(json.dumps(self.grid()))
            (root / "narrative.json").write_text(json.dumps(self.narrative()))
            (root / "preflight.json").write_text(json.dumps(self.grid()["provenance"]))
            args = ["build-page.py", "--grid", str(root / "grid.json"),
                    "--narrative", str(root / "narrative.json"), "--slug", "example",
                    "--output", str(root / "page.html"), "--preflight", str(root / "preflight.json")]
            stdout = io.StringIO()
            with patch.object(sys, "argv", args), redirect_stdout(stdout):
                self.assertEqual(builder.main(), 0)
            self.assertEqual(len(stdout.getvalue().splitlines()), 1)
            self.assertEqual(json.loads(stdout.getvalue()), {"ok": True, "path": str((root / "page.html").resolve()),
                                                             "cells": 1, "quizQuestions": 5})
            self.assertTrue((root / "page.html").exists())
            self.assertIn(str((root / "preflight.json").resolve()), (root / "page.html").read_text())


if __name__ == "__main__":
    unittest.main()
