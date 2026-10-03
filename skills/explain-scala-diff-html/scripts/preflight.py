"""Resolve an sbt module in isolated git worktrees; print one JSON report."""
import argparse
import hashlib
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile

from scala_diff import ContractError, command, emit, fs2_core


def git(project, *args):
    code, out, err = command(["git", "-C", str(project), *args])
    if code:
        raise ContractError(err + out)
    return out.strip()


def create_control_clone(project, control, sha):
    code, out, err = command(["git", "clone", "--shared", "--no-checkout", str(project), str(control)])
    if code:
        raise ContractError(err + out)
    # JGit (used by sbt-git) follows linked worktrees to their common git dir.
    # Give that control repository a real worktree and index before sbt starts.
    git(control, "checkout", "--detach", sha)


def sbt(worktree, *tasks):
    try:
        return command(["sbt", "-error", "--client", ";" + ";".join(tasks)], cwd=worktree, timeout=600)
    finally:
        code, out, err = command(["sbt", "--client", "shutdown"], cwd=worktree, timeout=60)
        if code:
            raise ContractError(f"sbt client shutdown failed in {worktree}: {err + out}")


def clean_lines(text):
    text = re.sub(r'\x1b\[[0-9;]*[A-Za-z]', '', text)
    return [re.sub(r'^\[(?:info|success)\]\s*', '', line).strip()
            for line in text.splitlines() if line.strip()]


def modules_from(text):
    modules = []
    for line in clean_lines(text):
        match = re.fullmatch(r'(?:\*\s*)?([\w.-]+)', line)
        if match:
            modules.append(match.group(1))
    return modules


def cats_effect(classpath):
    matches = re.findall(r'(?:^|[/\\])cats-effect_(?:2\.\d+|3)-([23]\.[^/\\:]+)\.jar', classpath)
    if not matches:
        return {"present": False, "major": None, "version": None}
    versions = sorted(set(matches))
    if len(versions) != 1:
        raise ContractError("Multiple cats-effect versions in the exported classpath: " + ", ".join(versions))
    return {"present": True, "major": int(versions[0][0]), "version": versions[0]}


def build_revision(worktree, module, sha, root, label):
    revision = {"sha": sha, "worktree": str(worktree), "builds": False}
    code, out, err = sbt(worktree, f"print {module}/scalaVersion", f"export {module}/Runtime/fullClasspath")
    log = root / f"sbt-{label}.log"
    log.write_text(err + out)
    revision["buildLog"] = str(log)
    lines = clean_lines(out)
    versions = [line for line in lines if re.fullmatch(r'\d+\.\d+\.\d+(?:[-\w.]*)?', line)]
    if versions:
        revision["scalaVersion"] = versions[-1]
    if code:
        revision["diagnostic"] = err + out
        return revision
    paths = [line for line in lines if line.startswith("/") and (os.pathsep in line or Path(line).exists())]
    if not versions or not paths:
        revision["diagnostic"] = out + err
        revision["hint"] = "sbt did not export a Scala version and full classpath"
        return revision
    classpath = paths[-1]
    missing = [p for p in classpath.split(os.pathsep) if not Path(p).exists()]
    if missing:
        revision["diagnostic"] = "Exported classpath entries do not exist: " + ", ".join(missing)
        return revision
    cp = root / f"cp-{label}.txt"
    cp.write_text(classpath + "\n")
    revision.update(builds=True, scalaVersion=versions[-1], classpathFile=str(cp),
                    catsEffect=cats_effect(classpath), fs2=fs2_core(classpath))
    return revision


def cli_compatibility_warnings(cli_command, root, revisions):
    versions = {}
    for label, revision in revisions:
        if revision and revision.get("scalaVersion"):
            versions.setdefault(revision["scalaVersion"], []).append(label)
    if not versions:
        return []

    source = root / "ScalaVersionCheck.scala"
    source.write_text("// Check whether this Scala CLI can compile the selected Scala version.\n")
    warnings = []
    for version, labels in versions.items():
        code, out, err = command(cli_command + ["compile", "--server=false", "--jvm", "system",
                                                 "--scala", version, str(source)], cwd=root)
        if code:
            diagnostic = (err + out).strip() or f"Scala CLI exited with status {code}"
            warnings.append(f"{', '.join(labels)}: Scala CLI could not compile Scala {version}: "
                            f"{diagnostic}\nTry --cli-version <release>; see the Scala CLI compatibility table.")
    return warnings


