from pathlib import Path
import subprocess
import tempfile
import unittest


SCRIPT = Path(__file__).with_name("browser-check.cjs")


class BrowserCheckPaths(unittest.TestCase):
    def test_page_mode_does_not_create_files_in_unrelated_working_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            working_dir = root / "unrelated"
            working_dir.mkdir()
            playwright = root / "playwright"
            playwright.mkdir()
            (playwright / "index.js").write_text(
                "exports.chromium = {launch: async () => ({"
                "newContext: async () => ({newPage: async () => ({on() {}})}),"
                "close: async () => {}})};"
            )
            result = subprocess.run(
                ["node", str(SCRIPT), str(playwright), "", "--page", str(root / "missing.html")],
                cwd=working_dir, capture_output=True, text=True, check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("HTML page is missing", result.stderr)
            self.assertEqual(list(working_dir.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
