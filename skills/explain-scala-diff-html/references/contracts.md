# Script contracts

The scripts print JSON to stdout (a summary when writing their main output to
a file), retain diagnostics as data, and return nonzero for contract failures.
`scala_diff.py` shares codegen and contracts. No jq or Python packages are required.

## Preflight

`python3 "$skill/scripts/preflight.py" <project-dir> [--module <sbt-project-id>] [--base <revision>]
[--head <revision>] [--build-root <dir>] [--classpath-file <path>] [--scala-version <version>]
[--base-classpath-file <path>] [--base-scala-version <version>]
[--temp-dir <parent>] [--cli-version <release>]`

For sbt, the module is the sbt project id, not its published artifact name. An ambiguous
multi-module build fails with the module list. Versions/classpaths are resolved
independently for head and base. sbt uses `-error --client` and an explicit command
sequence: `print <module>/scalaVersion; export <module>/Runtime/fullClasspath`.
`--build-root` locates a nested sbt build relative to the git root; it defaults
to the git root. Preflight runs sbt in the matching revision's build directory.
Preflight runs `sbt --client shutdown` in each build directory after project discovery
and after each revision export, including when an sbt command fails. A failed
shutdown stops preflight with a diagnostic so a server is not silently left running.
The exported fullClasspath also compiles the project. Logs and diagnostics are
retained verbatim. Compile diagnostics suggesting missing symbols include a
separate module/classpath hint; never replace or paraphrase the diagnostic.

