import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "browser-check.py"
SPEC = importlib.util.spec_from_file_location("browser_wrapper", SCRIPT)
WRAPPER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(WRAPPER)


class BrowserWrapperTests(unittest.TestCase):
    def run_wrapper(self, page, cache, chrome):
        calls = []
        module = cache / "node_modules" / "playwright"
        browser = cache / "browsers" / "chromium" / "chrome"

        def fake_run(command, *, env=None):
            calls.append(command)
            if command[0] == "npm":
                module.mkdir(parents=True)
                (module / "index.js").touch()
            elif command[:2] == ["node", "-e"]:
                return str(browser) + "\n"
            elif command[0].endswith("/playwright"):
                browser.parent.mkdir(parents=True)
                browser.touch()
            else:
                self.assertEqual(command[-2:], ["--page", str(page.resolve())])
                self.assertEqual(env["PLAYWRIGHT_BROWSERS_PATH"], str(cache / "browsers"))
                return '{"ok":true,"offlineCombinations":5}\n'
            return ""

        output = io.StringIO()
        with patch.object(sys, "argv", [str(SCRIPT), "--page", str(page)]), \
                patch.object(WRAPPER, "cache_dir", return_value=cache), \
                patch.object(WRAPPER, "system_chrome", return_value=chrome), \
                patch.object(WRAPPER, "run", side_effect=fake_run), \
                contextlib.redirect_stdout(output):
            WRAPPER.main()
            WRAPPER.main()
        lines = output.getvalue().splitlines()
        self.assertEqual(len(lines), 2)
        for line in lines:
            result = json.loads(line)
            self.assertTrue(result["ok"])
            self.assertEqual(result["screenshots"]["desktop"], str(page.resolve().with_name("example-desktop.png")))
            self.assertEqual(result["screenshots"]["mobile"], str(page.resolve().with_name("example-mobile.png")))
            self.assertEqual(result["screenshots"]["console"], str(page.resolve().with_name("example-console.png")))
        self.assertEqual(sum(command[0] == "npm" for command in calls), 1)
        return calls

    def test_reuses_playwright_and_system_chrome_without_downloading(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            page = root / "example.html"
            page.touch()
            calls = self.run_wrapper(page, root / "cache", "/system/chrome")
            self.assertEqual(len(calls), 3)
            self.assertEqual(calls[-1][3], "/system/chrome")

    def test_downloads_chromium_once_when_no_system_browser_exists(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            page = root / "example.html"
            page.touch()
            calls = self.run_wrapper(page, root / "cache", None)
            self.assertEqual(sum(command[0].endswith("/playwright") for command in calls), 1)
            self.assertEqual(calls[-1][3], "")

    def test_invalid_explicit_chrome_path_is_an_error(self):
        with patch.dict(os.environ, {"CHROME_PATH": "/missing/chrome"}):
            with self.assertRaisesRegex(RuntimeError, "CHROME_PATH is not an executable"):
                WRAPPER.system_chrome()


if __name__ == "__main__":
    unittest.main()
