"""Resolve Scala versions and classpaths in isolated git worktrees; print one JSON report."""
import argparse
import hashlib
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile

from scala_diff import ContractError, command, emit


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


def supplied_revision(worktree, sha, root, label, classpath_file, scala_version):
    source = Path(classpath_file).expanduser().resolve()
    if not source.is_file():
        raise ContractError(f"Classpath file does not exist: {source}")
    classpath = source.read_text().strip()
    if not classpath:
        raise ContractError(f"Classpath file is empty: {source}")
    paths = classpath.split(os.pathsep)
    if any(not Path(path).is_absolute() for path in paths):
        raise ContractError(f"Classpath entries must be absolute paths: {source}")
    missing = [path for path in paths if not Path(path).exists()]
    if missing:
        raise ContractError("Supplied classpath entries do not exist: " + ", ".join(missing))
    target = root / f"cp-{label}.txt"
    shutil.copy2(source, target)
    return {"sha": sha, "worktree": str(worktree), "builds": True,
            "scalaVersion": scala_version, "classpathFile": str(target),
            "catsEffect": cats_effect(classpath)}


def build_revision(worktree, module, sha, root, label, build_root=Path(".")):
    revision = {"sha": sha, "worktree": str(worktree), "builds": False}
    code, out, err = sbt(worktree / build_root, f"print {module}/scalaVersion", f"export {module}/Runtime/fullClasspath")
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
                    catsEffect=cats_effect(classpath))
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
    head_cp = getattr(args, "classpath_file", None)
    head_version = getattr(args, "scala_version", None)
    base_cp = getattr(args, "base_classpath_file", None)
    base_version = getattr(args, "base_scala_version", None)
    for label, classpath_file, scala_version in (("head", head_cp, head_version), ("base", base_cp, base_version)):
        if bool(classpath_file) != bool(scala_version):
            raise ContractError(f"{label} needs both a classpath file and a Scala version")
        if scala_version and not re.fullmatch(r'\d+\.\d+\.\d+(?:[-\w.]*)?', scala_version):
            raise ContractError(f"Invalid {label} Scala version: {scala_version}")
    if base_cp and not args.base:
        raise ContractError("--base-classpath-file requires --base")
    needs_sbt = not head_cp or bool(args.base and not base_cp)
    for tool in (("git", "sbt", "scala-cli", "java") if needs_sbt else ("git", "scala-cli", "java")):
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
    build_root_arg = Path(getattr(args, "build_root", None) or ".")
    build_root_path = (project / build_root_arg).resolve()
    try:
        build_root = build_root_path.relative_to(project)
    except ValueError as error:
        raise ContractError(f"Build root must be inside {project}: {build_root_path}") from error
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
    base = None
    if base_sha:
        base = root / "base"
        git(control, "worktree", "add", "--detach", str(base), base_sha)
    if needs_sbt:
        discovery = head if not head_cp else base
        sbt_root = discovery / build_root
        if not (sbt_root / "build.sbt").exists() and not (sbt_root / "project").exists():
            raise ContractError(f"No sbt build at {sbt_root}")
        code, out, err = sbt(sbt_root, "projects")
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
    else:
        module = args.module or "root"
        if not re.fullmatch(r'[\w.-]+', module):
            raise ContractError(f"Invalid module {module!r}")
        modules = [module]
    head_info = (supplied_revision(head, sha, root, "head", head_cp, head_version) if head_cp else
                 build_revision(head, module, sha, root, "head", build_root))
    if patch_hash:
        head_info["workingTreeHash"] = patch_hash
    base_info = None
    if base_sha:
        base_info = (supplied_revision(base, base_sha, root, "base", base_cp, base_version) if base_cp else
                     build_revision(base, module, base_sha, root, "base", build_root))
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
            "toolchain": {"scalaCli": version_match.group(1) if version_match else cli_version.strip(), "command": cli_command, "sbtClient": bool(needs_sbt)},
            "cacheDir": str(root / "drivers"), "tempDir": str(root), "warnings": warnings}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project_dir")
    parser.add_argument("--module")
    parser.add_argument("--build-root", help="sbt build directory relative to the git root (default: git root)")
    parser.add_argument("--base")
    parser.add_argument("--head", help="Committed head revision; omit to snapshot local changes")
    parser.add_argument("--classpath-file", help="Head classpath file; use with --scala-version to skip sbt")
    parser.add_argument("--scala-version", help="Head Scala version for --classpath-file")
    parser.add_argument("--base-classpath-file", help="Base classpath file; use with --base-scala-version to skip sbt")
    parser.add_argument("--base-scala-version", help="Base Scala version for --base-classpath-file")
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
