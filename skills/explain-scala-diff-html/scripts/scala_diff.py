"""Shared stdlib-only contracts and Scala driver generation. No project dependencies."""
import hashlib
import itertools
import json
import math
import os
from pathlib import Path
import re
import signal
import subprocess
import uuid


class ContractError(ValueError):
    pass


def read_json(path):
    return json.loads(Path(path).read_text())


def emit(value):
    print(json.dumps(value, ensure_ascii=False, indent=2))


def command(args, cwd=None, timeout=300):
    """Kill the process group on timeout, including compiler/JVM children."""
    with subprocess.Popen(args, cwd=cwd, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, text=True, start_new_session=True) as proc:
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            stdout, stderr = proc.communicate()
            return 124, stdout, stderr
    return proc.returncode, stdout, stderr


def normalized(source):
    # Keep string/character literals intact; strip comments only outside literals.
    tokens = re.findall(r'"""[\s\S]*?"""|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|'
                        r'//[^\n]*|/\*[\s\S]*?\*/|[A-Za-z_$][\w$]*|\d+(?:\.\d+)?|[^\s]', source)
    return " ".join(t for t in tokens if not t.startswith(("//", "/*")))


def cache_key(cell, revision):
    # Include the grid and harness configuration as well as the agreed identity.
    payload = dict(cell)
    for name in ("snippet", "setup"):
        payload[name] = normalized(payload.get(name, ""))
    payload["imports"] = [normalized(i) for i in payload.get("imports", [])]
    return hashlib.sha256(json.dumps([cell["cellId"], payload, revision],
                                   sort_keys=True).encode()).hexdigest()


def function_name(cell):
    if cell.get("function"):
        return cell["function"]
    match = re.search(r'([\w.]+)(?:\[[^\]]+\])?\s*\(', cell["snippet"])
    return match.group(1) if match else cell["cellId"]


def check_effects(cells, confirmations):
    for cell in cells:
        effect = cell.get("effect", "pure")
        if effect not in ("pure", "IO", "Resource", "Stream"):
            raise ContractError(f"{cell['cellId']}: unsupported effect {effect!r}; supply a supported adapter")
        if effect != "pure":
            if not cell.get("function"):
                raise ContractError(f"{cell['cellId']}: effectful cells require the fully qualified function name")
            if cell["function"] not in confirmations:
                raise ContractError(f"Explicit user confirmation required for {cell['function']}; "
                                    f"after confirmation pass --confirm-effect {cell['function']}")


def scala_string(value):
    # Scala uses JSON-compatible escapes except escaped slash; ASCII escapes also
    # keep Unicode line separators out of the generated source.
    return json.dumps(value, ensure_ascii=True)


def type_parts(type_name):
    match = re.fullmatch(r'([\w.]+)\[(.*)\]', type_name.strip())
    if not match:
        return type_name.strip().split(".")[-1], []
    args, start, depth = [], 0, 0
    inner = match.group(2)
    for i, char in enumerate(inner):
        depth += (char == "[") - (char == "]")
        if char == "," and depth == 0:
            args.append(inner[start:i].strip())
            start = i + 1
    args.append(inner[start:].strip())
    return match.group(1).split(".")[-1], args


def literal(value, type_name, depth=0):
    if isinstance(value, dict) and "scala" in value:
        if not isinstance(value["scala"], str) or not value["scala"].strip():
            raise ContractError("Scala expressions must be nonempty strings")
        return value["scala"]
    if depth > 2:
        raise ContractError("Input construction exceeds the depth cap of 2")
    kind, args = type_parts(type_name)
    if kind == "String" and isinstance(value, str):
        return scala_string(value)
    if kind == "Char" and isinstance(value, str) and len(value) == 1:
        return "'" + value.replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n").replace("\r", "\\r") + "'"
    if kind == "Boolean" and isinstance(value, bool):
        return str(value).lower()
    if kind in ("Byte", "Short", "Int", "Long") and isinstance(value, int) and not isinstance(value, bool):
        return str(value) + {"Byte": ".toByte", "Short": ".toShort", "Int": "", "Long": "L"}[kind]
    if kind in ("Float", "Double") and isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
        return repr(float(value)) + ("f" if kind == "Float" else "d")
    if kind == "Option" and len(args) == 1:
        return "None" if value is None else "Some(" + literal(value, args[0], depth + 1) + ")"
    if kind in ("List", "Vector", "Set") and len(args) == 1 and isinstance(value, list):
        return f"{kind}[{args[0]}](" + ", ".join(literal(v, args[0], depth + 1) for v in value) + ")"
    if kind == "Map" and len(args) == 2 and isinstance(value, (dict, list)):
        pairs = value.items() if isinstance(value, dict) else value
        return f"Map[{', '.join(args)}](" + ", ".join(literal(k, args[0], depth + 1) + " -> " +
                                                     literal(v, args[1], depth + 1) for k, v in pairs) + ")"
    if type_name.startswith("java.time.") and isinstance(value, str):
        return f"{type_name}.parse({scala_string(value)})"
    raise ContractError(f"Cannot construct {type_name} from {value!r}; provide a {{scala, label}} expression "
                        "or expose raw constructor inputs in the snippet")


