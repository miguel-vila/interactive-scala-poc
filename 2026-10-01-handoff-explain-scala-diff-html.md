# Handoff — `explain-scala-diff-html` skill, v1 plan

Date: 2026-10-01
Author of decisions: Miguel Vilá González (all design calls below are ratified by him)
Next session focus: **implement v1 of the skill**

---

## 1. What this is

A new skill that produces a single self-contained HTML page explaining a Scala
code change, with **embedded interactive consoles**: each console shows a
prefilled call to a function from the diff, parameter widgets, and the real
result of running that call — rendered for both the base and the head revision
side by side.

It is a fork of the existing `explain-diff-html` skill, extended with Scala
execution machinery.

Reference artifacts (read these, do not re-derive):

| Thing | Path |
| --- | --- |
| Parent skill to fork | `/Users/miguelvilagonzalez/repos/SKILLS/skills/explain-diff-html/SKILL.md` |
| Shared HTML scaffold (**do not fork**) | `/Users/miguelvilagonzalez/repos/SKILLS/skills/explain-diff-html/references/html-scaffold.html` |
| sbt conventions for target projects | `/Users/miguelvilagonzalez/.claude/plugins/marketplaces/lynn-skills/plugins/playback-dev/skills/executing-sbt-commands/SKILL.md` |
| Skills repo (git, branch `main`) | `/Users/miguelvilagonzalez/repos/SKILLS` |
| Empty POC sandbox | `/Users/miguelvilagonzalez/repos/interactive-scala-poc` (empty, not a git repo) |

---

## 2. Decisions already made — do not reopen

### 2.1 Fork, but share the scaffold

- Fork `SKILL.md`. It is 143 lines and **is meant to diverge**: new step 0
  (toolchain preflight), step 2 gains a constructability survey, step 4 gains a
  console section, step 8 gains console checks.
- **Do not fork `references/html-scaffold.html`.** Tokens, layout, TOC,
  callouts and the option-shuffling quiz JS keep improving; two copies drift
  silently. The fork's step 0 reads the sibling scaffold by path, appends its
  own `references/console.html`, and fails loudly with the expected path when
  the sibling is missing.
- The value of the fork is `scripts/`, not the prose. Classpath resolution,
  worktree setup, driver codegen and batch execution are deterministic and too
  long to re-derive from instructions on each run.

### 2.2 Execution model: baked tier only in v1

Two tiers were designed. **v1 ships only the first.**

| Tier | Needs | Input surface | In v1? |
| --- | --- | --- | --- |
| Baked | nothing | parameter widgets over a precomputed grid | **yes** |
| Live | loopback kernel | free-form editable snippet | no — phase 2 |

Rationale: the baked tier already delivers the described experience (change a
parameter, see the output, see before next to after), works offline, and
survives being attached to a PR. The kernel carries the whole security surface
(a loopback server that compiles and runs arbitrary Scala with the project
classpath is an RCE surface: loopback bind + token + explicit start + idle
timeout) and most of the engineering. Build it only once a real page makes the
grid feel too small.

Consequence for v1: the page makes **no network requests at all**, so the
parent skill's self-containment rules hold unchanged.

### 2.3 Scala runs via generated drivers compiled by scala-cli

Rejected alternatives and why:

- **Scala.js** — needs the whole transitive dependency graph published for JS
  (false for backend projects) and needs `crossProject` edits to the target
  build. Too intrusive.
- **Runtime reflection** — cannot synthesize implicits or givens, fights name
  mangling (`Foo$.MODULE$`, `f$default$1`), flattens parameter lists
  unpredictably. A large share of real signatures are out of reach.

Chosen: generate a small Scala source that calls the function the way a human
would, and let the compiler resolve implicits, givens, default args and
overloads.

```
sbt -error --client "export <module>/Runtime/fullClasspath"   # once, cached
  ↓  generated Batch.scala
scala-cli run --scala <projectScalaVersion> --extra-jars <cp> Batch.scala
```

**One compile, N invocations.** The batch driver holds the whole grid and loops
over it; never one compile per row. A 60-row grid is one compile plus 60 fast
calls.

### 2.4 Cells are independent

Multiple consoles per page, anchored to narrative sections. **No shared REPL
session between cells.** Shared state would make a result depend on execution
history, break a reader who jumps to section 4, and destroy the cache key. Each
cell compiles one self-contained driver. A cell needing setup inlines that setup
in a collapsed `setup` region the reader can expand.

Cache key: `hash(cellId, normalizedSnippet, revision)`. Normalize whitespace and
comments.

