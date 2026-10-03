# Script contracts

All scripts print JSON to stdout, diagnostics as data, and return nonzero for
contract failures. `preflight.sh`, `probe-types.py`, and `run-grid.py` implement
the handoff's v1 contracts; optional fields extend them without replacing fields.
`scala_diff.py` shares codegen and contracts, and `preflight.py` is the shell
launcher's stdlib implementation. No jq or Python packages are required.

## Preflight

`preflight.sh <project-dir> [--module <sbt-project-id>] [--base <revision>]
[--head <revision>] [--temp-dir <parent>] [--cli-version <release>]`

The module is the sbt project id, not its published artifact name. An ambiguous
multi-module build fails with the module list. Versions/classpaths are resolved
independently for head and base. sbt uses `-error --client` and an explicit command
sequence: `print <module>/scalaVersion; export <module>/Runtime/fullClasspath`.
The exported fullClasspath also compiles the project. Logs and diagnostics are
retained verbatim. Compile diagnostics suggesting missing symbols include a
separate module/classpath hint; never replace or paraphrase the diagnostic.

Preflight makes a private shared control clone in a system temporary directory,
checks out the head commit there to give JGit a worktree and index, then attaches
detached worktrees to it. Both sbt builds run in the detached worktrees. Nothing is
created in the original target repository, including worktree metadata. Omitted
`--head` snapshots tracked changes and non-ignored untracked files, adding a
`workingTreeHash` to provenance. Untracked symlinks require selecting a committed
revision. Retain the temporary directory while probing/running grids; its
classpath contains compiled directories. Afterwards it is disposable.

Report fields: `ok`, `projectDir`, `module`, `modules`, `scalaVersion`,
`catsEffect: {present, major, version}`, `head`, optional/null `base`,
`toolchain: {scalaCli, sbtClient}`, `warnings`, `tempDir`, `cacheDir`.
Each revision includes `sha`, `worktree`, `classpathFile` on success, `builds`,
`scalaVersion`, `catsEffect`, `fs2: {present, version}`, `buildLog`, and
`diagnostic` on failure.
Top-level Scala/CE versions describe head. Mixed suffixes produce warnings.
`toolchain.command` records the exact CLI selection used by both drivers.
An explicit `--cli-version` opts into Scala CLI's separate-release launcher;
it may download that version but never changes the installed default. CLI 1.5.0
supports Scala up to 3.5.0; Scala 3.7.4 needs CLI 1.10.0 or newer. See the
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

For wrappers, opaque types, shallow case classes, or explicit constructors, a
value may be `{"scala":"demo.Id(\"abc\")","label":"abc"}`. The Scala expression
is compiled directly, not reflected upon. Survey explicit constructions for the
depth-2/8-field caps. Prefer raw primitive parameters plus visible construction
when a validating constructor can reject an input. Function inputs, handles,
resources, and abstract implementations are outside the survey's admissible set.
Never infer admissibility from a signature: the successful probe decides it.

## Probe and grid

`probe-types.py --preflight <json> --cell <cell.json>
[--confirm-effect <fully-qualified-function>]`

The probe compiles and runs one default call per revision. Its `admissible` field
describes head. Domain Left/None are values; thrown or timed-out defaults reject
the cell. Returns `cellId`, `admissible`, `diagnostic`, `params` (resolved), `cell`
(complete resolved spec), and `revisions` (individual verdicts and diagnostics).
A rejected base probe is preserved independently; it does not reject head.
Keep failed probes in a JSON array of `{cellId, diagnostic}` for `--dropped`.

`run-grid.py --preflight <json> --cells <cells.json> [--dropped <json>]
[--confirm-effect <fully-qualified-function>]` (repeat confirmations as needed).

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
Map/set rendering sorts entries for stable comparisons. String values render
quoted to distinguish them from numbers. Standard unambiguous keys retain the
handoff's `3|dGVzdA==` format. Ambiguous/compound values use a canonical JSON tuple;
the browser selects through `rows`, so no lossy delimiter parsing is involved.

Pure result caches hash cellId, normalized snippet, revision, setup, imports,
grid, and generated harness. Normalization preserves string literals. Effects
always execute after fresh named confirmation; they never silently use a cache.
`provenance` carries preflight plus `droppedCells` and confirmed `effects`.

## Page builder

`build-page.py --grid <json> --narrative <json> --slug <slug>
[--output <path>] [--scaffold <path>]`

Default output: `~/explanations/<today>-explanation-<slug>.html`.
`--scaffold` is a development override; installation uses this skill's bundled
`references/html-scaffold.html` and fails with its full expected path when absent.
The narrative is `{title, subtitle?, background, intuition, code}`. The last
three fields are authored HTML fragments; use the parent's section structure.
Anchor every recorded cell exactly where its explanation belongs:

