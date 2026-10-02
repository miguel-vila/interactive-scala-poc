"""Install the reviewed skill into SKILLS and copy its two verified example pages."""
import json
from pathlib import Path
import shutil

workspace = Path(__file__).resolve().parent
source = workspace / "skills/explain-scala-diff-html"
repo = Path("/Users/miguelvilagonzalez/repos/SKILLS")
target = repo / "skills/explain-scala-diff-html"
readme = repo / "README.md"
current = readme.read_text()
marker = "| [`zeit`]"
row = "| [`explain-scala-diff-html`](skills/explain-scala-diff-html) | Builds offline Scala before/after consoles and quizzes verified against real executions. |\n"
if target.exists():
    raise SystemExit(f"Refusing to overwrite an existing skill: {target}")
if marker not in current or "[`explain-scala-diff-html`]" in current:
    raise SystemExit("README changed; review the skill table before installation")
pages = ["2026-10-01-explanation-scala-diff.html", "2026-10-01-explanation-optional-source-regions.html"]
explanations = Path.home() / "explanations"
for name in pages:
    if not (workspace / ".validation" / name).exists():
        raise SystemExit(f"Missing verified page: {name}")
    if (explanations / name).exists():
        raise SystemExit(f"Refusing to overwrite an existing explanation: {name}")
shutil.copytree(source, target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
readme.write_text(current.replace(marker, row + marker, 1))
explanations.mkdir(parents=True, exist_ok=True)
for name in pages:
    shutil.copy2(workspace / ".validation" / name, explanations / name)
print(json.dumps({"skill": str(target), "pages": [str(explanations / name) for name in pages]}, indent=2))
