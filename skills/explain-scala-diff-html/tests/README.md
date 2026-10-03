# Validation

The production scripts need only Python's standard library. Tests use unittest;
the optional browser acceptance check uses Playwright and an installed Chromium.
Run from a writable development directory and set `skill` to this skill's
absolute directory. The HTML scaffold is bundled; no other skill is needed.

```bash
python3 -m unittest discover -s "$skill/tests" -v
python3 "$skill/tests/prepare_fixture.py"
```

The fixture command creates `.validation/fixture`, a disposable git repository
with two commits and three sbt modules. It never alters a real project.
Read `.validation/revisions.json`, then run preflight using its projectDir and
base fields and `--module core`, saving stdout to `.validation/preflight.json`.
Run `python3 "$skill/tests/validate_fixture.py"` for pure calls. Use `--effects`
only after obtaining named confirmation for the four fixture functions listed
in the validator. The check runs real Scala, verifies compiler rejection,
values/throws/timeouts, both revisions, all three effect adapters, exact quiz
evidence, and head-only/no-diff pages. The timeout cell also verifies that a
successful row still executes after a timed-out row.

After building `.validation/2026-10-01-explanation-scala-diff.html`, run
`python3 "$skill/tests/validate_fixture.py" --live` to validate the real
kernel through its loopback HTTP API. It checks both revisions, compile
diagnostics and editor lines, timeout, cancellation, refused effects, and
allowed effects. The local HTTP tests use fake Scala CLI and Java executables:
`python3 -m unittest discover -s "$skill/tests" -v`.
They also verify that `start-kernel.py` leaves the server running after the
launcher exits and returns a usable URL, PID, and log paths.

For Scala 2 / cats-effect 2, use `prepare_fixture.py --legacy`, read
`.validation/revisions-ce2.json`, preflight module `legacy`, and pass the resulting
JSON through `validate_fixture.py --preflight <json> --output-dir .validation/ce2`.
The same named effect confirmations apply; add `--effects` after approval.

For browser checks, install Playwright in the development directory only:

```bash
npm install --prefix .validation/browser --no-save --package-lock=false --ignore-scripts playwright
node "$skill/tests/browser-check.cjs" <playwright-module-path> <chromium-executable>
```

This launches an offline browser, selects every combination in each cell in
comparison/head-only/no-diff pages, checks recorded output and changed markers,
answers every quiz question from its evidence, verifies that generated drivers
are absent from the page, and checks mobile overflow. It fails on external
requests or script errors and saves
desktop/mobile screenshots in `.validation`. Fixture and browser files remain
outside the skill; do not commit generated classpaths, driver caches, or pages.
When `.validation/preflight.json` exists, the same browser check also starts a
fake local kernel, edits a live cell, checks both columns and compiler line
mapping, and verifies the unauthorized banner.