def defaults(type_name):
    kind, args = type_parts(type_name)
    if kind == "String":
        return ["example", "", "invalid"]
    if kind == "Char":
        return ["a", "0", " "]
    if kind == "Boolean":
        return [False, True]
    if kind in ("Int", "Long", "Byte", "Short", "Float", "Double"):
        return [0, 1, 3]
    if kind == "Option" and len(args) == 1:
        return [None] + defaults(args[0])[:2]
    if kind in ("List", "Set", "Vector") and len(args) == 1:
        items = defaults(args[0])[:2]
        return [[], items[:1], items]
    if kind == "Map" and len(args) == 2:
        keys, vals = defaults(args[0])[:2], defaults(args[1])[:2]
        return [[], list(zip(keys, vals))[:1], list(zip(keys, vals))]
    raise ContractError(f"{type_name}: supply finite values or enumCases; construction is checked by the compiler")


def resolve_cell(cell):
    cell = json.loads(json.dumps(cell))
    if not re.fullmatch(r'[a-z][a-z0-9-]*', cell.get("cellId", "")):
        raise ContractError("cellId must be a lowercase kebab-case HTML id")
    if not isinstance(cell.get("snippet"), str) or not cell["snippet"].strip():
        raise ContractError(f"{cell['cellId']}: missing snippet")
    if any(re.search(r'(?m)^\s*//>\s*using\b', source) for source in
           [cell["snippet"], cell.get("setup", ""), *cell.get("imports", [])]):
        raise ContractError("Cell sources may not add Scala CLI directives; use only the exported project classpath")
    if not isinstance(cell.get("params", []), list):
        raise ContractError("params must be an array")
    names = set()
    for param in cell.get("params", []):
        name = param.get("name", "")
        if not re.fullmatch(r'[a-zA-Z_][\w]*', name) or name in names:
            raise ContractError(f"Invalid or repeated parameter name {name!r}")
        names.add(name)
        if "values" not in param:
            cases = param.get("enumCases")
            param["values"] = [{"scala": case, "label": case.split(".")[-1]} for case in cases] if cases else defaults(param["type"])
        if not isinstance(param["values"], list) or not param["values"]:
            raise ContractError(f"{name}: values must be a nonempty array")
        if "default" in param and param["default"] not in param["values"]:
            raise ContractError(f"{name}: default must occur in values")
        encodings = [json.dumps(v, sort_keys=True) for v in param["values"]]
        if len(set(encodings)) != len(encodings):
            raise ContractError(f"{name}: repeated values")
        for value in param["values"]:
            literal(value, param["type"])
    cell.setdefault("params", [])
    cell.setdefault("effect", "pure")
    timeout = cell.get("timeoutSeconds", 5)
    if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or not 0 < timeout <= 5:
        raise ContractError("timeoutSeconds must be between 0 and 5 seconds")
    if cell.get("effect") == "Stream" and (not isinstance(cell.get("take", 20), int) or not 1 <= cell.get("take", 20) <= 1000):
        raise ContractError("Stream take must be between 1 and 1000")
    return cell


def grid_rows(cell, probe=False):
    params = cell["params"]
    if probe:
        return [tuple(p.get("default", p["values"][0]) for p in params)]
    return list(itertools.product(*(p["values"] for p in params)))


