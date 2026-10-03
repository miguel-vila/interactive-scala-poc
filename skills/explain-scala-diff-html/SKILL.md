---
name: explain-scala-diff-html
description: Produces a self-contained HTML explanation of a Scala change with verified before/after consoles and a quiz derived from actual executions. Use when explaining Scala diffs, commits, branches, or PRs, or demonstrating Scala behaviour with offline parameter widgets.
---

# Explain Scala Diff (HTML)

Write an offline Scala explanation with independent cells and finite grids.
## 0. Check the toolchain and create a session directory

Open [the script contracts](references/contracts.md) only when a script rejects
input or a field's meaning is unclear.
Use Python 3.9 or newer, git, Java, sbt's thin client, and scala-cli; PRs also
require gh. Check that these tools are available before resolving the target.
Do not upgrade existing tools automatically. Browser validation also requires
Node.js, Playwright, and Chromium. Installing missing browser test dependencies in a
disposable directory is permitted; never add them to the target project. See
[browser setup](tests/README.md#browser-checks). For Scala newer than the installed
CLI supports, select
`--cli-version <release>` explicitly; this preserves the system installation.

Set `skill` to this skill's absolute directory and `session_dir` to an absolute
directory in the current session's scratchpad, outside the target project. Shell
variables do not persist across separate command invocations: set `skill` and
`session_dir` in the same shell command that uses them, or replace them with
literal absolute paths. Use `python3 "$skill/scripts/<name>.py"` for script
commands and absolute project, session, and output paths. Keep the session
directory for the whole explanation and any live kernel session. Create it before
running preflight:

```bash
command -v git java sbt scala-cli python3
python3 -c 'import sys; assert sys.version_info >= (3, 9)'
sbt --version # Verify this sbt supports --client.
# For PRs, also check: command -v gh
skill=/absolute/path/to/explain-scala-diff-html
session_dir=/absolute/path/to/session
mkdir -p "$session_dir"
```

## 1. Resolve the target

For local changes, inspect `git diff HEAD`, `git diff --cached`, and status.
For a branch, resolve its merge base with the branch's base ref; for a commit or
range, use show/diff. For a PR, read its diff, title, body, and commits with gh.
Read the stat and commit messages. Fetch remote refs when necessary; preserve
the user's checkout and local changes. Do not delegate: this workflow is sequential.

Map the target to preflight revision flags before running it:

| Target | Flags |
| --- | --- |
| Local changes | `--base HEAD`; omit `--head` to snapshot tracked and non-ignored untracked files. |
| Branch | `--base <merge-base>` and `--head <branch-sha>`. Find the merge base with `git merge-base <base-ref> <branch>`. |
| Commit or range | `--base <first-commit>^` and `--head <last-commit>`. For one commit, first and last are the same. |
| PR | Fetch its head ref with `git fetch origin pull/<n>/head` (or get `headRefOid` and `baseRefName` with `gh pr view <n> --json headRefOid,baseRefName` and fetch those refs). Use the fetched head SHA as `--head` and its merge base with the fetched base ref as `--base`. Never check out the PR. |
| No diff | Omit `--base` and `--head`. |

## 2. Gather context and run preflight

Read changed files in full, their callers, types, tests, and older implementations.
Choose concrete data to reuse in prose, figures, and consoles. Survey admissible
inputs: primitives, exhaustive enum/case-object cases, literal-backed wrappers,
Option/collections, shallow case classes (depth 2, at most 8 fields), and java.time.
Expose raw values for validating constructors; show construction in the snippet.
Inline each cell's independent imports/setup. Instantiate generic effects to IO.
Support only pure values, IO, Resource[IO, A], and fs2.Stream[IO, A].

Find the owning sbt project before preflight: match each changed path to the
project's base directory in `build.sbt` (`project in file("...")`, or the
default directory for `lazy val x = project`). Use that project's sbt id for
`--module`. If changed files belong to different modules, run a separate
preflight and use a separate session directory for each. With the revision
flags from step 1, run (replace the quoted placeholders with actual values):

```bash
skill=/absolute/path/to/explain-scala-diff-html
session_dir=/absolute/path/to/session
python3 "$skill/scripts/preflight.py" /absolute/path/to/project --module "<owning-sbt-id>" --base "<base-sha-or-HEAD>" --head "<committed-head-sha>" --temp-dir "$session_dir" > "$session_dir/preflight.json"
cat "$session_dir/preflight.json"
```

