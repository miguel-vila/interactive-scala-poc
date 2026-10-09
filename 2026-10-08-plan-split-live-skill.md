# Plan — split the live tier into `explain-scala-diff-live`

Date: 2026-10-08
Status: implemented and accepted on 2026-10-08. The repository suite passes
57 tests. Fresh Scala 3.5.0 / cats-effect 3.5.4 and Scala 2.13.14 / cats-effect
2.5.5 preflights, pure and approved effect grids (29 rows / 7 cells), and real
live kernel checks passed. Chromium verified each completed page (29
combinations, zero external requests), all three fixture variants (87
combinations / 15 quiz answers), and live editing, diagnostics, token rejection,
restart commands, and saved-page inertness. Both skill entry points validate.
The skills CLI installed both skills side by side for Codex and Claude Code
from this checkout; the installed builder/kernel smoke check passed.
Artifacts: `.validation/split-acceptance/.validation/` (ignored).
Supersedes, in `2026-10-02-plan-live-tier.md`: §2.1 (the fragment is appended to
every page), §2.7 (the kernel rebuilds revisions), §2.8 (validate, then append),
and the §3 file layout. Supersedes the README section "Install from this folder".
Scope: two skills in `skills/`. `explain-scala-diff-html` builds offline pages.
`explain-scala-diff-live` serves a built page through the local kernel. The
installer is replaced by the skills CLI. Tests move to the repository root.

---

## 1. What this changes

- `explain-scala-diff-html` produces offline pages only: no live fragment, no
  kernel hint in cells, one validation pass over the whole document.
- `explain-scala-diff-live` is a second skill. It accepts either a completed page
  plus its preflight, or a diff target. For a diff target its SKILL.md says to
  follow the html skill's workflow first, then start the kernel.
- The kernel inserts `live.html` into the page when it serves it. Files on disk
  never carry the fragment.
- `install.py` is deleted. Installation is `npx skills add
  miguel-vila/interactive-scala-poc`.
- Tests, fixtures, and acceptance tooling move to a repository-level `tests/`
  tree. An installed skill contains `SKILL.md`, `references/`, and `scripts/`.

---

## 2. Decisions (settled 2026-10-08)

### 2.1 Shared code: the live skill imports from the installed html sibling

The live skill needs three names from the html skill's `scala_diff.py`:
`ContractError`, `read_json`, and `HARNESS` (the quote and render code, so live
and recorded output render identically). Its tests also need `build-page.py`.

Resolution rule, in `kernel.py` and `live_driver.py`:

```python
HTML_SCRIPTS = Path(__file__).resolve().parents[2] / "explain-scala-diff-html" / "scripts"
```

`parents[2]` is the directory that contains both skills. It holds in every
layout the skills CLI produces and in this checkout:

| Layout | Both skills live in |
| --- | --- |
| checkout | `skills/` |
| `skills add` default (`symlink` mode) | `~/.agents/skills/` or `<project>/.agents/skills/`; agent directories are symlinks to it, and `resolve()` follows them |
| `skills add --copy` | each agent's skills directory |

When `HTML_SCRIPTS / "scala_diff.py"` is missing, the kernel prints one JSON line
and exits 1:

```json
{"ok": false, "diagnostic": "explain-scala-diff-html is not installed next to this skill at <path>. Install it with: npx skills add miguel-vila/interactive-scala-poc --skill explain-scala-diff-html"}
```

Install and update both skills from the same repository revision. The kernel
refuses a page whose `pageVersion` it does not know.

Rejected: a shared directory without `SKILL.md` (the CLI installs only skill
directories); a vendored copy of the shared module (two sources for the render
format); environment variables or searches of other agent directories.

### 2.2 The page carries `page-provenance`; the kernel injects the fragment

