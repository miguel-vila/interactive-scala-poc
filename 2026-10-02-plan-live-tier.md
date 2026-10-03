# Plan — live tier for `explain-scala-diff-html` (phase 2)

Date: 2026-10-02
Status: planned, not started. Supersedes the "phase 2" note in handoff §2.2.
Scope: the reader edits a Scala snippet in a text box on the generated page and
runs it against the base and head classpaths, with the results side by side.

---

## 1. What this adds

v1 bakes a finite grid: widgets select a recorded row. The live tier adds, inside
each console cell, an editor prefilled with the current widget selection. The
reader changes anything (inputs, the call, setup, imports), presses Run, and sees
the real result from both revisions. A local **kernel** process compiles and runs
the snippet. Without a kernel the page is the v1 page.

User loop:

1. Open the page from disk. Everything works offline as today. Each cell shows
   "Edit and run" with the kernel command.
2. Run `python3 <skill>/scripts/kernel.py --page <page.html>`. It prints one URL
   that carries a token.
3. Open that URL. The same page now has working editors. Edit, Cmd+Enter, read.
4. Ctrl-C the kernel, or let it exit after 30 idle minutes.

---

## 2. Decisions

### 2.1 Additive; the page stays offline-complete

- The baked grid stays the verified truth and the only quiz source. Live results
  appear next to it, labelled live, and are never written into the page.
- The live fragment is appended to every page; there is no `--live` flag. A page
  reaches reviewers through PRs, and anyone with the repo and toolchain can go
  live from any page. Cost: a small fragment and a change to the network-API
  check (§2.8).
- From `file://` the fragment makes zero requests. It activates only when the
  page is served by a kernel.

### 2.2 Executor: stdlib HTTP server; each run is `scala-cli compile` then `java`

Rejected: an in-process compiler host (REPL APIs differ per Scala version, and a
runaway thread cannot be killed; the handoff already records this for pure
calls). Rejected: piping a `scala-cli repl` (fragile output parsing, and shared
state contradicts handoff §2.4).

Chosen:

- `scala-cli compile --jvm system --scala <v> --extra-jars <cp> -d out Live.scala`,
  then `java -cp out:<cp> ScalaDiffLive <result.json>`. The split gives exact
  classification: compile exit code ≠ 0 → `compileError`; run outcomes →
  `value` / `throwable` / `timeout`. No string heuristics. (v1's `execute_driver`
  classifies from stderr text; adopting the split there is a separate follow-up.)
- Bloop on (the scala-cli default) for warm compiles. `--no-bloop` passes
  `--server=false`. One workspace per revision (`<tempDir>/live/head`,
  `<tempDir>/live/base`) so Bloop's project identity is stable.
- Measured on the fixture (Scala 3.5.0, 10 classpath entries, Bloop already up):

| Strategy | first run | repeat | after an edit |
| --- | --- | --- | --- |
| `scala-cli run --server=false` (v1) | 2.3 s | 1.9 s | — |
| `scala-cli run`, Bloop | 1.2 s | 0.7 s | 0.7 s |
| `scala-cli compile -d out` + `java -cp` | — | — | 0.5 s + 0.2 s |

  A cold Bloop start adds about 3 s once. Re-measure on a playback module in
  step 0 of §6; with hundreds of jars the cold path is expected to be several
  times slower and the warm path less so. Both are usable; Bloop is chosen for
  interactivity.
- The kernel warms both revisions at start by compiling a trivial driver in the
  background. `/api/status` reports `warm` per revision.
- The kernel captures the run process's stdout and stderr (capped at 64 KB) and
  returns it as `output`, so `println` debugging works in the editor.

### 2.3 Security model

Threat addressed: another website, another local process, or an agent makes the
kernel run code. Not addressed: the reader's own code. It runs with the reader's
privileges, exactly like `sbt console`, and the page says so.

- Bind `127.0.0.1` on an ephemeral port. No option for other interfaces.
- A 256-bit token (`secrets.token_urlsafe`) per start, printed once in the start
  line. Every `/api/*` request needs `Authorization: Bearer <token>`, compared
  with `hmac.compare_digest`. Wrong or missing → 401 with no detail.
- The kernel serves the page itself at `/<basename>`, so API calls are
  same-origin: no CORS, no `Access-Control-Allow-Origin: null`, no
  private-network-access rules. Only that path is served; everything else is
  404. The page GET is unauthenticated because a browser cannot send a URL
  fragment; it exposes a file the local user can already read. The file is read
  once at start; a rebuilt page needs a kernel restart.
- Token transport: URL fragment `#k=<token>`. On load the page moves it to
  `sessionStorage` (origin `http://127.0.0.1:<port>`, per tab) and clears the
  hash. TOC anchors therefore cannot destroy it, and it never travels in a URL.
  A new tab needs the printed URL again.
