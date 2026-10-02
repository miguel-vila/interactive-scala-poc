# Scala diff explanation skill

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

## Required tools

Python 3, git, Java, sbt with thin-client support, and scala-cli must be on PATH.
The CLI release must support the target module's exact Scala version. Preflight
can select a separate release with `--cli-version` without replacing the default.