```html
<div class="scala-cell" data-cell="decode-retry"></div>
```

The builder embeds one safe application/json block per cell, appends console.html,
renders provenance/diagnostics and any head-only/effect banners, and derives five
quiz answers from runs. It prefers changed rows and uses other observed results
as distractors. At least five recorded rows are required, including in no-diff
mode. Quiz evidence identifies the exact cell, row, revision, and recorded output.
The scaffold's shuffled options/feedback remain together. Missing anchors,
duplicate ids, resource tags, network APIs, and invalid quiz data fail validation.
Inspect authored HTML too: the deterministic checker does not prove arbitrary
inline JS is network-free. Never insert arbitrary scripts into narrative fragments.

The builder validates the offline page before appending `references/live.html`.
It also embeds `live-provenance` with the module, shas, working-tree hash,
Scala version, project path, and CLI command. The fragment is inert from
`file://`: it makes no requests and shows the kernel command inside each cell.
Recorded rows and quiz evidence remain unchanged by live runs.

## Live kernel

When the skill starts live mode, use `start-kernel.py` with the same options
as `kernel.py`. It launches a detached kernel, waits for the startup JSON,
prints that JSON with a `launcherLog` path, and exits. Give the user the
printed URL and PID. `kill -TERM <pid>` stops that detached kernel. The
launcher keeps its startup stdout and stderr in a private temporary folder;
the kernel also writes API requests to its own `log` path. A person who runs
`kernel.py` in a foreground terminal can stop it with Ctrl-C.

`kernel.py --page <html> [--preflight <json>] [--allow-effects]
[--idle-minutes 30] [--max-timeout-seconds 60] [--port 0]
[--no-bloop] [--temp-dir <parent>]`

Run it only when the reader asks for live mode. It prints one JSON line with
`ok`, a loopback URL containing a fragment token, process id, temporary and log
paths, effect mode, idle limit, and available revision shas. Open that URL.
SIGTERM stops a detached kernel; it also exits after 30 idle minutes by
default. Bloop may remain after exit; `scala-cli bloop exit` stops it manually.

With `--preflight`, the kernel verifies page shas and existing classpaths.
Without it, or if the temporary worktrees are gone, it rebuilds the same
committed revisions via preflight and tells the terminal that sbt is running.
A page made from uncommitted working-tree changes requires its original
preflight files. A base rebuild failure leaves a head-only kernel. Head failure
stops startup. `--no-bloop` uses Scala CLI's `--server=false` path.

The kernel serves the page only on `127.0.0.1` and checks Host, Origin, and a
256-bit bearer token on every `/api/*` route. The browser reads the token from
the printed URL fragment into tab session storage and clears the fragment.
The page GET is unauthenticated. Responses disable caching and use a restrictive
CSP. Source runs with the local user's privileges. Scala CLI directives in
editor text are rejected. Effects are refused unless the process was started
with `--allow-effects`; supported types are IO, Resource[IO, A], and
fs2.Stream[IO, A]. This gate applies when the returned expression has one of
those types. An edited Scala block can also perform direct side effects while
building a value; the kernel does not sandbox reader code.

| Route | Request | Response |
| --- | --- | --- |
| `GET /api/status` | bearer token | Page hash, revisions, effect mode, queue and warm state |
| `POST /api/runs` | `{cellId, source, timeoutSeconds?}` | 202 `{runId}`; 400 invalid source, 413 oversized body, 429 full queue |
| `GET /api/runs/{id}?wait=25` | bearer token | Run state and each revision's phase, kind, render, output, diagnostic, editor lines, duration and driver source |
| `POST /api/runs/{id}/cancel` | `{}` | `{ok}`; kills an active compiler or JVM process group |

Runs are serial; the queue holds four. Each press compiles and runs head,
then base, with a fresh JVM per revision. Results appear as each revision
finishes. The default run timeout is 5 seconds; the UI offers 5, 15, and 60
seconds, bounded by `--max-timeout-seconds`. Compilation has its own 300-second
limit. Runtime stdout and stderr are capped at 64 KB. Compile errors retain
verbatim diagnostics; `lines` separately maps compiler locations to editor
lines. `kind` is one of `value`, `throwable`, `timeout`, `compileError`,
`refused`, or `cancelled`. Live results never replace baked rows or quiz data.

The agent never calls `/api/*` during an explanation or uses live output as
recorded evidence. Automated kernel tests and fixture validation exercise the
API separately.