def row_key(row):
    # Preserve the v1 pipe keys when unambiguous. Use a canonical JSON tuple for
    # pipes, compound values, booleans, nulls or strings resembling other types.
    safe = all((isinstance(v, int) and not isinstance(v, bool)) or
               (isinstance(v, str) and "|" not in v and not re.fullmatch(r'-?\d+', v)) for v in row)
    return "|".join(str(v) for v in row) if safe else json.dumps(row, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def validate_budget(cells):
    total = 0
    for cell in cells:
        rows = math.prod(len(p["values"]) for p in cell["params"])
        if rows > 48:
            raise ContractError(f"{cell['cellId']}: {rows} rows exceeds the 48-row cell budget")
        total += rows
    if total > 200:
        raise ContractError(f"{total} rows exceeds the 200-row page budget")


HARNESS = r'''
  def quote(s: String): String = "\"" + s.flatMap {
    case '"' => "\\\""
    case '\\' => "\\\\"
    case '\n' => "\\n"
    case '\r' => "\\r"
    case '\t' => "\\t"
    case c if c < ' ' => "\\u%04x".format(c.toInt)
    case c => c.toString
  } + "\""
  def render(value: Any, depth: Int = 0): String = {
    if (depth > 8) "…"
    else value match {
      case null => "null"
      case s: String => quote(s)
      case m: scala.collection.Map[_, _] => m.iterator.map { case (k, v) =>
        render(k, depth + 1) + " -> " + render(v, depth + 1)
      }.toList.sorted.mkString("Map(", ", ", ")")
      case s: scala.collection.Set[_] => s.iterator.map(v => render(v, depth + 1)).toList.sorted.mkString("Set(", ", ", ")")
      case l: List[_] => l.map(v => render(v, depth + 1)).mkString("List(", ", ", ")")
      case v: Vector[_] => v.map(x => render(x, depth + 1)).mkString("Vector(", ", ", ")")
      case a: Array[_] => a.map(v => render(v, depth + 1)).mkString("Array(", ", ", ")")
      case p: Product if p.productArity == 0 => p.productPrefix
      case p: Product => p.productIterator.map(v => render(v, depth + 1)).mkString(p.productPrefix + "(", ", ", ")")
      case other => String.valueOf(other)
    }
  }
  def execute(thunk: () => Any): (String, String) = {
    val task = new java.util.concurrent.FutureTask[(String, String)](
      new java.util.concurrent.Callable[(String, String)] {
        def call(): (String, String) = try { ("value", render(callValue())) } catch {
          case _: java.util.concurrent.TimeoutException => ("timeout", "Timed out after TIMEOUT seconds")
          case scala.util.control.NonFatal(e) => ("throwable", e.getClass.getName + ": " + Option(e.getMessage).getOrElse(""))
        }
        def callValue(): Any = thunk()
      })
    val worker = new Thread(task, "scala-diff-row")
    worker.setDaemon(true)
    worker.start()
    try task.get(TIMEOUT_MILLISL, java.util.concurrent.TimeUnit.MILLISECONDS) catch {
      case _: java.util.concurrent.TimeoutException =>
        task.cancel(true)
        ("timeout", "Timed out after TIMEOUT seconds")
      case scala.util.control.NonFatal(e) => ("throwable", e.toString)
    }
  }
'''


def driver_source(cell, revision, rows):
    imports = "\n".join("import " + i.removeprefix("import ") for i in cell.get("imports", []))
    effect = cell["effect"]
    ce = revision.get("catsEffect", {})
    runtime = ""
    if effect != "pure":
        if ce.get("major") not in (2, 3):
            raise ContractError(f"{cell['cellId']}: {effect} requires a detected cats-effect 2 or 3 classpath")
        runtime = "import cats.effect.IO\nimport scala.concurrent.duration._\n"
        if ce["major"] == 3:
            runtime += "import cats.effect.unsafe.implicits.global\n"
        else:
            runtime += "import scala.concurrent.ExecutionContext\n"
    setup_runtime = ""
    if effect != "pure" and ce["major"] == 2:
        setup_runtime = ("implicit val cs: cats.effect.ContextShift[IO] = IO.contextShift(ExecutionContext.global)\n"
                         "implicit val timer: cats.effect.Timer[IO] = IO.timer(ExecutionContext.global)\n")
    calls = []
    for row in rows:
        bindings = "\n".join(f"val {p['name']}: {p['type']} = {literal(v, p['type'])}" for p, v in zip(cell["params"], row))
        expression = "{\n" + cell["snippet"] + "\n}"
        if effect == "Resource":
            expression += ".use(value => IO.pure(value))"
        elif effect == "Stream":
            expression += f".take({cell.get('take', 20)}L).compile.toList"
        if effect != "pure":
            expression = f"IO.defer({expression}).timeout({cell.get('timeoutSeconds', 5)}.seconds).unsafeRunSync()"
        calls.append("(" + scala_string(row_key(row)) + ", () => {\n" + setup_runtime + bindings + "\n" + cell.get("setup", "") + "\n" + expression + "\n})")
    harness = HARNESS.replace("TIMEOUT_MILLIS", str(math.ceil(cell.get("timeoutSeconds", 5) * 1000))).replace("TIMEOUT", str(cell.get("timeoutSeconds", 5)))
    return imports + "\n" + runtime + "\nobject ScalaDiffBatch {\n" + harness + '''
  def main(args: Array[String]): Unit = {
    val rows: List[(String, () => Any)] = List(
''' + ",\n".join(calls) + '''
    )
    val writer = new java.io.PrintWriter(new java.io.File(args(0)), "UTF-8")
    try rows.foreach { case (key, call) =>
      val (kind, result) = execute(call)
      writer.println("{\\"key\\":" + quote(key) + ",\\"kind\\":" + quote(kind) + ",\\"render\\":" + quote(result) + "}")
      writer.flush()
    } finally writer.close()
  }
}
'''


def execute_driver(preflight, cell, revision_name, probe=False, use_cache=True):
    revision = dict(preflight[revision_name])
    revision.setdefault("scalaVersion", preflight["scalaVersion"])
    revision.setdefault("catsEffect", preflight.get("catsEffect", {}))
    rows = grid_rows(cell, probe)
    source = driver_source(cell, revision, rows)
    identity = {"revision": revision, "probe": probe, "harness": hashlib.sha256(normalized(source).encode()).hexdigest()}
    directory = Path(preflight.get("cacheDir", Path(preflight["head"]["classpathFile"]).parent / "drivers")) / cache_key(cell, identity)
    directory.mkdir(parents=True, exist_ok=True)
    cache_file = directory / "results.json"
    # Effects are replayed only after a new confirmation, and never silently read
    # from a cache that could hide changes in ambient configuration.
    if use_cache and cell["effect"] == "pure" and cache_file.exists():
        return read_json(cache_file)
    source_file = directory / "Batch.scala"
    source_file.write_text(source)
    result_file = directory / ("rows-" + uuid.uuid4().hex + ".jsonl")
    classpath = Path(revision["classpathFile"]).read_text().strip()
    args = preflight.get("toolchain", {}).get("command", ["scala-cli"]) + ["run", "--server=false", "--runner=false", "--jvm", "system", "--scala", revision["scalaVersion"],
            "--extra-jars", classpath, "--main-class", "ScalaDiffBatch", str(source_file), "--", str(result_file)]
    code, stdout, stderr = command(args, cwd=directory,
                                   timeout=180 + len(rows) * (cell.get("timeoutSeconds", 5) + 1))
    recorded = {}
    if result_file.exists():
        for line in result_file.read_text().splitlines():
            entry = json.loads(line)
            recorded[entry.pop("key")] = entry
    diagnostic = stderr + stdout
    if code and not recorded:
        kind = "timeout" if code == 124 else "compileError" if "Compilation failed" in diagnostic or "error" in diagnostic.lower() else "throwable"
        recorded = {row_key(row): {"kind": kind, "render": diagnostic or "Driver terminated without results"} for row in rows}
    elif len(recorded) != len(rows):
        for row in rows:
            recorded.setdefault(row_key(row), {"kind": "timeout" if code == 124 else "throwable", "render": diagnostic or "Driver terminated before this row"})
    output = {"results": recorded, "driverSource": source, "diagnostic": diagnostic if code else None}
    if cell["effect"] == "pure" and code == 0:
        cache_file.write_text(json.dumps(output))
    return output


def validate_preflight(preflight, cells):
    if not preflight.get("ok") or not preflight.get("head", {}).get("builds"):
        raise ContractError("Preflight failed; read its verbatim diagnostic before generating a page")
    for cell in cells:
        if cell.get("module", preflight["module"]) != preflight["module"]:
            raise ContractError(f"{cell['cellId']}: module does not match preflight; resolve a separate per-module preflight")


def dropped_hint(diagnostic, preflight):
    missing = re.search(r'not found|Not found|Cannot resolve|value .* is not a member', diagnostic or "")
    if missing:
        return "Check the owning module or refresh the exported classpath. Modules: " + ", ".join(preflight.get("modules", [preflight["module"]]))
    return None