- Request checks: `Host` must be `127.0.0.1:<port>` or `localhost:<port>`, else
  400 (DNS rebinding). `Origin`, when present, must equal the kernel origin, else
  403. POST bodies must be `application/json` and at most 64 KB, else 415 / 413.
- Response headers: `Cache-Control: no-store`, `X-Content-Type-Options:
  nosniff`, `Referrer-Policy: no-referrer`, and a CSP with `default-src 'none'`,
  inline script and style allowed, `connect-src 'self'`, `img-src data:`,
  `frame-ancestors 'none'`.
- Explicit start: nothing in the page build starts a kernel. The skill starts
  one only when the user asks for live mode (§2.10).
- Idle timeout: exit after `--idle-minutes` (default 30) without API requests.
  SIGINT and SIGTERM kill in-flight process groups, then exit.
- Every API request is logged to stderr: time, path, cellId, outcome, duration.
  When started in the background the log goes to `<tempDir>/kernel.log` and the
  path is printed in the start line.
- v1 source rules apply to editor text: no `//> using` directives; only the
  exported project classpath.

### 2.4 Each run is a fresh process; cancel is a kill

- Reuse v1's `command()` (new session; SIGKILL the process group on timeout) for
  both the compile and the run.
- Per-run `timeoutSeconds`: default 5. The UI offers 5, 15 and 60; the kernel
  caps at `--max-timeout-seconds` (default 60). v1 caps at 5 s because grids
  multiply; a live run is one call. The compile step has its own 300 s bound.
- One worker thread; runs are serialised; queue depth 4; beyond that 429.
  Cancel works on queued and in-flight runs. In flight → kill the process group
  → kind `cancelled`.
- Head and base run in sequence per press. Each revision's result is delivered
  as soon as it is ready.

### 2.5 Effects: detected by the compiler, allowed by a start flag

v1 needs an `effect` field because the agent chooses it. In the editor the reader
types any expression, so the driver detects the effect from the static type with
a type class. This works in Scala 2.12, 2.13 and 3:

```scala
trait Runner[T] { def apply(t: => T): Any }
trait LowPriorityRunner { implicit def pure[T]: Runner[T] = ... }   // value as is
object Runner extends LowPriorityRunner {
  implicit def io[A]: Runner[IO[A]] = ...                           // .timeout(t).unsafeRunSync()
  implicit def resource[A]: Runner[Resource[IO, A]] = ...           // .use(IO.pure)
  implicit def stream[A]: Runner[fs2.Stream[IO, A]] = ...           // .take(n).compile.toList
}
def run[T](t: => T)(implicit r: Runner[T]): Any = r(t)
```

- `io` and `resource` are emitted only when preflight detected cats-effect (CE2
  gets ContextShift/Timer implicits as in v1). `stream` is emitted only when
  `fs2-core` is on the classpath. Preflight gains an `fs2: {present, version}`
  field per revision; the kernel computes it from the classpath file when the
  field is absent, so existing preflight JSON stays valid.
- `--allow-effects` off (default): the same instances exist but **refuse**. They
  return kind `refused` with the exact message "Effects are disabled in this
  kernel. Restart it with --allow-effects to run cats.effect.IO." The by-name
  argument is never forced, so the effect is neither built nor run. On: they
  run with `.timeout` and `unsafeRunSync()` in the driver, never in the editor
  text.
- This keeps the v1 invariant: no effect runs unless a human opted in. The
  opt-in moves from "name the function" (meaningless for free text) to "start
  the kernel with the flag". The page shows which mode the kernel is in.
- Overridable: a browser confirmation dialog instead of the flag. Rejected
  because an agent holding the token could call the API without any dialog; the
  flag is visible in the terminal and in shell history.

### 2.6 Editor and results

- A plain `<textarea>`: monospace, `spellcheck=false`, Tab inserts two spaces,
  Cmd/Ctrl+Enter runs, auto-grows. No editor library (self-containment and page
  size).
- Prefill is the baked cell's current bindings, setup and call, which v1 already
  renders in the call `<pre>`. Buttons: Run, Cancel, Reset, "Load selection"
  (copies the current widget selection into the editor), and a timeout select.
  After the first prefill the reader owns the text.
- Imports and setup live inside the block the reader edits. The driver wraps the
  text as `run { <text> }` inside the v1 harness.
- Results use the same two-column base/head layout: a `live` chip, duration,
  kind chip, differs status, run output, and driver source on demand. Compile
  diagnostics are shown **verbatim**. The kernel adds `lines` (editor line
  numbers derived from the known offset of the snippet in the driver) as
  separate data, and the UI highlights them. Compiler text is never rewritten.
- Live results and baked results never overwrite each other.

### 2.7 The page describes its environment