Omit `--head` for local changes; omit both revision flags for no-diff mode.
Preflight builds in disposable worktrees outside the project; do not edit its
build files. It resolves the Scala version and classpath per owning module.
Base build failure permits a visibly labelled head-only page. Head failure stops.
The report's `tempDir` contains compiled classes and classpath files. Do not
remove it or the session directory while a kernel using this preflight runs;
the launcher exiting does not mean the kernel has stopped.

## 3. Author cells and run the grid

**Before any probe or grid invokes an effectful function, obtain explicit user
confirmation naming its fully qualified function.** Explain repeated execution.
Set `function` in the spec and pass `--confirm-effect <function>` only after that
confirmation. This includes setup that performs effects; label such cells IO.
Never infer permission from a request to explain a diff. Generated harnesses alone
may use unsafeRunSync; snippets shown to the reader retain the project's call shape.

Write `cells.json` as an array of cell specs. This example shows the input shapes;
replace the sample calls and types with those found in the target project:

```json
[
  {
    "cellId": "decode-retry",
    "module": "core",
    "snippet": "demo.Demo.decode(input, mode, id)",
    "effect": "IO",
    "function": "demo.Demo.decode",
    "imports": ["demo._"],
    "setup": "",
    "params": [
      {"name": "input", "type": "String", "values": ["dGVzdA==", "", "!!!"], "default": "dGVzdA=="},
      {"name": "mode", "type": "demo.Mode", "enumCases": ["demo.Mode.Strict", "demo.Mode.Lenient"]},
      {"name": "id", "type": "demo.Id", "values": [{"scala": "demo.Id(\"a\")", "label": "a"}]}
    ]
  }
]
```

Write `narrative.json` with these fields. `subtitle` is optional. Each of the
last three fields is inner HTML of a section created by the builder: start at
`<h3>` or `<p>`. Do not include `<section>`, `<h2>`, or ids the scaffold already
uses. Put each recorded cell's anchor where its explanation belongs:

```json
{
  "title": "A change in decoding",
  "subtitle": "Recorded behaviour before and after",
  "background": "<p>Context for the change.</p>",
  "intuition": "<h3>The key idea</h3><p>What changes for a concrete input.</p>",
  "code": "<p>Try the recorded call.</p><div class=\"scala-cell\" data-cell=\"decode-retry\"></div>"
}
```

The anchor itself is `<div class="scala-cell" data-cell="decode-retry"></div>`.

Author independent cells in an absolute-path JSON file, then run the grid with
named confirmations as needed:

```bash
skill=/absolute/path/to/explain-scala-diff-html
session_dir=/absolute/path/to/session
python3 "$skill/scripts/run-grid.py" --preflight "$session_dir/preflight.json" --cells "$session_dir/cells.json" --output "$session_dir/grid.json"
```

The grid drops non-compiling cells with verbatim diagnostics and records values,
throws, and timeouts. Refuse oversized grids (48 rows/cell, 200/page); never
truncate. Use the probe only to iterate on one rejected cell's default call:

```bash
skill=/absolute/path/to/explain-scala-diff-html
session_dir=/absolute/path/to/session
python3 "$skill/scripts/probe-types.py" --preflight "$session_dir/preflight.json" --cell "$session_dir/cell.json" --output "$session_dir/resolved-cell.json" --append-to "$session_dir/cells.json"
```

The probe saves the resolved cell and adds it to `cells.json` only when its
default call returns a value. Keep rejected diagnostics verbatim. Rerun the grid
after changing cells; pass `--dropped "$session_dir/dropped.json"` when retaining
separately probed rejections.

## 4. Build the page

Build with:

```bash
skill=/absolute/path/to/explain-scala-diff-html
session_dir=/absolute/path/to/session
python3 "$skill/scripts/build-page.py" --grid "$session_dir/grid.json" --narrative "$session_dir/narrative.json" --preflight "$session_dir/preflight.json" --slug "<short-kebab-name>"
```

Its default output is
`~/explanations/<YYYY-MM-DD>-explanation-<slug>.html`. Keep the page outside the
target repo. Open the completed file with `open /absolute/path/to/page.html` and
report that path.
## 5. Required sections

Use one long page with matching TOC anchors; no top-level tabs.
Background: skippable beginner context followed by context directly relevant to
the change. Intuition: the core idea with concrete toy inputs. Code: group the
walkthrough to tell a story and anchor consoles next to the claims they verify.
Quiz: **five** multiple-choice questions generated from actual recorded rows,
preferring rows where base/head differ. Supply at least five recorded rows;
never invent executions to reach the quiz count.
## 6. Writing style

