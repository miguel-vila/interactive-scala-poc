"""Compile and run edited snippets against exported revision classpaths."""
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import time
import uuid

HTML_SCRIPTS = Path(__file__).resolve().parents[2] / "explain-scala-diff-html" / "scripts"
if not (HTML_SCRIPTS / "scala_diff.py").is_file():
    print(json.dumps({"ok": False, "diagnostic":
        f"explain-scala-diff-html is not installed next to this skill at {HTML_SCRIPTS}. "
        "Install it with: npx skills add miguel-vila/interactive-scala-poc --skill explain-scala-diff-html"}), flush=True)
    sys.exit(1)
sys.path.insert(0, str(HTML_SCRIPTS))

from scala_diff import ContractError, read_json, HARNESS


def live_command(args, cwd, timeout, cancelled):
    """Run a command that can be cancelled while it or its children are active."""
    with tempfile.TemporaryFile() as out_file, tempfile.TemporaryFile() as err_file:
        with subprocess.Popen(args, cwd=cwd, stdout=out_file, stderr=err_file,
                              start_new_session=True) as proc:
            deadline = time.monotonic() + timeout
            timed_out = False
            while proc.poll() is None:
                if cancelled.is_set() or time.monotonic() >= deadline:
                    timed_out = not cancelled.is_set()
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    proc.wait()
                    break
                time.sleep(.05)
            was_cancelled = cancelled.is_set()
            code = 130 if was_cancelled else 124 if timed_out else proc.returncode
        out_file.seek(0)
        err_file.seek(0)
        return (code, out_file.read(65536).decode(errors="replace"),
                err_file.read(65536).decode(errors="replace"), was_cancelled)


def fs2_core(classpath):
    matches = re.findall(r'(?:^|[/\\])fs2-core_(?:2\.\d+|3)-([^/\\:]+)\.jar', classpath)
    versions = sorted(set(matches))
    if len(versions) > 1:
        raise ContractError("Multiple fs2-core versions in the exported classpath: " + ", ".join(versions))
    return {"present": bool(versions), "version": versions[0] if versions else None}


def live_driver_source(snippet, revision, effects_allowed=False, timeout=5):
    """Return a fresh-process driver and its one-based editor source line."""
    if re.search(r'(?m)^\s*//>\s*using\b', snippet):
        raise ContractError("Editor sources may not add Scala CLI directives")
    ce = revision.get("catsEffect", {})
    fs2 = revision.get("fs2", {})
    runtime = ""
    if ce.get("present"):
        runtime = "import cats.effect.IO\nimport scala.concurrent.duration._\n"
        if ce.get("major") == 3:
            runtime += "import cats.effect.unsafe.implicits.global\n"
        elif ce.get("major") == 2:
            runtime += "import scala.concurrent.ExecutionContext\n"
        else:
            raise ContractError("Unsupported cats-effect major version")
    if fs2.get("present"):
        if not ce.get("present"):
            raise ContractError("fs2-core requires cats-effect for the live runner")
        runtime += "import fs2.Stream\n"
    harness = HARNESS.split("  def execute(", 1)[0]
    instances = ["  trait Runner[T] { def apply(t: => T): Any }",
                 "  trait LowPriorityRunner { implicit def pure[T]: Runner[T] = new Runner[T] { def apply(t: => T): Any = t } }",
                 "  object Runner extends LowPriorityRunner {"]
    if ce.get("present"):
        operation = "t.timeout(" + str(timeout) + ".seconds).unsafeRunSync()" if effects_allowed else "throw new Refused"
        instances.append("    implicit def io[A]: Runner[IO[A]] = new Runner[IO[A]] { def apply(t: => IO[A]): Any = " + operation + " }")
        operation = "t.use(a => IO.pure(a)).timeout(" + str(timeout) + ".seconds).unsafeRunSync()" if effects_allowed else "throw new Refused"
        instances.append("    implicit def resource[A]: Runner[cats.effect.Resource[IO, A]] = new Runner[cats.effect.Resource[IO, A]] { def apply(t: => cats.effect.Resource[IO, A]): Any = " + operation + " }")
    if fs2.get("present"):
        operation = "t.take(20L).compile.toList.timeout(" + str(timeout) + ".seconds).unsafeRunSync()" if effects_allowed else "throw new Refused"
        instances.append("    implicit def stream[A]: Runner[Stream[IO, A]] = new Runner[Stream[IO, A]] { def apply(t: => Stream[IO, A]): Any = " + operation + " }")
    instances += ["  }", "  def run[T](t: => T)(implicit runner: Runner[T]): Any = runner(t)",
                  "  class Refused extends RuntimeException"]
    ce2 = ""
    if ce.get("major") == 2:
        ce2 = ("  implicit val cs: cats.effect.ContextShift[IO] = IO.contextShift(ExecutionContext.global)\n"
               "  implicit val timer: cats.effect.Timer[IO] = IO.timer(ExecutionContext.global)\n")
    prefix = runtime + "\nobject ScalaDiffLive {\n" + harness + "\n".join(instances) + "\n" + ce2 + '''
  def execute(thunk: () => Any): (String, String) = try {
    ("value", render(thunk()))
  } catch {
    case _: Refused => ("refused", "Effects are disabled in this kernel. Restart it with --allow-effects to run cats.effect.IO.")
    case _: java.util.concurrent.TimeoutException => ("timeout", "Timed out after ''' + str(timeout) + ''' seconds")
    case scala.util.control.NonFatal(e) => ("throwable", e.getClass.getName + ": " + Option(e.getMessage).getOrElse(""))
  }
  def main(args: Array[String]): Unit = {
    val (kind, result) = execute(() => run {
'''
    line = prefix.count("\n") + 1
    suffix = '''
    })
    val writer = new java.io.PrintWriter(new java.io.File(args(0)), "UTF-8")
    try writer.println("{\\"kind\\":" + quote(kind) + ",\\"render\\":" + quote(result) + "}")
    finally writer.close()
  }
}
'''
    return prefix + snippet + suffix, line


