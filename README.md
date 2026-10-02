# Scala diff explanation skill

Implementation of the October 1 handoff lives in
[skills/explain-scala-diff-html](skills/explain-scala-diff-html/SKILL.md).
The production scripts use Python 3's standard library, git, sbt's thin client,
Java, and scala-cli. The parent explain-diff-html scaffold is shared by path.

The skill bundles contract tests and a reproducible two-revision Scala fixture;
see [tests/README.md](skills/explain-scala-diff-html/tests/README.md).
Generated validation projects, classpaths, grids, pages and screenshots live in
`.validation/`. Real projects are built in external temporary worktrees.

```bash
python3 -m unittest discover -s skills/explain-scala-diff-html/tests -v
```

The fork can be installed as a sibling of explain-diff-html in the SKILLS repo.
Use `--cli-version` in preflight to select a separately downloaded CLI release
when the installed release does not support the target project's Scala version.

## Install from this folder

Install the shared parent scaffold and the Scala skill into the same global
skill directory. From this repository, using the existing local SKILLS checkout:

```bash
npx skills add ~/repos/SKILLS --skill explain-diff-html -g -a codex -y
npx skills add . --skill explain-scala-diff-html -g -a codex -y
```

The [skills CLI](https://github.com/vercel-labs/skills) accepts local directories.
The first command provides the sibling `explain-diff-html` scaffold; the second
installs this folder's implementation. The parent is a separate dependency and
is not bundled in this public repository. Use your own local parent checkout
if it is stored elsewhere. For Claude Code, replace `codex` with `claude-code`.

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