- The builder's block becomes `<script type="application/json"
  id="page-provenance">` with `pageVersion: 2` and no `kernelScript`. The
  `preflight` path stays; it is page metadata the kernel reads by default.
- The builder never appends `live.html`. `validate_page` checks the finished
  document; the network-API rule applies to the whole file again.
- At start the kernel reads `references/live.html` from its own skill and
  inserts it before the final `</body>` of the bytes it serves. `console.html`
  already precedes `</body>`, so the fragment still mounts after the console.
- The fragment keeps its `http:` plus token guard, so a served page saved to disk
  stays inert.
- The restart command moves out of the page. `GET /api/status` returns
  `restartCommand`: the kernel's own command line with resolved page, preflight,
  and flags, quoted with `shlex.join`. The fragment shows it in the
  `kernel-gone` banner. Offline pages show no kernel hint; the html SKILL.md ends
  with one line that names the live skill.
- `status.page.sha256` hashes the file on disk, which is the page the reader has.

### 2.3 No in-kernel rebuild

`resolve_preflight` keeps two sources: the explicit `--preflight` path, else the
path recorded in `page-provenance`. It verifies shas, `workingTreeHash`,
module, `builds`, and that `tempDir` and every classpath file exist. Any other
case is a `ContractError` that names the page's shas and module and says to run
the html skill's `preflight.py` for them and pass the result as `--preflight`.
For a working-tree page the message adds that the original preflight is
required. This removes the `preflight` import, the `argparse.Namespace`
construction, and the "sbt is rebuilding" messages. No test covered the rebuild.

### 2.4 fs2 detection moves to the live skill

`fs2_core` moves to `live_driver.py`. Preflight stops writing `fs2`; the
preflight contract drops the field. The kernel computes `fs2` from each
revision's classpath file at start, which is today's fallback made the only
path. The fs2 assertions move to the live tests.

### 2.5 Tests at the repository root

```
tests/
  __init__.py
  support.py                 # HTML_SKILL, LIVE_SKILL, FIXTURES paths; load(skill, script)
  README.md
  prepare_fixture.py  refresh_demo.py  validate_fixture.py
  fixtures/  base/ head/ bin/ cells.json effects.json narrative.json
  html/  __init__.py test_pipeline.py test_page.py test_browser_check.py test_browser_wrapper.py
  live/  __init__.py test_kernel.py test_live_driver.py test_installed_layout.py browser-check-live.cjs
```

One command from the repository root:

```bash
python3 -m unittest discover -s tests -t .
```

`browser-check.cjs` is a runtime file (SKILL.md step 9 runs it through
`browser-check.py`), so its page mode and offline fixture mode move to
`skills/explain-scala-diff-html/scripts/browser-check.cjs`. Its kernel part
becomes `tests/live/browser-check-live.cjs`. The Playwright cache directory and
`.validation/` conventions do not change.

`test_install.py` is deleted with the installer. Its useful property, "a copied
skill folder works on its own", becomes `tests/live/test_installed_layout.py`:

1. Copy both skill folders into a temporary `skills/` directory, excluding
   `__pycache__` as the CLI does. Build a page with the copied html builder.
   Start the copied kernel with the fake toolchain. GET the page and assert the
   fragment is present once.
2. Copy only the live skill. Assert the kernel prints the §2.1 diagnostic.

### 2.6 Installation through the skills CLI

The CLI discovers every `SKILL.md` under `skills/`, so the layout needs no
manifest. README text:

```bash
npx skills add miguel-vila/interactive-scala-poc          # choose skills and agents
npx skills add miguel-vila/interactive-scala-poc -g --all # both skills, every agent, user level
npx skills add /path/to/this/checkout -g                  # from a local checkout
```

The CLI copies each selected skill folder, so the repository keeps runtime files
inside the skills and everything else outside them.

---

## 3. File layout (after)

```
skills/explain-scala-diff-html/
  SKILL.md
  references/  contracts.md  html-scaffold.html  console.html
  scripts/     preflight.py  probe-types.py  run-grid.py  build-page.py
               browser-check.py  browser-check.cjs  scala_diff.py
skills/explain-scala-diff-live/
  SKILL.md
  references/  contracts.md  live.html
  scripts/     kernel.py  start-kernel.py  live_driver.py
