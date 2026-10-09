# Scala diff explanation skills

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
The production scripts use Python 3's standard library, git, Java, and
scala-cli. Preflight uses sbt's thin client for sbt builds, or supplied
classpaths and Scala versions for other JVM Scala builds. The HTML scaffold is
bundled with the skill.

Repository tests include contract tests and a reproducible two-revision Scala fixture;
see [tests/README.md](tests/README.md).
Generated validation projects, classpaths, grids, pages and screenshots live in
`.validation/`. Real projects are built in external temporary worktrees.

```bash
python3 -m unittest discover -s tests -t .
```

## Install

```bash
npx skills add miguel-vila/interactive-scala-poc          # choose skills and agents
npx skills add miguel-vila/interactive-scala-poc -g --all # both skills, every agent, user level
npx skills add /path/to/this/checkout -g                  # from a local checkout
```

`explain-scala-diff-html` builds offline pages. `explain-scala-diff-live` serves
those pages with controls for editing and running Scala. Install both side by
side from the same repository revision for live use. Runtime resources stay
inside each skill; tests and fixtures live at the repository root.

## Use in a Scala project

Start Codex in the JVM Scala project you want to explain. For an sbt project,
send:

```text
$explain-scala-diff-html Explain HEAD~1..HEAD, using sbt module core.
```

Replace `core` with the owning sbt project id. A branch, commit range, PR, or
local working changes can be the target. You can also ask for a no-diff example.
For other build tools, supply classpaths and Scala versions as described in the
[preflight contract](skills/explain-scala-diff-html/references/contracts.md#preflight).

Codex supports explicit skill mentions with `$` or the `/skills` selector; see
the [official skill documentation](https://learn.chatgpt.com/docs/build-skills).
If the newly installed skill does not appear, restart Codex.

The skill builds temporary worktrees, probes proposed calls, and generates
`~/explanations/<date>-explanation-<slug>.html`. Functions that perform effects
require explicit confirmation naming each function before any probe or grid.

Every generated page works offline. To edit a Scala snippet and run it against
both revisions, start the local kernel after building and checking the page:

```bash
python3 skills/explain-scala-diff-live/scripts/kernel.py --page /path/to/page.html --preflight /path/to/preflight.json
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
Scala can still perform side effects directly. The kernel needs the matching
preflight report, its worktrees, and classpath files. If those are gone, rerun
the HTML workflow's preflight for the page's shas and module. A page from
uncommitted changes requires its original preflight or a newly built page.
Use `$explain-scala-diff-live` with a completed page and preflight, or with a
diff target to build and check a page before launch. See the
[live skill](skills/explain-scala-diff-live/SKILL.md).

## Required tools

See the [skill requirements](skills/explain-scala-diff-html/SKILL.md#0-check-the-toolchain-and-create-a-session-directory)
for supported JVM projects, versions, and browser-check dependencies.