def preflight(args):
    for tool in ("git", "sbt", "scala-cli", "java"):
        if not shutil.which(tool):
            raise ContractError(f"Missing tool: {tool}")
    cli_command = ["scala-cli"]
    if args.cli_version:
        if not re.fullmatch(r'\d+\.\d+\.\d+(?:[-\w.]*)?', args.cli_version):
            raise ContractError("--cli-version must be an explicit Scala CLI release")
        cli_command += ["--cli-version", args.cli_version]
    code, cli_version, err = command(cli_command + ["version"])
    if code:
        raise ContractError(err + cli_version)
    version_match = re.search(r'Scala CLI version:\s*([\w.-]+)', cli_version)
    project = Path(git(args.project_dir, "rev-parse", "--show-toplevel")).resolve()
    if not (project / "build.sbt").exists() and not (project / "project").exists():
        raise ContractError(f"No sbt build at {project}")
    sha = git(project, "rev-parse", "--verify", (args.head or "HEAD") + "^{commit}")
    base_sha = git(project, "rev-parse", "--verify", args.base + "^{commit}") if args.base else None
    root = Path(tempfile.mkdtemp(prefix="scala-diff-", dir=args.temp_dir)).resolve()
    # git worktree metadata goes in this private control clone, never in the source
    # repo. All sbt-generated target/project files also stay in these worktrees.
    control = root / "control"
    create_control_clone(project, control, sha)
    head = root / "head"
    git(control, "worktree", "add", "--detach", str(head), sha)
    patch_hash = None
    if not args.head:
        code, patch, err = command(["git", "-C", str(project), "diff", "--binary", "HEAD", "--"])
        if code:
            raise ContractError(err)
        untracked = git(project, "ls-files", "--others", "--exclude-standard", "-z").split("\0")
        digest = hashlib.sha256(patch.encode())
        if patch:
            patch_file = root / "working-tree.patch"
            patch_file.write_text(patch)
            git(head, "apply", str(patch_file))
        for name in filter(None, untracked):
            source, target = project / name, head / name
            if source.is_symlink():
                raise ContractError(f"Untracked symlink cannot be snapshotted: {name}; commit it or select --head")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            digest.update(name.encode() + b"\0" + source.read_bytes())
        if patch or any(untracked):
            patch_hash = digest.hexdigest()
    code, out, err = sbt(head, "projects")
    if code:
        raise ContractError(err + out)
    modules = modules_from(out)
    if not args.module:
        if len(modules) != 1:
            raise ContractError("Choose --module for the changed file. Modules: " + ", ".join(modules))
        module = modules[0]
    else:
        module = args.module
    if not re.fullmatch(r'[\w.-]+', module) or module not in modules:
        raise ContractError(f"Unknown module {module!r}. Modules: {', '.join(modules)}")
    head_info = build_revision(head, module, sha, root, "head")
    if patch_hash:
        head_info["workingTreeHash"] = patch_hash
    base_info = None
    if base_sha:
        base = root / "base"
        git(control, "worktree", "add", "--detach", str(base), base_sha)
        base_info = build_revision(base, module, base_sha, root, "base")
    warnings = []
    for label, revision in (("head", head_info), ("base", base_info)):
        if revision and revision.get("builds"):
            jars = Path(revision["classpathFile"]).read_text().strip().split(os.pathsep)
            suffixes = set(re.findall(r'_(2\.\d+|3)-', " ".join(jars)))
            if len(suffixes) > 1:
                warnings.append(label + ": mixed cross-version suffixes: " + ", ".join(Path(j).name for j in jars if re.search(r'_(2\.\d+|3)-', j)))
        elif revision:
            warnings.append(label + " did not build; " + ("head-only page required" if label == "base" else "stop"))
    warnings.extend(cli_compatibility_warnings(cli_command, root, (("head", head_info), ("base", base_info))))
    return {"ok": head_info["builds"], "projectDir": str(project), "module": module, "modules": modules,
            "scalaVersion": head_info.get("scalaVersion"), "catsEffect": head_info.get("catsEffect", {"present": False, "major": None, "version": None}),
            "head": head_info, "base": base_info,
            "toolchain": {"scalaCli": version_match.group(1) if version_match else cli_version.strip(), "command": cli_command, "sbtClient": True},
            "cacheDir": str(root / "drivers"), "tempDir": str(root), "warnings": warnings}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project_dir")
    parser.add_argument("--module")
    parser.add_argument("--base")
    parser.add_argument("--head", help="Committed head revision; omit to snapshot local changes")
    parser.add_argument("--cli-version", help="Explicitly select a separate Scala CLI release without changing the system installation")
    parser.add_argument("--temp-dir", help="Parent for disposable worktrees (default: system temp)")
    args = parser.parse_args()
    try:
        report = preflight(args)
        emit(report)
        return 0 if report["ok"] else 1
    except (ContractError, OSError) as error:
        emit({"ok": False, "diagnostic": str(error)})
        return 1


if __name__ == "__main__":
    sys.exit(main())
