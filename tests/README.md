# Validation

The production scripts need only Python's standard library. Tests use unittest;
the browser acceptance check uses Node.js, Playwright, and Chromium.
Run from the repository root and set `skill` to the absolute
`skills/explain-scala-diff-html` directory. Live acceptance also uses
`skills/explain-scala-diff-live`.

```bash
python3 -m unittest discover -s tests -t .
python3 tests/prepare_fixture.py
```

The fixture command creates `.validation/fixture`, a disposable git repository
with two commits and three sbt modules. It never alters a real project.
Read `.validation/revisions.json`, then run preflight using its projectDir and
base fields and `--module core`, saving stdout to `.validation/preflight.json`.
Invoke it as `python3 "$skill/scripts/preflight.py" <project-dir> --module core
--base <revision> --temp-dir <session-dir> > .validation/preflight.json`.
Run `python3 tests/validate_fixture.py` for pure calls. Use `--effects`
only after obtaining named confirmation for the four fixture functions listed
in the validator. The check runs real Scala, verifies compiler rejection,
values/throws/timeouts, both revisions, all three effect adapters, exact quiz
evidence, and head-only/no-diff pages. The timeout cell also verifies that a
successful row still executes after a timed-out row. Acceptance runs the grid
serially to keep its short timeout independent of concurrent compiler load.

After building `.validation/2026-10-01-explanation-scala-diff.html`, run
`python3 tests/validate_fixture.py --live` to validate the real
kernel through its loopback HTTP API. It checks both revisions, compile
diagnostics and editor lines, timeout, cancellation, refused effects, and
allowed effects with `--live --effects` after named approval. The local HTTP
tests use fake Scala CLI and Java executables:
`python3 -m unittest discover -s tests -t .`.
They also verify that `start-kernel.py` leaves the server running after the
launcher exits and returns a usable URL, PID, and log paths.

For Scala 2 / cats-effect 2, use `prepare_fixture.py --legacy`, read
`.validation/revisions-ce2.json`, preflight module `legacy`, and pass the resulting
JSON through `validate_fixture.py --preflight <json> --output-dir .validation/ce2`.
The same named effect confirmations apply; add `--effects` after approval.

## Browser checks

Playwright is a browser-test dependency, not a project dependency. The wrapper
installs it in a reusable cache outside the target repository and prefers a
system Chrome/Chromium executable. Set `CHROME_PATH` to select one explicitly.
Run from a writable development directory with `skill` set as above and `page`
set to the completed HTML path:

```bash
python3 "$skill/scripts/browser-check.py" --page "$page"
```

The wrapper prints one JSON line with the result and screenshot paths. It uses
`~/.cache/explain-scala-diff-html/playwright` (or `$XDG_CACHE_HOME`) and
downloads Chromium there only if no system browser is found. For fixture mode,
call `skills/explain-scala-diff-html/scripts/browser-check.cjs` without `--page`.
Run `tests/live/browser-check-live.cjs` separately for the fake local kernel,
live editing, compiler line mapping, and the unauthorized banner. Both accept
Playwright's module path and an optional Chromium executable as their first
arguments. The live check accepts an optional fixture output directory third.

```bash
node skills/explain-scala-diff-html/scripts/browser-check.cjs .validation/browser/node_modules/playwright
node tests/live/browser-check-live.cjs .validation/browser/node_modules/playwright
```

Copied-install tests build with a copied HTML skill and serve with its copied
live sibling, and verify the diagnostic for a live-only installation. Installed
skills contain only `SKILL.md`, `references/`, and `scripts/`.

The check selects every recorded combination, verifies rendered output and
changed markers, answers the quiz, checks phone-width overflow, and fails on
external requests or script errors. It saves desktop and mobile screenshots in
the completed page's directory as `<page-stem>-desktop.png` and
`<page-stem>-mobile.png`, plus `<page-stem>-console.png`; the first two capture
the viewport. Fixture mode saves `desktop.png`, `mobile.png`, and `console.png`
in `.validation`. Inspect the desktop and mobile screenshots visually. Keep the
completed page and its screenshots outside the skill and target project. Do not
commit generated classpaths, driver caches, or pages.
