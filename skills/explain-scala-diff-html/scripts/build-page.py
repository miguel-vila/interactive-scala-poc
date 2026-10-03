#!/usr/bin/env python3
"""Fill the bundled scaffold, embed offline grids and derive verified quiz answers."""
import argparse
from datetime import date
from html import escape
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import sys

from scala_diff import ContractError, emit, read_json


def script_json(value, indent=None):
    return json.dumps(value, ensure_ascii=True, indent=indent).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def answer(result):
    return result["kind"] + ": " + result["render"]


def verified_quiz(grid):
    candidates = []
    for cell_id, cell in grid["cells"].items():
        for row in cell["rows"]:
            result = cell["results"][row["key"]]
            if result["head"]["kind"] != "compileError":
                candidates.append((not result["differs"], cell_id, cell, row, result))
    candidates.sort(key=lambda row: row[0])
    if len(candidates) < 5:
        raise ContractError("The verified quiz requires at least five recorded rows; expand an admissible grid before building the page")
    questions = []
    for _, cell_id, cell, row, result in candidates[:5]:
        right = answer(result["head"])
        alternatives = []
        # Distractors are other observed results, including the old behaviour.
        for item in [result.get("base")] + [c[4]["head"] for c in candidates]:
            if item and item["kind"] != "compileError" and answer(item) != right and answer(item) not in alternatives:
                alternatives.append(answer(item))
        if not alternatives:
            # This is a quiz claim, not a fabricated execution result.
            alternatives = ["No result was recorded for this input."]
        params = ", ".join(p["name"] + "=" + json.dumps(v, ensure_ascii=False) for p, v in zip(cell["params"], row["values"])) or "no parameters"
        options = [{"text": right, "feedback": "The recorded head result for " + params + " was " + right + ".", "correct": True}]
        options += [{"text": text, "feedback": "The recorded head result for this input was " + right + "."} for text in alternatives[:3]]
        questions.append({"stem": "What does " + cell["call"] + " produce at head with " + params + "?", "options": options,
                          "evidence": {"cellId": cell_id, "rowKey": row["key"], "revision": "head"}})
    return questions


class PageChecks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = []
        self.anchors = []
        self.errors = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if "id" in attrs:
            self.ids.append(attrs["id"])
        if tag == "a" and attrs.get("href", "").startswith("#"):
            self.anchors.append(attrs["href"][1:])
        for key in ("src", "srcset", "poster", "data", "action"):
            if key in attrs:
                self.errors.append(f"External or embedded resource attribute {tag}.{key}; inline the content")
        if tag == "link":
            self.errors.append("External link resource; inline styles")


def validate_page(page, quiz):
    parser = PageChecks()
    parser.feed(page)
    if len(set(parser.ids)) != len(parser.ids):
        parser.errors.append("Repeated HTML ids")
    parser.errors += ["Missing anchor " + anchor for anchor in parser.anchors if anchor not in parser.ids]
    if re.search(r'\b(?:fetch|XMLHttpRequest|WebSocket|EventSource)\s*\(|@import\b|url\s*\(', page):
        parser.errors.append("Network-capable JavaScript or CSS resource")
    if "<pre" not in page or "white-space:" not in page or "shuffled(q.options)" not in page:
        parser.errors.append("Scaffold code/overflow/quiz checks failed")
    if len(quiz) != 5 or any(sum(o.get("correct", False) for o in q["options"]) != 1 for q in quiz):
        parser.errors.append("The quiz must have five questions, each with exactly one correct option")
    if parser.errors:
        raise ContractError("; ".join(parser.errors))


def footer(provenance):
    details = []
    for name in ("head", "base"):
        revision = provenance.get(name)
        if not revision:
            continue
        ce = revision.get("catsEffect", provenance.get("catsEffect", {}))
        details.append(f"<li>{name}: <code>{escape(revision['sha'])}</code>; Scala {escape(str(revision.get('scalaVersion', 'unavailable')))}; cats-effect {escape(str(ce.get('version') or 'absent'))}</li>")
        if revision.get("workingTreeHash"):
            details.append("<li>Working tree snapshot: <code>" + escape(revision["workingTreeHash"]) + "</code></li>")
        if revision.get("diagnostic"):
            details.append("<li>" + name + " build diagnostic:<pre>" + escape(revision["diagnostic"]) + "</pre></li>")
    for dropped in provenance.get("droppedCells", []):
        details.append("<li>Dropped cell " + escape(dropped["cellId"]) + "<pre>" + escape(dropped.get("diagnostic") or "No diagnostic") + "</pre></li>")
    effects = provenance.get("effects", [])
    for effect in effects:
        details.append("<li>Effects performed by " + escape(effect["function"]) + " in cell " + escape(effect["cellId"]) + " (" + escape(effect["effect"]) + "), with explicit user confirmation.</li>")
    if not effects:
        details.append("<li>No effectful cells were executed.</li>")
    for warning in provenance.get("warnings", []):
        details.append("<li>" + escape(warning) + "</li>")
    return '<footer id="provenance"><h2>Execution provenance</h2><p>Module: <code>' + escape(provenance["module"]) + '</code></p><ul>' + "".join(details) + '</ul><details><summary>Complete preflight report</summary><pre>' + escape(json.dumps(provenance, ensure_ascii=False, indent=2)) + '</pre></details></footer>'


def live_provenance(provenance, preflight=None):
    return {"pageVersion": 1, "projectDir": provenance.get("projectDir", ""),
            "module": provenance["module"],
            "preflight": str(preflight) if preflight else None,
            "head": {"sha": provenance["head"]["sha"],
                     "workingTreeHash": provenance["head"].get("workingTreeHash")},
            "base": {"sha": provenance["base"]["sha"]} if provenance.get("base") else None,
            "scalaVersion": provenance.get("scalaVersion"),
            "kernelScript": str(Path(__file__).resolve().parent / "kernel.py"),
            "toolchain": {"command": provenance.get("toolchain", {}).get("command", ["scala-cli"])}}


