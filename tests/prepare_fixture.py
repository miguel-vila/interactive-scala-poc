"""Create a reproducible, disposable two-revision sbt fixture in this workspace."""
import json
from pathlib import Path
import shutil
import subprocess
import argparse

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--legacy", action="store_true", help="Include a Scala 2 / cats-effect 2 module")
args = parser.parse_args()
OUTPUT = Path.cwd() / ".validation"
target = OUTPUT / ("fixture-ce2" if args.legacy else "fixture")
if target.exists():
    raise SystemExit("Fixture already exists; use .validation/revisions.json")
target.mkdir(parents=True)
(target / "project").mkdir()
(target / "project/build.properties").write_text("sbt.version=1.10.2\n")
(target / "build.sbt").write_text('''
lazy val root = project.in(file(".")).aggregate(core, legacy)
lazy val core = project.in(file("core")).settings(
  scalaVersion := "3.5.0",
  libraryDependencies ++= Seq(
    "org.typelevel" %% "cats-effect" % "3.5.4",
    "co.fs2" %% "fs2-core" % "3.10.2"
  )
)
lazy val legacy = project.in(file("legacy")).settings(scalaVersion := "2.13.14")
''')
if args.legacy:
    with (target / "build.sbt").open("a") as build:
        build.write('''
legacy / libraryDependencies ++= Seq(
  "org.typelevel" %% "cats-effect" % "2.5.5",
  "co.fs2" %% "fs2-core" % "2.5.11"
)
''')
(target / ".gitignore").write_text("target/\n.bsp/\n.metals/\n")
source = target / "core/src/main/scala/demo/Demo.scala"
source.parent.mkdir(parents=True)
def git(*args):
    return subprocess.check_output(["git", "-C", str(target), *args], text=True).strip()
git("init", "-q")
for revision in ("base", "head"):
    shutil.copy2(ROOT / "tests/fixtures" / revision / "Demo.scala", source)
    if args.legacy:
        legacy_source = target / "legacy/src/main/scala/demo/Demo.scala"
        legacy_source.parent.mkdir(parents=True, exist_ok=True)
        legacy_source.write_text((ROOT / "tests/fixtures" / revision / "Demo.scala").read_text().replace(
            "def slow(): IO[String]", "def slow()(implicit timer: cats.effect.Timer[IO]): IO[String]"))
    git("add", ".")
    git("-c", "user.name=Scala Diff Fixture", "-c", "user.email=fixture@example.invalid", "-c", "commit.gpgsign=false", "commit", "-q", "-m", revision)
    if revision == "base":
        base = git("rev-parse", "HEAD")
report = {"projectDir": str(target), "base": base, "head": git("rev-parse", "HEAD")}
(OUTPUT / ("revisions-ce2.json" if args.legacy else "revisions.json")).write_text(json.dumps(report, indent=2))
print(json.dumps(report, indent=2))
