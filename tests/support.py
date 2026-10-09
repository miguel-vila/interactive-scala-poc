"""Paths and script loading shared by repository tests and acceptance tools."""
import importlib.util
from pathlib import Path
import sys

# Direct acceptance-tool execution puts tests/ on sys.path. Its html/
# package must not shadow Python's standard library html module.
TESTS = Path(__file__).resolve().parent
sys.path[:] = [entry for entry in sys.path if Path(entry).resolve() != TESTS]
ROOT = TESTS.parent
HTML_SKILL = ROOT / "skills/explain-scala-diff-html"
LIVE_SKILL = ROOT / "skills/explain-scala-diff-live"
FIXTURES = ROOT / "tests/fixtures"
sys.path.insert(0, str(HTML_SKILL / "scripts"))
sys.path.insert(0, str(LIVE_SKILL / "scripts"))


def load(skill, script):
    name = skill.name.replace("-", "_") + "_" + script.replace("-", "_")
    spec = importlib.util.spec_from_file_location(name, skill / "scripts" / (script + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