Preflight makes a private shared control clone under `--temp-dir` (or in a system
temporary directory when that option is omitted). It checks out the head commit
there to give JGit a worktree and index, then attaches
detached worktrees to it. Both sbt builds run in the detached worktrees. Nothing is
created in the original target repository, including worktree metadata. Omitted
`--head` snapshots tracked changes and non-ignored untracked files, adding a
`workingTreeHash` to provenance. Untracked symlinks require selecting a committed
revision. The report's `tempDir` contains worktrees and classpath files; supplied
classpaths may point to compiled classes outside it. See
[the skill workflow](../SKILL.md#2-gather-context-and-run-preflight) for the session directory lifetime.

For a revision built outside sbt, pass both its classpath file and Scala version:
`--classpath-file` and `--scala-version` for head, or `--base-classpath-file`
and `--base-scala-version` for base. Base flags require `--base`. A classpath file
contains one OS-separated classpath with absolute paths to existing compiled
classes and dependencies. Preflight copies it into `tempDir`, checks its entries,
detects cats-effect, and creates the revision worktree without invoking
sbt for that revision. If both revisions use supplied classpaths, sbt is not
required. In this case `--module` defaults to `root`; set it to the module name
used by the cells. `toolchain.sbtClient` is true when any revision uses sbt.

Build the selected revision and produce its classpath before running preflight:

- Mill: `./mill show <module>.runClasspath > run-classpath.json` returns a JSON
  array. Convert it with
  `python3 -c 'import json, os, sys; from pathlib import Path; print(os.pathsep.join(str(Path(p).resolve()) for p in json.load(sys.stdin)))' < run-classpath.json > cp.txt`.
- Maven: run `mvn compile dependency:build-classpath -Dmdep.outputFile=cp.txt`,
  then prepend the absolute `target/classes` path to the dependency classpath in
  `cp.txt`.
- Gradle (Groovy DSL): add
  `tasks.register('printClasspath') { dependsOn 'classes'; doLast { println sourceSets.main.runtimeClasspath.asPath } }`
  to `build.gradle`, then run `./gradlew -q printClasspath > cp.txt`.
- Scala CLI: run `scala-cli compile --print-class-path <sources> > cp.txt`.

For each tool, resolve paths to absolute paths before passing the file to
preflight. Supply the Scala version used to build that revision. Repeat for base
when its build or version differs from head.

Report fields: `ok`, `projectDir`, `module`, `modules`, `scalaVersion`,
`catsEffect: {present, major, version}`, `head`, optional/null `base`,
`toolchain: {scalaCli, sbtClient}`, `warnings`, `tempDir`, `cacheDir`.
Each revision includes `sha`, `worktree`, `classpathFile` on success, `builds`,
`scalaVersion` and `catsEffect`. sbt revisions also
include `buildLog`; failed builds include `diagnostic`.
Top-level Scala/CE versions describe head. Mixed suffixes produce warnings.
`toolchain.command` records the exact CLI selection used by both drivers.
An explicit `--cli-version` opts into Scala CLI's separate-release launcher;
it may download that version but never changes the installed default. Preflight
checks whether the selected CLI compiles each resolved Scala version and includes
the compiler diagnostic and a `--cli-version <release>` hint in `warnings` when
it fails. See the
[official compatibility table](https://scala-cli.virtuslab.org/docs/reference/scala-versions/).
Use a separate preflight per owning module; never reuse one module's classpath
for another. Do not hide a base-build failure or a Scala-version difference.

## Cell specifications

```json
{
  "cellId": "decode-retry",
  "module": "core",
  "function": "demo.Demo.ioDecode",
  "snippet": "demo.Demo.ioDecode(input)",
  "effect": "IO",
  "imports": ["demo._"],
  "setup": "",
  "params": [
    {"name": "input", "type": "String", "values": ["dGVzdA==", "", "!!!"], "default": "dGVzdA=="}
  ]
}
```

`effect` defaults to `pure`. Supported values: `pure`, `IO`, `Resource`, `Stream`.
`effect` describes the snippet's result type only. `setup` runs as plain
statements before the wrapper and must be pure; the confirmation gate and the
result cache do not inspect it.

Every effectful cell requires `function`, the fully qualified function actually
invoked. IO-defer wraps snippet evaluation; Resource uses `.use(IO.pure)`;
Stream uses `.take(n).compile.toList` with `take` defaulting to 20, maximum 1000.
CE2 injects ContextShift/Timer, CE3 injects its global runtime. Effects and pure
calls have a per-row timeout (default 5 seconds; `timeoutSeconds` may shorten it).
All drivers have a daemon-thread watchdog, and subprocesses have an outer bound.
A non-cooperative pure call cannot be safely killed as a JVM thread; the daemon
watchdog bounds the result, but interrupted work may continue until batch exit.
Use approved, local functions and isolate resources in each cell.

Parameter order defines grid order and row keys. `default` must be one of
`values`; otherwise the first value is the probe input and initial widget value.
Primitive, Option, List/Set/Vector/Map inputs can omit values to get small grids.
Map literals use JSON key/value pairs to preserve numeric keys. java.time types
use strings parsed by their public parse method. For enums/sealed case objects,
survey the source and provide **every** public case in `enumCases`; the resolver
expands these into dropdown values and the generated driver checks them.

```json
{"name":"mode","type":"demo.Mode","enumCases":["demo.Mode.Strict","demo.Mode.Lenient"]}
```

Literal inputs nest at most two levels. Wrappers, opaque types, and case classes
use `{"scala":"demo.Id(\"abc\")","label":"abc"}` expressions compiled as written;
the successful probe decides admissibility. Prefer raw primitive parameters plus
visible construction when a validating constructor can reject an input. Function
inputs, handles, resources, and abstract implementations are outside the survey's
admissible set.
Never infer admissibility from a signature.

## Probe and grid

`probe-types.py --preflight <json> --cell <cell.json>
[--output <resolved-cell.json>] [--append-to <cells.json>]
[--confirm-effect <fully-qualified-function>]`

The probe compiles and runs one default call per revision. Its `admissible` field
describes head. Domain Left/None are values; thrown or timed-out defaults reject
the cell. Returns `cellId`, `admissible`, `diagnostic`, `params` (resolved), `cell`
(complete resolved spec), and `revisions` (individual verdicts and diagnostics).
A rejected base probe is preserved independently; it does not reject head.
Keep failed probes in a JSON array of `{cellId, diagnostic}` for `--dropped`.
`--output` saves the resolved `cell` even for a rejected probe. `--append-to`
adds or replaces an admissible cell by `cellId` in an array or a `{"cells": [...]}`
object; a rejected cell is not added. The CLI prints one summary line with
`cellId`, `admissible`, and the rejection diagnostic.

`run-grid.py --preflight <json> --cells <cells.json> [--dropped <json>] [--output <grid.json>]
[--confirm-effect <fully-qualified-function>] [--jobs <count>]` (repeat confirmations as needed).

`--jobs` bounds concurrent Scala CLI runs. It defaults to the CPU count, capped
at four; use `--jobs 1` for sequential execution.

The input is an array, or `{"cells": [...]}`. Budgets are per parameter
combination, not multiplied by two revisions. Refuse the entire request before
any run if a cell exceeds 48 or the page exceeds 200 rows. Each cell/revision is
one scala-cli compile/run, followed by all its calls. Throwable/timeout rows do
not abort subsequent rows. Head compile failures drop a cell with the original
diagnostic; base cell compile failures remain `compileError` results. Unknown
effect shapes stop before execution. Neither script runs effects before named
confirmation; confirmation is a caller assertion of permission already obtained.

Output: `{"cells": {"<id>": {...}}, "provenance": {...}}`. Each cell has `call`,
`params`, `imports`, `setup`, `effect`, `function`, `rows: [{key, values, bindings}]`, `results`, and
`driverSource: {head, base}`. Results map row keys to `{head: {kind, render},
base: {kind, render}, differs}`. Kinds: `value`, `throwable`, `timeout`,
`compileError`. Drivers inline Product rendering/JSON without added libraries.
The grid retains driver source for validation; the page builder omits it from
the reader-facing page.
With `--output`, the full grid is written to the file and stdout is one JSON
line with `cells`, `rows`, `differingRows`, and `droppedCells` counts. Without
`--output`, stdout contains the full grid for existing callers and tests.
Map/set rendering sorts entries for stable comparisons. String values render
quoted to distinguish them from numbers. Unambiguous rows of integers and
strings use pipe-separated keys such as `3|dGVzdA==`. Ambiguous or compound
values use a canonical JSON tuple;
the browser selects through `rows`, so no lossy delimiter parsing is involved.

Pure result caches hash cellId, normalized snippet, revision, setup, imports,
grid, and generated harness. Normalization preserves string literals. Effects
always execute after fresh named confirmation; they never silently use a cache.
`provenance` carries preflight plus `droppedCells` and confirmed `effects`.

## Page builder

`build-page.py --grid <json> --narrative <json> --slug <slug>
[--output <path>] [--scaffold <path>] [--preflight <json>]`

Default output: `~/explanations/<today>-explanation-<slug>.html`.
The builder prints one JSON summary line with `ok`, `path`, `cells`, and
`quizQuestions`; it does not print the embedded preflight report.
`--scaffold` is a development override; installation uses this skill's bundled
`references/html-scaffold.html` and fails with its full expected path when absent.
The narrative is `{title, subtitle?, background, intuition, code, explanations?}`.
`explanations` is a map of recorded cell ids to one or two sentences explaining
why head differs. The builder appends the text to every quiz option's feedback
for that cell and rejects unrecorded cell ids. `background`, `intuition`, and
`code` are inner HTML fragments of sections the builder creates. Start at
`<h3>` or `<p>`; do not include `<section>`, `<h2>`, or ids already used by the
scaffold.
Anchor every recorded cell exactly where its explanation belongs:

```html
<div class="scala-cell" data-cell="decode-retry"></div>
```

The builder embeds one safe application/json block per cell, appends console.html,
renders provenance/diagnostics and any head-only/effect banners, and derives five
quiz answers from runs. It prefers changed rows, uses at most two questions per
cell while other cells have candidates, and uses other observed results as
distractors. At least five recorded rows are required, including in no-diff
mode. Quiz evidence identifies the exact cell, row, revision, and recorded output.
The scaffold's shuffled options/feedback remain together. Missing anchors,
duplicate ids, resource tags, network APIs, and invalid quiz data fail validation.
Inspect authored HTML too: the deterministic checker does not prove arbitrary
inline JS is network-free. Never insert arbitrary scripts into narrative fragments.

The builder embeds exactly one `page-provenance` application/json block with
`pageVersion: 2`, project path, module, head sha and working-tree hash, optional
base sha, Scala version, and CLI command. `--preflight` records the absolute
path of the report used to create the grid; otherwise that field is null.
There is no kernel script path or live fragment. Validation runs once on the
finished document, including provenance. The whole file must work offline.