The builder embeds `<script type="application/json" id="live-provenance">` with
`projectDir`, `module`, `head.sha`, `head.workingTreeHash`, `base.sha` or null,
`scalaVersion`, `toolchain.command` and `pageVersion`. The kernel reads it and:

- with `--preflight <json>`: checks that the temp dir and classpath files exist
  and that the shas match the page; a mismatch refuses with both sha pairs;
- without it, or when the temp dir is gone: re-runs preflight for the same shas
  and module by importing `preflight.py`, and tells the user why sbt is running.
  A page built from a working-tree snapshot (`workingTreeHash` set) cannot be
  rebuilt from shas; the kernel refuses with that explanation.

### 2.8 Validation rule change

Today `validate_page` rejects any `fetch(` in the whole page. New rule: validate
the page **before** appending the bundled `live.html`, exactly as today, so the
narrative and scaffold still cannot carry network code; then append `live.html`.
The fragment is covered by its own tests: every `fetch(` targets a relative
`/api/...` path, there are no `http`, `https` or `ws` literals, and activation is
gated on `location.protocol === "http:"` plus a stored token. The Playwright
check keeps asserting zero requests from `file://`.

### 2.9 Scratch cell (optional, last)

When a kernel is connected, one extra cell at the end of the page with an empty
editor: "try anything against both revisions". Same component, `cellId`
`scratch`. Build it only after §6 steps 1–4 are green.

### 2.10 What the agent does and must not do

- Page build: unchanged. The builder emits the provenance block and the fragment
  on its own.
- When the user asks for live mode, start the kernel with the session's
  preflight JSON (this skips the sbt rebuild), report the printed URL, and say
  how to stop it. Never start it as part of "explain this diff".
- Never call `/api/*`. The kernel is for the reader's browser. Any run the agent
  needs goes through `run-grid.py`, where the named effect gate applies. The
  request log makes agent calls visible.
- The agent never pastes live results into the narrative as recorded facts.

---

## 3. File layout (additions)

```
skills/explain-scala-diff-html/
  scripts/kernel.py            # loopback server, run queue, page serving (stdlib only)
  scripts/scala_diff.py        # + live_driver_source, compile_driver, run_compiled, fs2 detection
  scripts/build-page.py        # + live-provenance block; append live.html after validation
  scripts/preflight.py         # + fs2: {present, version} per revision
  references/live.html         # editor CSS + JS, appended after console.html
  references/contracts.md      # + kernel CLI and HTTP contracts
  tests/test_kernel.py         # fake toolchain on PATH, real server on an ephemeral port
  tests/fixtures/bin/          # fake scala-cli and java used by test_kernel.py
  SKILL.md                     # new "Live mode" section; drop "No loopback kernel belongs in v1"
```

---

## 4. Contracts

### `kernel.py`

```
kernel.py --page <html> [--preflight <json>] [--allow-effects] [--idle-minutes 30]
          [--max-timeout-seconds 60] [--port 0] [--no-bloop] [--temp-dir <parent>]
```

Prints one JSON line to stdout, then serves:

```json
{"ok": true, "url": "http://127.0.0.1:PORT/<basename>#k=<token>", "pid": 123,
 "tempDir": "...", "log": "...", "effectsAllowed": false, "idleMinutes": 30,
 "revisions": {"head": {"sha": "..."}, "base": {"sha": "..."}}}
```

Failures print `{"ok": false, "diagnostic": "..."}` and exit 1: page/preflight
sha mismatch, missing tool, head rebuild failure. A base rebuild failure gives
head-only live mode with a warning, as in v1.

### HTTP (JSON; every `/api/*` route needs the bearer token)

| Method and path | Body | Response |
| --- | --- | --- |
| `GET /api/status` | — | `{ok, page: {basename, sha256}, revisions: {head: {sha, scalaVersion, catsEffect, fs2}, base or null}, effectsAllowed, defaultTimeoutSeconds, maxTimeoutSeconds, busy, queued, warm: {head, base}}` |
| `POST /api/runs` | `{cellId, source, timeoutSeconds?}` | 202 `{runId}`; 400 bad cellId, directives, or timeout; 413 over 64 KB; 429 queue full |
| `GET /api/runs/{id}?wait=25` | — | `{runId, state, revisions: {head: {state, kind?, render?, output?, diagnostic?, lines?, durationMs?, driverSource?}, base?}}`; returns on change or after `wait` seconds (max 30) |
| `POST /api/runs/{id}/cancel` | — | `{ok}` |
| `GET /<basename>` | — | the page, `text/html; charset=utf-8`, no auth |
| anything else | | 404 or 405 |

Run states: `queued`, `compiling`, `running`, `done`, `cancelled`.
Result kinds: `value`, `throwable`, `timeout`, `compileError`, `refused`, `cancelled`.

### `live-provenance` block (emitted by `build-page.py`)