### 2.5 Admissible inputs: literals and newtypes, judged by the compiler

In decreasing confidence:

- primitives and `String`
- enums / sealed traits whose children are all case objects — these auto-expand
  to a full dropdown and are the **highest-value input** for a diff demo
- single-field wrappers: `case class Id(value: String)`, `AnyVal`,
  `opaque type` with a public `apply` or `from`
- `Option[T]` for admissible `T` → `None` plus two `Some`
- `List` / `Set` / `Vector` / `Map` of admissible → empty, one, many
- case classes whose every field is admissible — depth cap 2, field cap 8
- `java.time` from literal strings

Out: function parameters, abstract types, traits with non-enumerable
implementations, anything needing a handle or resource.

**Admissibility is decided by compiling, never by reasoning about the
signature.** Generate one driver that calls the function once with a single
default per parameter; compile and run it. Compiles and runs → admissible. Fails
→ the cell is dropped and the compiler diagnostic is recorded verbatim. A model
reading a signature is confident and wrong about implicit scope, variance and
smart constructors.

For newtypes with validating constructors, the **raw** value is the parameter and
the construction stays inside the visible snippet, so validation becomes part of
what the page explains:

```scala
val raw = "not-a-uuid"
TrackId.from(raw).map(Catalog.lookup)   // Left(InvalidTrackId) is a result, not a failure
```

### 2.6 Effects: cats-effect `IO` is the default

- **Detect the CE version, do not assume it.** CE2 `unsafeRunSync()` needs
  implicit `ContextShift`/`Timer`; CE3 needs
  `cats.effect.unsafe.implicits.global`. One grep of the exported classpath for
  `cats-effect_*-3.` settles it and converts a baffling compile error into a
  preflight line.
