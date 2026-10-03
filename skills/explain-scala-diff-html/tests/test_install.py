import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

import test_page


REPO = Path(__file__).resolve().parents[3]
SKILL_NAME = "explain-scala-diff-html"


class LocalInstallation(unittest.TestCase):
    def run_installer(self, checkout, destination):
        return subprocess.run(
            [sys.executable, "-B", str(checkout / "install.py"), "--dest", str(destination)],
            text=True, capture_output=True, check=False,
        )

    def test_linked_install_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "agent" / "skills"
            first = self.run_installer(REPO, destination)
            self.assertEqual(first.returncode, 0, first.stderr + first.stdout)
            target = destination / SKILL_NAME
            self.assertTrue(target.is_symlink())
            self.assertEqual(target.resolve(), REPO / "skills" / SKILL_NAME)
            second = self.run_installer(REPO, destination)
            self.assertEqual(second.returncode, 0, second.stderr + second.stdout)
            self.assertTrue(json.loads(second.stdout)["alreadyInstalled"])

    def test_existing_directory_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory)
            target = destination / SKILL_NAME
            target.mkdir()
            sentinel = target / "user-content.txt"
            sentinel.write_text("Keep this installation.")
            result = self.run_installer(REPO, destination)
            self.assertEqual(result.returncode, 1)
            self.assertFalse(json.loads(result.stdout)["ok"])
            self.assertEqual(sentinel.read_text(), "Keep this installation.")
            self.assertFalse(target.is_symlink())

    def test_existing_dangling_link_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory)
            target = destination / SKILL_NAME
            target.symlink_to(destination / "missing-checkout", target_is_directory=True)
            result = self.run_installer(REPO, destination)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(target.readlink(), destination / "missing-checkout")

    def test_missing_required_file_prevents_installation(self):
        required = (
            "SKILL.md",
            "references/html-scaffold.html",
            "references/console.html",
            "references/live.html",
        )
        for name in required:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                checkout = root / "checkout"
                source = checkout / "skills" / SKILL_NAME
                shutil.copytree(REPO / "skills" / SKILL_NAME, source,
                                ignore=shutil.ignore_patterns("__pycache__"))
                shutil.copy2(REPO / "install.py", checkout / "install.py")
                (source / name).unlink()
                destination = root / "agent" / "skills"

                result = self.run_installer(checkout, destination)

                self.assertEqual(result.returncode, 1, result.stderr + result.stdout)
                self.assertIn(str(source / name), json.loads(result.stdout)["diagnostic"])
                self.assertFalse((destination / SKILL_NAME).exists())

    def test_isolated_checkout_builds_page_through_installed_link(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            checkout = root / "checkout"
            source = checkout / "skills" / SKILL_NAME
            shutil.copytree(REPO / "skills" / SKILL_NAME, source,
                            ignore=shutil.ignore_patterns("__pycache__"))
            shutil.copy2(REPO / "install.py", checkout / "install.py")
            destination = root / "agent" / "skills"
            installed = self.run_installer(checkout, destination)
            self.assertEqual(installed.returncode, 0, installed.stderr + installed.stdout)
            grid = root / "grid.json"
            narrative = root / "narrative.json"
            grid.write_text(json.dumps(test_page.PageContracts().grid()))
            narrative.write_text(json.dumps(test_page.PageContracts().narrative()))
            output = root / "example.html"
            result = subprocess.run(
                [sys.executable, "-B", str(destination / SKILL_NAME / "scripts" / "build-page.py"),
                 "--grid", str(grid), "--narrative", str(narrative),
                 "--slug", "isolated-install", "--output", str(output)],
                cwd=root, text=True, capture_output=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            page = output.read_text()
            self.assertIn('data-cell="test-cell"', page)
            self.assertEqual(page.count("correct: true"), 5)


if __name__ == "__main__":
    unittest.main()