Use clear, connected prose; make each section flow into the next. Use callouts
for concepts and edge cases. Identify each effectful cell.
## 7. Diagrams

Reuse two or three diagram families. Prefer concrete data flows and before/after
pairs with the same layout. Build diagrams with HTML/CSS, never ASCII art.
Use real HTML tables and lists. Wrap wide content in a scrolling container.

Scaffold classes for narrative HTML:

- Callouts: `callout`, `callout edge`, or `callout skippable`; put the heading
  in a child with class `label`.
- Figures: `<figure>` with `<figcaption>`; wrap wide diagrams in `scroll`. A
  data flow uses `flow` with `node` children containing `name`, `role`, and
  `payload`; use `arrow` between nodes and `changed` on a changed node.
- Before/after pairs: `ba` with `before` and `after` children, each with a
  `head`. UI mockups: `ui` with `bar` and `body`, containing `row` and `btn`.
- Code diffs: `pre code` with `add`, `del`, and `ctx` spans.

## 8. HTML rules

One file, inline CSS/JS, no external requests or fonts. Use responsive styling.
Keep code in pre elements (or explicitly pre-wrapped divs), and wide content in
overflow-x:auto containers. Keep setup expandable. Show the target code and
results, not generated execution drivers.
Include both shas, module, each revision's Scala and cats-effect versions,
effect approvals, dropped cells and raw diagnostics.
## 9. Check before finishing

Check code whitespace.
Set `skill` to this skill's absolute directory, `page` to the completed HTML's
absolute path, and `browser_dir` as in the
[browser setup](tests/README.md#browser-checks).
After installing Playwright and Chromium there, run:

```bash
skill=/absolute/path/to/explain-scala-diff-html
page=/absolute/path/to/page.html
browser_dir=/absolute/path/to/browser-directory
PLAYWRIGHT_BROWSERS_PATH="$browser_dir/browsers" node "$skill/tests/browser-check.cjs" "$browser_dir/node_modules/playwright" "" --page "$page"
```

The empty executable argument selects the Chromium installed in
`PLAYWRIGHT_BROWSERS_PATH`; pass a system Chrome/Chromium executable there if
using one instead. The check exercises every widget combination offline, quiz
answers, changed markers, and phone-width overflow, and checks that `file://`
makes zero external requests. Inspect its desktop and mobile viewport
screenshots next to the page. Verify head-only banners and provenance against
the grid. If the browser cannot run, report that visual and interaction
checks remain unverified; CSS inspection or a static script does not replace them.
Report any toolchain or base-build limitation explicitly.

## Live mode

Do not start the kernel during an ordinary explanation. Finish the page and its
browser checks before starting the kernel: it reads the HTML, provenance, and
cell IDs once at startup. If you rebuild the page afterward, stop the old kernel
and start a new one with the rebuilt page and matching preflight. Give the user
the new URL.

When the user asks for live mode, run:

```bash
skill=/absolute/path/to/explain-scala-diff-html
session_dir=/absolute/path/to/session
page=/absolute/path/to/page.html
python3 "$skill/scripts/start-kernel.py" --page "$page" --preflight "$session_dir/preflight.json" --temp-dir "$session_dir"
```

This launcher waits for the kernel's
startup JSON, then exits while the kernel stays running. On `ok: true`, give
the user its exact `url`, `pid`, and `log` path. Tell them to stop it with
`kill -TERM <pid>`; it also exits after 30 idle minutes. On `ok: false`,
report the diagnostic and launcher log. Do not call `/api/*` to check it.
Keep `session_dir` and the report's `tempDir` until that kernel stops, including
when it exits on idle timeout. Then they may be cleaned up.
If no matching preflight file survives, omit `--preflight` to rebuild committed
revisions. A page built from uncommitted changes needs its original preflight.
Pass `--allow-effects` only when the user requests that mode. The browser
shows the mode and warns that snippets run with the reader's privileges.

Never call the kernel's `/api/*` routes as part of an explanation. Use
`run-grid.py` for agent-run examples and retain its named effect confirmation
gate. Never move a live result into narrative or quiz evidence; only recorded
grid rows support those claims. See [kernel contracts](references/contracts.md#live-kernel)
for the CLI, API, and provenance details.