- **`unsafeRunSync()` lives in the driver, never in the snippet the reader
  sees.** The cell shows the call as it appears in the repo; the page states
  once, in a callout, that effects are run with `unsafeRunSync` and a timeout.
  Otherwise the explanation teaches a call shape nobody writes.
  *(This is a deliberate, scoped exception to the `CLAUDE.md` rule against
  `unsafe` variants: it applies to generated harness code at the edge, not to
  project code or to the skill's own Scala.)*
- **Bound every run**: `.timeout(5.seconds)` in the driver; a timeout renders as
  a result value. One blocking cell must not take the grid down.
- **Three supported effect shapes**, everything else flagged rather than
  guessed: `IO[A]`; `Resource[IO, A]` via `.use`; `fs2.Stream[IO, A]` via
  `.take(n).compile.toList`. `def f[F[_]: Async]` instantiates to `F = IO`,
  which is where this default pays off most.
- **Safety gate (sharp edge, must be implemented).** A grid run invokes the
  function tens of times, and `IO` can perform real I/O — an HTTP `PUT` or a DB
  write will happen 60 times against whatever the ambient config points at. No
  probe can distinguish that from a pure `IO`. So: the batch must **not** run an
  effectful function without explicit user confirmation that names the function,
  and the generated page must state which cells performed effects.

### 2.7 Version and dependency conflicts: prevented, then probed

- **Scala version** comes from `sbt --client "print <module>/scalaVersion"` and
  is passed to scala-cli verbatim, so mismatch stops being a failure mode.
  Target projects use `crossScalaVersions`, so this must be resolved **per
  module** for the module owning the changed file, and the chosen version must
  appear in the page footer — a page built against 2.13 when the service ships 3
  would mislead a reviewer.
- **The driver adds zero dependencies.** The `Product`-walking renderer and the
  JSON emitter are inlined in the generated source. Do **not** pull `pprint` or
  `circe`: the only classpath is the project's own, so we cannot introduce a
  conflict.
- **Mixed cross-version suffixes** in the exported classpath (`_2.13` jars next
  to `_3`) — one grep, one warning line.
- **Everything else is the compile probe**, three actionable outcomes: compiles
  and runs → proceed; symbol not found → wrong module or stale classpath, report
  with the module list; any other diagnostic → pass the compiler text through
  **verbatim** and stop. Never paraphrase compiler output.
- **The base revision may not build.** Probe the two worktrees independently. If
  base fails, degrade to a head-only page with a visible banner — never a silent
  single column, because a reader who thinks they are seeing before/after when
  they are not will draw the wrong conclusion.
- All of the above becomes one `preflight` report printed before any page is
  built, and then a short provenance footer in the page: both shas, module,
  Scala version, cats-effect version, which cells were dropped and the
  diagnostic for each. That footer is what makes the page credible in a PR.

### 2.8 The quiz becomes verified

The parent skill's quiz answers are model-authored, so they can be wrong in
exactly the subtle cases that matter. With a grid in hand, questions are
**derived from recorded runs**: pick rows where base and head differ, ask what
the new code returns, and take the `correct: true` option from the actual
output. This is the main substantive upgrade over the parent skill, independent
of the console.

---

## 3. Target file layout

```
/Users/miguelvilagonzalez/repos/SKILLS/skills/explain-scala-diff-html/
  SKILL.md                      # forked from explain-diff-html, diverges by design
  references/console.html       # console CSS + JS only (appended to the shared scaffold)
  scripts/preflight.sh          # sbt export, scala version, CE detection, worktrees
  scripts/probe-types.py        # generate + compile the constructability driver per cell
  scripts/run-grid.py           # batch driver codegen, compile, run, emit results JSON
```

Language split (proposed, not ratified — settle it in the first 10 minutes):
bash for toolchain probing, Python 3 stdlib only for codegen and orchestration
(no `jq` dependency), generated Scala for the drivers.

---

## 4. Script contracts

These JSON shapes are the contract between the prose and the machinery. Keep
them stable; the SKILL.md stays short because these are executables.

### `scripts/preflight.sh <project-dir> [--module <m>] [--base <sha>]`

```json
{ "ok": true,
  "projectDir": "/Users/.../playback-core",
  "module": "playback-core",
  "scalaVersion": "2.13.14",
  "catsEffect": { "present": true, "major": 3, "version": "3.5.4" },
  "head": { "sha": "abc1234", "classpathFile": "/tmp/.../cp-head.txt", "builds": true },
  "base": { "sha": "def5678", "worktree": "/tmp/.../base",
            "classpathFile": "/tmp/.../cp-base.txt", "builds": true },
  "toolchain": { "scalaCli": "1.5.0", "sbtClient": true },
  "warnings": ["mixed cross-version suffixes: foo_2.13-1.0.jar, bar_3-2.0.jar"] }
```

### Cell spec (authored by the model during the skill run)

```json
{ "cellId": "decode-retry",
  "module": "playback-core",
  "snippet": "Thing.decode(input, retries)",
  "effect": "IO",
  "params": [ { "name": "retries", "type": "Int", "values": [0, 1, 3], "default": 3 },
              { "name": "input", "type": "String", "values": ["dGVzdA==", "", "!!!"] } ] }
```

### `scripts/probe-types.py --preflight <json> --cell <cell.json>`

```json
{ "cellId": "decode-retry", "admissible": true, "diagnostic": null,
  "params": [ "...resolved, with enum cases expanded..." ] }
```

### `scripts/run-grid.py --preflight <json> --cells <cells.json>`

```json
{ "cells": { "decode-retry": {
      "results": { "3|dGVzdA==": { "head": { "kind": "value", "render": "Right(test)" },
                                   "base": { "kind": "value", "render": "Right(test)" },
                                   "differs": false },
                   "0|!!!":      { "head": { "kind": "value", "render": "Left(BadPadding)" },
                                   "base": { "kind": "throwable", "render": "IllegalArgumentException: ..." },
                                   "differs": true } },
      "driverSource": "<generated Scala, one per revision>" } },
  "provenance": { "...": "copied into the page footer" } }
```

Result `kind` is one of `value` | `throwable` | `timeout` | `compileError`.
**Throwables are captured as data and never kill the batch** — a cell showing
"this input now throws" is often exactly what a diff is about.

Grid budget: cap at **48 rows per cell** and **200 rows per page**; refuse and
report when a cartesian product exceeds it rather than silently truncating.

---

## 5. Page contract

The generated page embeds one declarative block per cell so the narrative stays
readable:

```html
<div class="scala-cell" data-cell="decode-retry"></div>
<script type="application/json" id="cell-decode-retry">
{ "call": "Thing.decode(input, retries)",
  "params": [ { "name": "retries", "type": "int", "values": [0,1,3], "default": 3 },
              { "name": "input", "type": "enum", "values": ["dGVzdA==", "", "!!!"] } ],
  "results": { "3|dGVzdA==": { "base": "Right(test)", "head": "Right(test)" },
               "0|!!!":      { "base": "Right(??)",   "head": "Left(BadPadding)" } } }
</script>
```

`references/console.html` holds the renderer. Requirements:

- No external requests of any kind (keeps the parent's section-8 greps passing).
- All code in `<pre>`, or `white-space: pre-wrap` on any styled `div`.
- Wide content scrolls in its own `overflow-x: auto` container.
- Each result shows the **generated driver source** on demand, so a reader sees
  the exact call that produced it and can paste it into their own REPL.
- Base and head columns plus a `differs` flag; single column with a visible
  banner when the base revision did not build.
- No-diff mode: revision set reduces to `{head}` and the console drops to one
  column. The skill name is therefore slightly narrow; accepted.

---

## 6. Implementation order

1. **Scaffolding + preflight.** Create the skill directory, fork `SKILL.md`,
   write `preflight.sh`. Verify against one real multi-module playback project:
   classpath exported, per-module `scalaVersion` resolved, CE version detected,
   base worktree created.
2. **Constructability probe.** `probe-types.py` for one hand-written cell spec.
   Prove that a non-admissible parameter is rejected with a verbatim compiler
   diagnostic.
3. **Batch grid.** `run-grid.py` — one compile, N rows, throwables and timeouts
   as data, both revisions. This is the core of v1.
4. **Console renderer.** `references/console.html` plus the append step in
   step 0 of the SKILL.md.
5. **Verified quiz.** Derive questions from differing rows.
6. **End-to-end on a real diff**, then tune the prose.

Do not start the loopback kernel. It is phase 2 and explicitly out of scope.

---

## 7. Acceptance criteria for v1

- Runs from a target sbt project directory with no edits to that project's
  build files, and creates nothing inside the target repo.
- Produces one HTML file in `~/explanations/` named
  `<YYYY-MM-DD>-explanation-<slug>.html`, opened with `open`, path reported to
  the user.
- The page works with no network and no running process: every widget
  combination resolves from the baked grid.
- At least one console cell shows base and head side by side, with the
  differing rows visibly marked.
- Provenance footer present: both shas, module, Scala version, cats-effect
  version, dropped cells with their diagnostics.
- An effectful function is never run without explicit confirmation naming it.
- The parent skill's section-8 checks still pass on the combined page.

---

## 8. Known open items

- **Quiz count inconsistency in the parent skill.** `SKILL.md:77` says *seven*
  questions; `SKILL.md:133`, `html-scaffold.html:7` and
  `html-scaffold.html:221` all say *five*. Pick one in the fork and keep the
  check and the prose in agreement.
- Script language split (bash / Python) not ratified.
- No target diff chosen yet for end-to-end validation.
- Role of `/Users/miguelvilagonzalez/repos/interactive-scala-poc` undecided —
  it is empty and not a git repo. Candidate use: a small fixture Scala project
  with a deliberate two-revision diff, for developing the grid pipeline without
  a large real project in the loop. Recommended, since step 3 needs a fast
  feedback cycle.
- `scala-cli` installed is **1.5.0** (via sdkman); newest is 1.17.1. Decide
  whether to require an upgrade or pin behaviour to 1.5.0.

---

## 9. Working agreements that apply (from `~/CLAUDE.md`)

- Prioritise quality, simplicity, correctness, robustness, long-term
  maintainability over development cost.
- Scala style: return `Either`/`Option` rather than throwing; side effects
  through the effect system in context; make invalid states unrepresentable;
  avoid `unsafe` variants **except** in generated driver code as agreed in §2.6.
- Tests: Weaver Test if the component has none. Prefer
  `expect.same(a, b) and expect.same(c, d)` over `expect.all(...)`.
- Use `sbt --client` for sbt commands; Metals MCP for compiles where possible;
  `sbtn` for integration tests.
- Use `cellar` to inspect dependency type structures; fall back to scalex, then
  Metals. Never inspect ivy folders or open jars by hand.
- `git mv` for renames, to preserve history. No model co-author trailer in
  commits.
- Communication: blunt and direct, no filler, no praise, plan first then one
  step at a time when debugging.

---

## 10. Suggested skills for the next session

| Skill | Why |
| --- | --- |
| `write-a-skill` | Authoring conventions for a new skill — invoke before writing `SKILL.md`. |
| `playback-dev:executing-sbt-commands` | `sbt --client` conventions, `crossScalaVersions`, Metals MCP, playback aliases. Needed for `preflight.sh`. |
| `explain-diff-html` | Read the parent `SKILL.md` and scaffold before forking; do not re-derive its structure. |
| `mattpocock-skills:tdd` or `tdd` | The three scripts have clean JSON contracts and deserve tests before the HTML work starts. |
| `playback-dev:loading-smithy-contracts` | Only if the chosen validation diff touches SiriusXM service contracts or generated types. |

Do not invoke `prototype` or any multi-agent workflow skill; the work is
sequential and the design is settled.
