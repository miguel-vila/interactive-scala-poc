# Live kernel contracts

When the skill starts live mode, use `start-kernel.py` with the same options
as `kernel.py`. It launches a detached kernel, waits for the startup JSON,
prints that JSON with a `launcherLog` path, and exits. Give the user the
printed URL and PID. `kill -TERM <pid>` stops that detached kernel. The
launcher keeps its startup stdout and stderr in a private temporary folder;
`--temp-dir` places that folder and the kernel's runtime files under the given
directory. The kernel also writes API requests to its own `log` path. A person
who runs `kernel.py` in a foreground terminal can stop it with Ctrl-C.

`kernel.py --page <html> [--preflight <json>] [--allow-effects]
[--idle-minutes 30] [--max-timeout-seconds 60] [--port 0]
[--no-bloop] [--temp-dir <parent>]`

Run it only when the reader asks for live mode. It prints one JSON line with
`ok`, a loopback URL containing a fragment token, process id, temporary and log
paths, effect mode, idle limit, and available revision shas. Open that URL.
It reads the page HTML, provenance, and cell IDs once at startup. Finish the
page and browser checks before launch; after any page rebuild, stop and restart
the kernel with the updated page and matching preflight, then use its new URL.

SIGTERM stops a detached kernel; it also exits after 30 idle minutes by
default. Bloop may remain after exit; `scala-cli bloop exit` stops it manually.

The kernel uses an explicit `--preflight` path first, else the path recorded in
`page-provenance`. It verifies head/base shas, head `workingTreeHash`, module,
`ok` and build verdicts, and that `tempDir` and all successful revisions'
classpath files exist. A failed base remains head only; a failed head stops
startup. Missing or mismatched input returns a diagnostic naming the page's
shas and module, with the HTML skill's `preflight.py` command and `--preflight`
instruction. A working-tree page requires its original preflight. The kernel
never rebuilds revisions. It detects fs2-core from each revision's classpath
file at startup. `--no-bloop` uses Scala CLI's `--server=false` path.

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
| `GET /api/status` | bearer token | Disk page hash, `restartCommand`, revisions, effect mode, queue and warm state |
| `POST /api/runs` | `{cellId, source, timeoutSeconds?}` | 202 `{runId}`; 400 invalid source, 413 oversized body, 429 full queue |
| `GET /api/runs/{id}?wait=25` | bearer token | Run state and each revision's phase, kind, render, output, diagnostic, editor lines, duration and driver source; the page does not display driver source |
| `POST /api/runs/{id}/cancel` | `{}` | `{ok}`; kills an active compiler or JVM process group |

Runs are serial; the queue holds four. Each press compiles and runs head,
then base, with a fresh JVM per revision. Results appear as each revision
finishes. The default run timeout is 5 seconds; the UI offers 5, 15, and 60
seconds, bounded by `--max-timeout-seconds`. Compilation has its own 300-second
limit. Runtime stdout and stderr are capped at 64 KB. Compile errors retain
verbatim diagnostics; `lines` separately maps compiler locations to editor
lines. `kind` is one of `value`, `throwable`, `timeout`, `compileError`,
`refused`, or `cancelled`. Live results never replace baked rows or quiz data.

Automated kernel tests and fixture validation exercise the API separately.

## Page provenance and serving

The HTML builder emits exactly one block:

```html
<script type="application/json" id="page-provenance">{...}</script>
```

Its JSON carries `pageVersion: 2`, `projectDir`, `module`, `preflight` (absolute
path or null), `head: {sha, workingTreeHash}`, `base: {sha}` or null,
`scalaVersion`, and `toolchain: {command}`. Legacy `live-provenance` pages and
unknown versions are refused with an instruction to rebuild with the current
`explain-scala-diff-html`.

At startup the kernel reads its own `references/live.html` and inserts it before
the final `</body>` in the served bytes, after the recorded console script.
It never modifies the page on disk. `status.page.sha256` hashes that disk file.
`status.restartCommand` is the kernel command with resolved page and preflight
paths and its flags, quoted with `shlex.join`. The browser shows it when a
connected kernel becomes unavailable. A saved served page stays inert under
`file://`; controls activate only under `http:` with a token.

## DOM consumed by the fragment

The sibling HTML skill supplies:

- `<div class="scala-cell" data-cell="<id>">`, where ids match `[a-z][a-z0-9-]*`.
- One `<script type="application/json" id="cell-<id>">` containing `call`,
  `imports`, `setup`, `params`, `rows`, and `results`.
- The console's first direct `<pre>` child: selected bindings, a blank line,
  and the call. The fragment uses it to prefill the editor.
- `<section id="code">` for the scratch cell; `scala-banner` and `scala-status`
  classes; CSS variables `--line`, `--ink`, `--panel`, `--del`, and `--muted`.
- The recorded `console.html` script before `</body>` and exactly one known
  `page-provenance` block.

## Installed sibling

Both `kernel.py` and `live_driver.py` resolve shared HTML scripts with:

```python
HTML_SCRIPTS = Path(__file__).resolve().parents[2] / "explain-scala-diff-html" / "scripts"
```

They import `ContractError`, `read_json`, and the rendering `HARNESS` from that
sibling. This works in the checkout, skills CLI symlink installations, and copy
installations. Install/update both skills from the same repository revision.
There is no environment override or directory search. If `scala_diff.py` is
absent there, startup exits 1 and prints one JSON line:

```json
{"ok": false, "diagnostic": "explain-scala-diff-html is not installed next to this skill at <path>. Install it with: npx skills add miguel-vila/interactive-scala-poc --skill explain-scala-diff-html"}
```
