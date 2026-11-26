#!/usr/bin/env python3
"""Drift check: fails when docs/ and the code have come apart.

It checks what AGENTS.md (How work moves) asks of every pull request: links in docs/ resolve,
every page is in the index, every requirement in docs/intent/requirements.md has a row in
docs/behaviour/traceability.md that names a test which exists (or says why it is a gap), and one
phase at most is open in docs/intent/plan.md (decision 26). Standard library only.
Run from the repository root: python3 infra/scripts/check_docs.py (make test-agents runs it).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path.cwd()
DOCS = ROOT / "docs"
REQUIREMENTS = DOCS / "intent" / "requirements.md"
PLAN = DOCS / "intent" / "plan.md"
TRACE = DOCS / "behaviour" / "traceability.md"
PRIVATE = DOCS / "intent" / "sources"  # gitignored when it holds personal data: never checked
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "dist", "build", "__pycache__"}

REQ_ID = re.compile(r"^[A-Z][A-Z0-9]*-\d+$")  # AUTH-3, BILL-12
NOT_REQ = {"IDEA", "BUG", "JAP"}  # backlog ids look the same but are not requirements
LINK = re.compile(r"(?<!!)\[[^\]]*\]\(([^)\s]+)\)")
TEST_REF = re.compile(r"([\w./-]+\.[A-Za-z]+)::([\w-]+(?:\.[\w-]+)*)")
PHASE = re.compile(r"^#{2,4}\s+Phase\s+([\w.]+)\b.*\((planned|open|done)\)\s*$", re.I)

errors: list[str] = []


def fail(where: Path, msg: str) -> None:
    errors.append(f"{where.relative_to(ROOT)}: {msg}")


def text_of(path: Path) -> str:
    """The file without fenced code blocks, so templates and examples are not checked."""
    return re.sub(r"^(```|~~~).*?^\1", "", path.read_text(encoding="utf-8"), flags=re.M | re.S)


def first_cells(path: Path) -> list[tuple[str, str]]:
    """(first cell, whole row) for every table body row."""
    rows = []
    for line in text_of(path).splitlines():
        s = line.strip()
        if not s.startswith("|") or re.fullmatch(r"\|[\s:|-]+\|?", s):
            continue
        cell = s.strip("|").split("|")[0].strip().strip("*`")
        rows.append((cell, s))
    return rows


def req_ids(rows: list[tuple[str, str]]) -> set[str]:
    out = set()
    for cell, _ in rows:
        token = cell.split()[0] if cell else ""
        if REQ_ID.match(token) and token.split("-")[0] not in NOT_REQ:
            out.add(token)
    return out


def doc_pages() -> list[Path]:
    return [p for p in DOCS.rglob("*.md") if PRIVATE not in p.parents]


def check_links() -> None:
    files = doc_pages() + [p for p in (ROOT / "AGENTS.md", ROOT / "CLAUDE.md") if p.exists()]
    for f in files:
        for target in LINK.findall(text_of(f)):
            if re.match(r"^[a-z][a-z0-9+.-]*:", target) or target.startswith("#"):
                continue
            path = (f.parent / target.split("#")[0]).resolve()
            if PRIVATE.resolve() in path.parents:
                continue
            if not path.exists():
                fail(f, f"broken link to {target}")


def check_index() -> None:
    index = DOCS / "README.md"
    if not index.exists():
        fail(DOCS, "docs/README.md (the index) is missing")
        return
    linked = {(index.parent / t.split("#")[0]).resolve() for t in LINK.findall(text_of(index))}
    for p in doc_pages():
        if p != index and p.resolve() not in linked:
            fail(p, "not linked from docs/README.md")


def find_file(name: str) -> list[Path]:
    hits = []
    for p in ROOT.rglob(Path(name).name):
        if not SKIP_DIRS.intersection(p.parts) and str(p).endswith(name):
            hits.append(p)
    return hits


def check_tests(pages: list[Path]) -> None:
    for page in pages:
        for file, name in TEST_REF.findall(text_of(page)):
            found = find_file(file)
            if not found:
                fail(page, f"test file {file} not found")
            elif not any(re.search(rf"\b{re.escape(name)}\b", f.read_text("utf-8", "ignore")) for f in found):
                fail(page, f"test {name} not found in {file}")


def check_trace() -> None:
    missing = [need for need in (REQUIREMENTS, TRACE) if not need.exists()]
    for need in missing:
        fail(need.parent, f"{need.name} is missing")
    if missing:
        return
    wanted = req_ids(first_cells(REQUIREMENTS))
    rows = first_cells(TRACE)
    mapped = req_ids(rows)
    for rid in sorted(wanted - mapped):
        fail(TRACE, f"{rid} has no traceability row")
    for rid in sorted(mapped - wanted):
        fail(TRACE, f"{rid} is not in requirements.md")
    for cell, row in rows:
        token = cell.split()[0] if cell else ""
        if token in mapped and not TEST_REF.search(row) and "gap" not in row.lower():
            fail(TRACE, f"{token} names no test and is not marked as a gap")


def check_plan() -> None:
    if not PLAN.exists():
        fail(PLAN.parent, "plan.md is missing")
        return
    known = req_ids(first_cells(REQUIREMENTS)) if REQUIREMENTS.exists() else set()
    open_phases = []
    for line in text_of(PLAN).splitlines():
        m = PHASE.match(line.strip())
        if m and m.group(2).lower() == "open":
            open_phases.append(m.group(1))
        if line.lower().startswith("delivers:"):
            for rid in re.findall(r"\b[A-Z][A-Z0-9]*-\d+\b", line):
                if rid.split("-")[0] not in NOT_REQ and rid not in known:
                    fail(PLAN, f"{rid} is delivered by a phase but not in requirements.md")
    if len(open_phases) > 1:
        fail(PLAN, f"more than one open phase: {', '.join(open_phases)}")


def main() -> int:
    if not DOCS.is_dir():
        print("check_docs: no docs/ folder here; run from the repository root")
        return 1
    check_links()
    check_index()
    check_trace()
    check_plan()
    check_tests([p for p in doc_pages() if "behaviour" in p.parts])
    if errors:
        print("Docs have drifted from the code:")
        print("\n".join(f"  {e}" for e in errors))
        return 1
    print("check_docs: docs and code agree")
    return 0


if __name__ == "__main__":
    sys.exit(main())