tests/                                   # §2.5
README.md  .gitignore  run-github-issues.sh  2026-*.md
```

Moves use `git mv`:

| From (`skills/explain-scala-diff-html/`) | To |
| --- | --- |
| `scripts/kernel.py`, `scripts/start-kernel.py` | `skills/explain-scala-diff-live/scripts/` |
| `references/live.html` | `skills/explain-scala-diff-live/references/` |
| `tests/test_kernel.py` | `tests/live/` |
| `tests/test_pipeline.py`, `test_page.py`, `test_browser_check.py`, `test_browser_wrapper.py` | `tests/html/` |
| `tests/prepare_fixture.py`, `refresh_demo.py`, `validate_fixture.py`, `tests/README.md` | `tests/` |
| `tests/fixtures/**` | `tests/fixtures/` |
| `tests/browser-check.cjs` | `scripts/browser-check.cjs` |

Deleted with `git rm`: `install.py`, `tests/test_install.py`.

New files: live `SKILL.md` and `references/contracts.md`;
`scripts/live_driver.py`, extracted from `scala_diff.py` (`live_command`,
`live_driver_source`, `compiler_lines`, `compile_driver`, `run_compiled`,
`fs2_core`); `tests/support.py` and the `__init__.py` files;
`tests/live/test_live_driver.py`, extracted from `test_pipeline.py`;
`tests/live/test_installed_layout.py`; `tests/live/browser-check-live.cjs`,
extracted from `browser-check.cjs`.

---

## 4. Contracts

### `page-provenance` (emitted by `build-page.py`)

```json
{"pageVersion": 2, "projectDir": "...", "module": "core",
 "preflight": "/absolute/preflight.json",
 "head": {"sha": "...", "workingTreeHash": null}, "base": {"sha": "..."},
 "scalaVersion": "3.5.0", "toolchain": {"command": ["scala-cli"]}}
```

`preflight` is null when the builder ran without `--preflight`; `base` is null
in no-diff mode.

### DOM contract the live fragment consumes

Documented in the live skill's `contracts.md`, checked by a live unit test that
builds a page with the html builder, and exercised in Chromium by the fixture
browser check:

- `<div class="scala-cell" data-cell="<id>">`, id matching `[a-z][a-z0-9-]*`.
- `<script type="application/json" id="cell-<id>">` with `call`, `imports`,
  `setup`, `params`, `rows`, `results`.
- The console renders each cell's first direct `<pre>` child as the bindings, a
  blank line, and the call; the fragment reads it to prefill the editor.
- `<section id="code">` hosts the scratch cell.
- Classes `scala-banner` and `scala-status`; CSS variables `--line`, `--ink`,
  `--panel`, `--del`, `--muted`.
- The `console.html` script precedes `</body>`.
- One `page-provenance` block with a known `pageVersion`.

### Kernel CLI

```
kernel.py --page <html> [--preflight <json>] [--allow-effects] [--idle-minutes 30]
          [--max-timeout-seconds 60] [--port 0] [--no-bloop] [--temp-dir <parent>]
```

Flags and the startup JSON are unchanged. Changed behaviour: the served page
carries the fragment; no rebuild (§2.3); a page with `live-provenance` or an
unknown `pageVersion` is refused with "rebuild the page with the current
explain-scala-diff-html"; a missing html sibling gives the §2.1 diagnostic.
`start-kernel.py` is unchanged.

### HTTP

`GET /api/status` gains `restartCommand` (string). Every other route, header
check, state, and kind is unchanged.

---

## 5. Implementation order (each step lands with the suite green)

1. **Tests to the root; installer out.** Create `tests/` per §2.5 with
   `support.py`; move `browser-check.cjs` into html `scripts/` and update
   `browser-check.py` and `test_browser_check.py`; delete `install.py` and
   `test_install.py`; add `test_installed_layout.py` with the html-only case
   (copy the folder, build a page); rewrite the README install section per
   §2.6. Skill behaviour does not change.
2. **Builder and fragment.** `page-provenance` v2 without `kernelScript`; stop
   appending `live.html`; whole-page validation; the offline browser check
   asserts zero `.live-panel` and zero `#live-provenance`. The kernel, still in
   the html skill at this step, injects the fragment when serving and returns
   `restartCommand`; `live.html` reads it from status. Tests: the file on disk
   has no fragment and the served page has it once; the fragment URL audit is
   unchanged.
3. **Kernel inputs.** Drop the rebuild branch (§2.3); drop `fs2` from preflight
   and compute it in the kernel (§2.4). Tests: a start without a usable
   preflight returns the diagnostic that names shas, module, and the preflight
   command; preflight tests stop expecting `fs2`; the recorded-path start for a
   working-tree page still passes.
4. **The live skill.** `git mv` `kernel.py`, `start-kernel.py`, `live.html`;
   extract `live_driver.py`; add the sibling import; move `test_kernel.py` to
   `tests/live/`, extract `test_live_driver.py` and `browser-check-live.cjs`;
   add the two-skill and live-only cases to `test_installed_layout.py`; point
   `validate_fixture.py --live` at the live kernel.
5. **Docs.** Live `SKILL.md`: toolchain check, the two entry forms, start,
   report URL, pid and log, stop with `kill -TERM`, restart after any page
   rebuild, never call `/api/*`, `--allow-effects` only on request. Live
   `contracts.md`: kernel CLI, HTTP, DOM contract, provenance, sibling rule.
   Html `SKILL.md`: remove "Live mode" and "optional live kernel", add the
   closing line. Html `contracts.md`: remove the live kernel section and the
   builder's live paragraph, update the preflight fields and the provenance
   paragraph. README: both skills. `tests/README.md`: new paths and command.
   Add a status line to `2026-10-02-plan-live-tier.md` that points here.
6. **Fixture acceptance.** `prepare_fixture.py`, preflight, `validate_fixture.py`
   (pure; `--effects` after named approval; `--live`), `browser-check.py --page`
   on the built page, `browser-check-live.cjs` in fixture mode, then the CE2
   variant. Record the results in this file's Status line.

---

## 6. Acceptance criteria

- Installing both skills with the skills CLI puts them side by side, and the
  installed live kernel serves a page built by the installed html skill.
  Installing only the live skill yields the §2.1 diagnostic.
- A page from the html skill contains no `fetch(`, no `.live-panel`, no
  `live-provenance`, and exactly one `page-provenance` block. From `file://` it
  makes zero requests.
- The served page carries the fragment once, before `</body>`; serving does not
  change the file on disk.
- Every kernel behaviour from the 2026-10-02 plan holds: loopback bind, token,
  Host and Origin checks, queue limit, cancel within one second, timeouts, idle
  exit, effects refused without `--allow-effects`, verbatim diagnostics with
  editor lines, the scratch cell, the 401 banner.
- A page from the previous builder is refused with a rebuild instruction.
- `python3 -m unittest discover -s tests -t .` passes from the repository root.
  Installed skills contain only `SKILL.md`, `references/`, and `scripts/`.
- No new dependencies: Python standard library, git, sbt thin client, Java,
  Scala CLI; Node and Playwright for browser checks only.

---

## 7. Non-goals

A vendored copy of shared code; locating the html skill by environment variable
or search; rebuilding revisions inside the kernel; a runtime browser check
shipped in the live skill; changes to `run-grid.py`, the grid, or the quiz
contracts; sandboxing reader code.

---

## 8. Follow-up outside the repository

`~/.agents/skills/explain-scala-diff-html` is a copy from about 2026-10-02 (it has
`preflight.sh` and lacks `browser-check.py`) and is absent from
`~/.agents/.skill-lock.json`. Remove it and the `~/.claude/skills` symlink to
it, then install both skills with the CLI.

Implementation notes: direct acceptance-tool execution removes `tests/` from
Python's import search path so `tests/html` cannot shadow the standard library
`html` module. The acceptance grid runs serially to keep the 0.2-second timeout
fixture independent of concurrent compiler load. `validate_fixture.py --live`
runs effects-disabled checks; adding `--effects` runs the approved enabled mode.

Follow-up completed: the stale installation and Claude symlink named in §8 were
already absent. Installed both skills with `npx skills add <checkout> -g --skill
explain-scala-diff-html explain-scala-diff-live --agent codex claude-code -y`.