```json
{"pageVersion": 1, "projectDir": "...", "module": "core",
 "head": {"sha": "...", "workingTreeHash": null}, "base": {"sha": "..."},
 "scalaVersion": "3.5.0", "toolchain": {"command": ["scala-cli"]}}
```

### Live driver (generated per run and revision)

```scala
object ScalaDiffLive {
  // v1 HARNESS: quote, render, execute with TIMEOUT
  // Runner instances per §2.5, refusing or running depending on --allow-effects
  def main(args: Array[String]): Unit = {
    val (kind, result) = execute(() => run {
      // editor text starts at a known line L; `lines` in results = compiler line - L + 1
    })
    // write {"kind","render"} to args(0)
  }
}
```

---

## 5. Page contract for `live.html`

States per cell:

- `offline`: `file://` or no stored token. A collapsed details block with the
  kernel command. Zero requests.
- `connecting`: status request in flight.
- `ready`: editor, Run, timeout select, revision chips (sha, Scala, cats-effect),
  and the effects mode.
- `busy`: Run becomes Cancel; per-revision phase text ("compiling head",
  "running base").
- `done`: results as in §2.6.
- `kernel-gone`: a request failed after `ready`. Banner with the restart
  command; the baked console keeps working.
- `unauthorized`: 401. Banner: open the URL the kernel printed.

The v1 HTML rules hold: code in `<pre>` or pre-wrapped elements, wide content in
`overflow-x: auto` containers, no external resources, scaffold tokens only.

---

## 6. Implementation order (TDD; each step lands with its tests green)

0. Re-measure §2.2 on one playback module, head only: `scala-cli compile` cold,
   Bloop warm, three edits. Record the numbers in §2.2. Keep Bloop as the default
   unless the data says otherwise.
1. `scala_diff.py`: `live_driver_source(snippet, revision, effects_allowed,
   timeout)` with the Runner instances and the snippet line offset;
   `compile_driver` and `run_compiled` with exact classification; fs2 detection.
   Unit tests: instances present or absent per classpath, refused versus running,
   CE2 injection, line offset, classification from exit codes with fake
   processes.
2. `kernel.py`: server, auth and header checks, page serving, run queue,
   long-poll, cancel, idle exit, provenance checks, warm-up, logging. Tests use
   fake `scala-cli` and `java` scripts on PATH and a real server on an ephemeral
   port through `urllib`: wrong token 401, wrong Host 400, foreign Origin 403,
   cancel kills the fake process group, idle exit, sha mismatch refusal.
3. Page: `live.html`, the provenance block in the builder, validate-then-append.
   Tests: block present, narrative `fetch(` still rejected, fragment appended
   once, fragment URL audit.
4. Browser and fixture: extend `browser-check.cjs` (`file://`: zero requests and
   the offline text; kernel URL: edit, run, two columns, compile error with
   editor line, 401 banner) and `validate_fixture.py --live` (run, cancel,
   timeout, refused, and `--allow-effects` through `urllib`). Update `SKILL.md`,
   `contracts.md`, `tests/README.md` and `README.md`.
5. Scratch cell (§2.9).

Do not change v1's `run-grid.py` execution path in this phase.

---

## 7. Acceptance criteria

- The v1 criteria hold on every page from `file://`, with zero network requests.
- From a built page: start the kernel, open the URL, edit a snippet, Cmd+Enter,
  and both base and head results appear. On the fixture the warm edit-run cycle
  is under 2 s per revision. Numbers for a playback module are recorded in §2.2.
- Without the token, or from another origin, every `/api/*` call is rejected.
  The kernel exits after the idle period. Cancel ends the JVM within one second.
- Effects run only when the kernel was started with `--allow-effects`. Otherwise
  the result is `refused` with the restart command.
- Compile diagnostics are verbatim, with editor line numbers alongside.
- Page content, quiz and provenance are unchanged by live runs.
- No new dependencies: Python stdlib, git, sbt thin client, Java, scala-cli.

---

## 8. Open items

- Latency on a real module (step 0). If a warm compile there exceeds about 5 s,
  revisit an in-process compiler host for Scala 3 only.
- `--allow-effects` as a start flag versus an in-terminal "press y" prompt. The
  prompt is a true out-of-band confirmation, but it needs a TTY and conflicts
  with background start. The flag is chosen; revisit if restarts become
  annoying.
- The Bloop server stays after the kernel exits (scala-cli default;
  `scala-cli bloop exit` stops it). Document; do not kill it.
- Showing the static type of the result (for example `Option[A]` becoming
  `Either[E, A]`) would help in a diff. It needs a macro or `-Xprint` parsing.
  Not planned.

---

## 9. Non-goals

Shared REPL state across cells or runs; an in-process compiler; editor
libraries; non-loopback access, HTTPS or multiple users; sandboxing the reader's
code; persisting live results into the page or quiz.
