# Validation

The production scripts need only Python's standard library. Tests use unittest;
the browser acceptance check uses Node.js, Playwright, and Chromium.
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

## Browser checks

Playwright is a browser-test dependency, not a project dependency. Install it
outside the target repository. If no Chromium is available, install one into the
same disposable directory. Run these commands from a writable development
directory, with `skill` set as above and `page` set to the completed HTML path:

```bash
browser_dir=$(mktemp -d)
npm install --prefix "$browser_dir" --no-save --package-lock=false --ignore-scripts playwright
PLAYWRIGHT_BROWSERS_PATH="$browser_dir/browsers" "$browser_dir/node_modules/.bin/playwright" install chromium
PLAYWRIGHT_BROWSERS_PATH="$browser_dir/browsers" node "$skill/tests/browser-check.cjs" "$browser_dir/node_modules/playwright" "" --page "$page"
```

If Chromium is already installed, omit the browser download and pass its
executable path in place of `""`. Omit `--page "$page"` to check the fixture
pages in `.validation`; when `.validation/preflight.json` exists, that mode also
starts a fake local kernel and checks live editing, compiler line mapping, and
the unauthorized banner.

The check selects every recorded combination, verifies rendered output and
changed markers, answers the quiz, checks phone-width overflow, and fails on
external requests or script errors. It saves desktop and mobile screenshots in
`.validation`; inspect both visually. Keep fixture, browser, and screenshot files
outside the skill and target project. Do not commit generated classpaths, driver
caches, or pages.
