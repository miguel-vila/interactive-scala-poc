---
name: explain-scala-diff-html
description: Produces a self-contained HTML explanation of a Scala change with verified before/after consoles and a quiz derived from actual executions. Use when explaining Scala diffs, commits, branches, or PRs, or demonstrating Scala behaviour with offline parameter widgets.
---

# Explain Scala Diff (HTML)

Write an offline Scala explanation with independent cells and finite grids.
## 0. Preflight

Read this skill's `references/html-scaffold.html`, `references/console.html`,
and `references/live.html`. All are bundled with the skill; no sibling skill
is required. If one is missing, stop and report its full expected path.
Read [the script contracts](references/contracts.md) before authoring cells.
Use Python 3 stdlib, git, Java, sbt's thin client, and scala-cli. Do not upgrade
existing tools automatically. Browser validation also requires Node.js,
Playwright, and Chromium. Installing missing browser test dependencies in a
disposable directory is permitted; never add them to the target project. See
[browser setup](tests/README.md#browser-checks). For Scala newer than the installed
CLI supports, select
`--cli-version <release>` explicitly; this preserves the system installation.

```bash
bash <skill-dir>/scripts/preflight.sh <project-dir> --module <owning-sbt-id> --base <sha> > /tmp/preflight.json
```

Omit `--base` for no-diff mode. `--head <sha>` selects a committed head; otherwise
snapshot tracked local changes and untracked files. Print the JSON report before
building a page. Builds run in disposable worktrees outside the project; do not
edit its build files. Resolve the Scala version and classpath per owning module.
Base build failure permits a visibly labelled head-only page. Head failure stops.
## 1. Resolve the target

For local changes, inspect `git diff HEAD`, `git diff --cached`, and status.
For a branch, resolve its merge base; for a commit/range, use show/diff. For a PR,
read its diff, title, body, and commits with gh. Read the stat and commit messages.
Fetch remote refs when necessary; preserve the user's checkout and local changes.
Do not delegate: this workflow is sequential.
## 2. Gather context and survey construction

Read changed files in full, their callers, types, tests, and older implementations.
Choose concrete data to reuse in prose, figures, and consoles. Survey admissible
inputs: primitives, exhaustive enum/case-object cases, literal-backed wrappers,
Option/collections, shallow case classes (depth 2, at most 8 fields), and java.time.
Expose raw values for validating constructors; show construction in the snippet.
Inline each cell's independent imports/setup. Instantiate generic effects to IO.
Support only pure values, IO, Resource[IO, A], and fs2.Stream[IO, A].

**Before any probe or grid invokes an effectful function, obtain explicit user
confirmation naming its fully qualified function.** Explain repeated execution.
Set `function` in the spec and pass `--confirm-effect <function>` only after that
confirmation. This includes setup that performs effects; label such cells IO.
Never infer permission from a request to explain a diff. Generated harnesses alone
may use unsafeRunSync; snippets shown to the reader retain the project's call shape.

Probe every cell with `scripts/probe-types.py --preflight /tmp/preflight.json
--cell /tmp/cell.json`. Only a successful default call is admissible. Keep rejected
diagnostics verbatim. Use each successful probe's resolved `cell` in `cells.json`.
Run `scripts/run-grid.py --preflight /tmp/preflight.json --cells /tmp/cells.json
--dropped /tmp/dropped.json` with named confirmations as needed. Refuse oversized
grids (48 rows/cell, 200/page); never truncate. Record values, throws, and timeouts.
## 3. Output file

Build with `scripts/build-page.py --grid /tmp/grid.json --narrative /tmp/narrative.json
--slug <short-kebab-name>`. Its default is
`~/explanations/<YYYY-MM-DD>-explanation-<slug>.html`. Keep the page outside the
target repo. Open the completed file with `open <path>` and report that path.
## 4. Required sections

Use one long page with matching TOC anchors; no top-level tabs.
Background: skippable beginner context followed by context directly relevant to
the change. Intuition: the core idea with concrete toy inputs. Code: group the
walkthrough to tell a story and anchor consoles next to the claims they verify.
Quiz: **five** multiple-choice questions generated from actual recorded rows,
preferring rows where base/head differ. The builder supplies correct flags and
feedback and preserves the scaffold's option shuffle. Supply at least
five recorded rows; never invent executions to reach the quiz count.
## 5. Writing style

Use clear, connected prose; make each section flow into the next. Use callouts
for concepts and edge cases. State once that approved effects execute through
unsafeRunSync with a five-second maximum timeout; identify each effectful cell.
## 6. Diagrams

Reuse two or three diagram families. Prefer concrete data flows and before/after
pairs with the same layout. Build diagrams with HTML/CSS, never ASCII art.
Use real HTML tables and lists. Wrap wide content in a scrolling container.
## 7. HTML rules

One file, inline CSS/JS, no external requests or fonts. Use responsive styling.
Keep code in pre elements (or explicitly pre-wrapped divs), and wide content in
overflow-x:auto containers. Embed JSON safely; use the builder to escape script
terminators. Append console.html and live.html to the bundled scaffold. Keep setup
expandable. Show the target code and results, not generated execution drivers.
Include both shas, module, each revision's Scala and cats-effect versions,
effect approvals, dropped cells and raw diagnostics.
## 8. Check before finishing

The builder checks anchors, resources, code blocks, option shuffle, and exactly
five correct flags. Also check code whitespace and absence of external resources.
Run `tests/browser-check.cjs` with `--page <completed-html>` using Playwright and
Chromium; install them locally for this check if missing. It exercises every
widget combination offline, quiz answers, changed markers, and phone-width
overflow, and checks that `file://` makes zero external requests. Inspect its
desktop and mobile screenshots visually. Verify head-only banners and provenance
against the grid. If the browser cannot run, report that visual and interaction
checks remain unverified; CSS inspection or a static script does not replace them.
Report any toolchain or base-build limitation explicitly.

## Live mode

Build every page with the live fragment and provenance. Do not start the
kernel during an ordinary explanation. When the user asks for live mode,
run `python3 <skill-dir>/scripts/start-kernel.py --page <page.html>
--preflight <session-preflight.json>`. This launcher waits for the kernel's
startup JSON, then exits while the kernel stays running. On `ok: true`, give
the user its exact `url`, `pid`, and `log` path. Tell them to stop it with
`kill -TERM <pid>`; it also exits after 30 idle minutes. On `ok: false`,
report the diagnostic and launcher log. Do not call `/api/*` to check it.
If no matching preflight file survives, omit `--preflight` to rebuild committed
revisions. A page built from uncommitted changes needs its original preflight.
Pass `--allow-effects` only when the user requests that mode. The browser
shows the mode and warns that snippets run with the reader's privileges.

Never call the kernel's `/api/*` routes as part of an explanation. Use
`run-grid.py` for agent-run examples and retain its named effect confirmation
gate. Never move a live result into narrative or quiz evidence; only recorded
grid rows support those claims. See [kernel contracts](references/contracts.md#live-kernel)
for the CLI, API, and provenance details.