def build_page(grid, narrative, scaffold=None, preflight=None):
    skill = Path(__file__).resolve().parents[1]
    expected = skill / "references/html-scaffold.html"
    scaffold = Path(scaffold) if scaffold else expected
    if not scaffold.exists():
        raise ContractError(f"HTML scaffold missing: {scaffold}. Reinstall or restore the skill's bundled scaffold: {expected}")
    page = scaffold.read_text()
    page = re.sub(r"<!--.*?-->", "", page, flags=re.S)
    page = re.sub(r"(?m)^(?://[^\n]*\n)+(?=const QUIZ = \[)", "", page)
    page = page.replace("TITLE", escape(narrative["title"]))
    page = re.sub(r'<p class="subtitle">.*?</p>', lambda _: '<p class="subtitle">' + escape(narrative.get("subtitle", "Recorded Scala behaviour before and after the change.")) + '</p>', page, count=1, flags=re.S)
    for section, title in (("background", "Background"), ("intuition", "Intuition"), ("code", "The code")):
        content = narrative.get(section)
        if not content:
            raise ContractError(f"Narrative requires an HTML fragment for {section}")
        if re.search(r"<\s*/?\s*(?:section|h2)\b", content, flags=re.I):
            raise ContractError(f"Narrative {section} must be inner HTML of the builder's section. Start with <h3> or <p>; do not include <section> or <h2> tags or duplicate scaffold ids")
        pattern = rf'<section id="{section}">[\s\S]*?</section>'
        page, count = re.subn(pattern, lambda _: f'<section id="{section}"><h2>{title}</h2>\n{content}\n</section>', page, count=1)
        if count != 1:
            raise ContractError(f"HTML scaffold is missing section {section}")
    data_blocks = []
    for cell_id, original in grid["cells"].items():
        placeholder = f'<div class="scala-cell" data-cell="{cell_id}"></div>'
        if placeholder not in page:
            raise ContractError(f"Narrative must anchor cell {cell_id} with {placeholder}")
        data = dict(original)
        data.pop("driverSource", None)
        data["baseFailed"] = bool(grid["provenance"].get("base") and not grid["provenance"]["base"]["builds"])
        data_blocks.append(f'<script type="application/json" id="cell-{cell_id}">' + script_json(data) + '</script>')
    provenance = grid["provenance"]
    if provenance.get("base") and not provenance["base"]["builds"]:
        banner = '<div class="scala-banner" role="status">The base revision did not build. Every console shows head only. The original diagnostic is in the provenance footer.</div>'
        page = page.replace('<nav class="toc">', banner + '\n<nav class="toc">', 1)
    if provenance.get("effects"):
        page = page.replace('<section id="code">', '<div class="callout edge"><p>Confirmed effects were run with <code>unsafeRunSync()</code> and a maximum five-second timeout per call. Cells below name the functions that performed effects.</p></div>\n<section id="code">', 1)
    quiz = verified_quiz(grid)
    # Keep the scaffold's option-shuffling code verbatim; replace only its data.
    quiz_js = script_json(quiz, indent=2).replace('"correct": true', 'correct: true')
    page, count = re.subn(r'const QUIZ = \[[\s\S]*?\];', lambda _: 'const QUIZ = ' + quiz_js + ';', page, count=1)
    if count != 1:
        raise ContractError("Cannot find the scaffold's QUIZ declaration")
    page = page.replace("<div id=\"quiz-list\"></div>", "<div id=\"quiz-list\"></div>" + '<details><summary>Recorded evidence for quiz answers</summary><pre>' + escape(json.dumps(quiz, ensure_ascii=False, indent=2)) + '</pre></details>')
    page = page.replace("</body>", "\n".join(data_blocks) + '\n' + footer(provenance) + '\n' + (skill / "references/console.html").read_text() + '\n</body>')
    validate_page(page, quiz)
    live = skill / "references/live.html"
    if not live.exists():
        raise ContractError(f"Live fragment missing: {live}")
    preflight_path = Path(preflight).expanduser().resolve() if preflight else None
    if preflight_path and not preflight_path.is_file():
        raise ContractError(f"Preflight file missing: {preflight_path}")
    block = '<script type="application/json" id="live-provenance">' + script_json(live_provenance(provenance, preflight_path)) + '</script>'
    before, closing = page.rsplit("</body>", 1)
    return before + block + "\n" + live.read_text() + "\n</body>" + closing


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--grid", required=True)
    parser.add_argument("--narrative", required=True)
    parser.add_argument("--slug", required=True)
    parser.add_argument("--output", help="Override the default ~/explanations path (useful for validation)")
    parser.add_argument("--scaffold", help="Override the bundled scaffold for development")
    parser.add_argument("--preflight", help="Record this preflight JSON path in the live kernel hint")
    args = parser.parse_args()
    try:
        if not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', args.slug):
            raise ContractError("slug must use lowercase kebab-case")
        grid = read_json(args.grid)
        page = build_page(grid, read_json(args.narrative), args.scaffold, args.preflight)
        output = Path(args.output).expanduser() if args.output else Path.home() / "explanations" / f"{date.today().isoformat()}-explanation-{args.slug}.html"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(page)
        emit({"ok": True, "path": str(output.resolve()), "cells": len(grid["cells"]), "quizQuestions": 5}, compact=True)
        return 0
    except (ContractError, OSError, KeyError, ValueError) as error:
        emit({"ok": False, "diagnostic": str(error)}, compact=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
