# Scala diff explanation skill

## Work through GitHub issues with Codex

From this repository, run `./run-github-issues.sh`. It starts a fresh Codex CLI
session for each eligible issue, stopping after ten runs, when there are no more
eligible issues, when Codex needs your decision, or on an error. Each completed
run commits its changes and closes its GitHub issue. Install and authenticate
`codex` and `gh` first; Python 3 is also required. The script grants Codex full
filesystem, command, and network access for each run. It exits with status 2
when your decision is needed, 3 when Codex is blocked, and 1 on execution or
verification errors.

Implementation of the October 1 handoff lives in
[skills/explain-scala-diff-html](skills/explain-scala-diff-html/SKILL.md).
The production scripts use Python 3's standard library, git, sbt's thin client,
Java, and scala-cli. The HTML scaffold is bundled with the skill.

The skill bundles contract tests and a reproducible two-revision Scala fixture;
see [tests/README.md](skills/explain-scala-diff-html/tests/README.md).
Generated validation projects, classpaths, grids, pages and screenshots live in
`.validation/`. Real projects are built in external temporary worktrees.

```bash
python3 -m unittest discover -s skills/explain-scala-diff-html/tests -v
```

## Install from this folder

From this repository, install for Codex:

```bash
python3 install.py
```

This creates `~/.agents/skills/explain-scala-diff-html` as a symlink to
`skills/explain-scala-diff-html` in this checkout. This folder is the single
source of truth: edits here are immediately available through the installed
skill. Keep the checkout at its installed path. Installation is offline and
does not require the SKILLS repository or another skill.

For Claude Code, use `python3 install.py --agent claude-code`, which links into
`~/.claude/skills`. To choose a different skills directory, use
`python3 install.py --dest /path/to/skills`.

Rerunning the installer is safe. It leaves the existing link in place and
refuses to overwrite any other installation with the same name.

## Use in a Scala project

Start Codex in the sbt project you want to explain, then send:

```text
$explain-scala-diff-html Explain HEAD~1..HEAD, using sbt module core.
```

Replace `core` with the owning sbt project id. A branch, commit range, PR, or
local working changes can be the target. You can also ask for a no-diff example.
Codex supports explicit skill mentions with `$` or the `/skills` selector; see
the [official skill documentation](https://learn.chatgpt.com/docs/build-skills).
If the newly installed skill does not appear, restart Codex.

The skill builds temporary worktrees, probes proposed calls, and generates
`~/explanations/<date>-explanation-<slug>.html`. Functions that perform effects
require explicit confirmation naming each function before any probe or grid.

Every generated page works offline. To edit a Scala snippet and run it against
both revisions, start the local kernel after building and checking the page:

```bash
python3 skills/explain-scala-diff-html/scripts/kernel.py --page /path/to/page.html --preflight /path/to/preflight.json
```

Open the URL printed by the kernel. It contains a one-time token in the URL
fragment. The kernel serves only on loopback, runs edited Scala with your local
user privileges, and exits after 30 idle minutes. Press Ctrl-C to stop it.
The kernel reads the HTML, provenance, and cell IDs once at startup. If you
rebuild the page while it is running, stop and restart the kernel to serve the
new page, then open the new URL.

When Codex starts live mode for you, it uses `scripts/start-kernel.py` to keep
the kernel running after the tool command ends. It reports the URL and PID;
stop that process with `kill -TERM <pid>`.
Automatic execution of a returned IO, Resource, or Stream is disabled by
default; add `--allow-effects` when you intend to run those values. Edited
Scala can still perform side effects directly. If the preflight files are gone,
the kernel can rebuild committed revisions with sbt. A page made from
uncommitted changes needs its original preflight files. Follow the
[skill's live-mode instructions](skills/explain-scala-diff-html/SKILL.md#live-mode)
for the preflight session directory.

## Required tools

See the [skill requirements](skills/explain-scala-diff-html/SKILL.md#0-check-the-toolchain-and-create-a-session-directory)
for supported JVM projects, versions, and browser-check dependencies.
