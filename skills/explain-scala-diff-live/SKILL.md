---
name: explain-scala-diff-live
description: Serve a verified Scala explanation page with a local kernel so the reader can edit and run snippets against its revisions. Use when the user requests live Scala editing, either for a completed explanation page or a Scala diff target.
---

# Explain Scala Diff (Live)

Serve a completed offline page through the local kernel. Install this skill and
`explain-scala-diff-html` side by side from the same repository revision.
Read [the contracts](references/contracts.md) when an input is rejected or a
field's meaning is unclear.

## Prepare the page and toolchain

There are two entry forms:

- A completed HTML page and its matching preflight: use their absolute paths.
  The kernel can also use the preflight path recorded in the page.
- A diff target (commit, branch, PR, local changes, or no-diff demonstration):
  follow the sibling [HTML skill's workflow](../explain-scala-diff-html/SKILL.md)
  through its browser check first. Use the resulting page and preflight.

Check Python 3.9 or newer, Java, and the Scala CLI command recorded in preflight
before launching. Do not upgrade tools automatically. The HTML workflow also
checks git, the target build tools, and browser-check dependencies when needed.
Keep the preflight report, its `tempDir`, and every exported classpath for the
whole kernel session. The kernel verifies revisions, working-tree hash, module,
and build verdicts; it requires a usable matching preflight. If files are gone,
run the HTML skill's `preflight.py` for the page's shas and module and pass the
result as `--preflight`. A working-tree page requires its original preflight;
if that is gone, rebuild the page from a new snapshot through the HTML workflow.

## Start and report

Finish the page and its browser checks before launching. Use absolute paths;
shell variables must be set in the same command that uses them:

```bash
skill=/absolute/path/to/explain-scala-diff-live
session_dir=/absolute/path/to/session
page=/absolute/path/to/page.html
python3 "$skill/scripts/start-kernel.py" --page "$page" --preflight "$session_dir/preflight.json" --temp-dir "$session_dir"
```

The launcher waits for startup JSON and exits while the kernel remains running.
On `ok: true`, report the exact `url`, `pid`, and `log` path. Open the printed
URL; its fragment contains the token. Tell the user to stop the kernel with
`kill -TERM <pid>`. It also exits after 30 idle minutes by default. On failure,
report the diagnostic and `launcherLog` path.

Pass `--allow-effects` only when the user requests that mode. The browser shows
whether automatic execution of returned IO, Resource, or Stream is enabled.
Edited Scala runs with the reader's local privileges.

The kernel reads the page, provenance, and cell IDs once at startup and injects
its controls into the served response. After any page rebuild, stop the kernel,
start it with the rebuilt page and matching preflight, and report the new URL.
Keep the session files until the kernel stops.

Never call the kernel's `/api/*` routes as part of an explanation. For agent-run
examples, use the HTML skill's `run-grid.py` and its named effect confirmation
gate. Live results never become narrative or quiz evidence; those claims use
recorded grid rows.