def compiler_lines(diagnostic, snippet_line, snippet_count):
    lines = set()
    for match in re.finditer(r'(?:Live\.scala:|Live\.scala\s*:)\s*(\d+)', diagnostic):
        line = int(match.group(1)) - snippet_line + 1
        if 1 <= line <= snippet_count:
            lines.add(line)
    return sorted(lines)


def compile_driver(toolchain, revision, source, workspace, cancelled, no_bloop=False):
    workspace = Path(workspace)
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "Live.scala").write_text(source)
    classpath = Path(revision["classpathFile"]).read_text().strip()
    output = workspace / "out"
    args = toolchain + ["compile", "--jvm", "system", "--scala", revision["scalaVersion"],
                        "--extra-jars", classpath, "-d", str(output)]
    if no_bloop:
        args.append("--server=false")
    args.append("Live.scala")
    code, stdout, stderr, was_cancelled = live_command(args, workspace, 300, cancelled)
    return {"code": code, "diagnostic": stderr + stdout, "cancelled": was_cancelled,
            "classpath": classpath, "output": output}


def run_compiled(revision, compile_result, workspace, timeout, cancelled):
    if compile_result["cancelled"]:
        return {"kind": "cancelled", "render": "Run cancelled"}
    if compile_result["code"] == 124:
        return {"kind": "timeout", "render": "Compilation timed out after 300 seconds"}
    if compile_result["code"]:
        return {"kind": "compileError", "render": compile_result["diagnostic"],
                "diagnostic": compile_result["diagnostic"]}
    result_file = Path(workspace) / ("result-" + uuid.uuid4().hex + ".json")
    args = ["java", "-cp", str(compile_result["output"]) + os.pathsep + compile_result["classpath"],
            "ScalaDiffLive", str(result_file)]
    code, stdout, stderr, was_cancelled = live_command(args, workspace, timeout, cancelled)
    if was_cancelled:
        result = {"kind": "cancelled", "render": "Run cancelled"}
    elif code == 124:
        result = {"kind": "timeout", "render": f"Timed out after {timeout} seconds"}
    elif code == 0 and result_file.exists():
        result = read_json(result_file)
    else:
        result = {"kind": "throwable", "render": stderr or f"Driver exited with code {code}"}
    result["output"] = (stdout + stderr)[:65536]
    return result
