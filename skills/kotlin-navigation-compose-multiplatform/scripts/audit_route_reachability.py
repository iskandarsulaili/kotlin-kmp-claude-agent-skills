#!/usr/bin/env python3
"""Audit route reachability in a Compose Multiplatform / Android app.

Answers one question: which routes RENDER in the nav host but have NO way for a player
to reach them?

Why this exists: hand-rolled greps for `onNavigate(Route.X)` over-report badly. They
miss (a) emitters inside the nav host itself and (b) catalog/menu tables that emit
indirectly via `onNavigate(entry.route)`, and they flag lifecycle routes seeded by the
root ViewModel. Both failure modes were hit in practice - expect ~40 false positives if
you skip the catalog form.

Usage:
    python3 audit_route_reachability.py <repo-root> [--routes-file PATH] \
        [--navhost PATH] [--catalog PATH] [--deeplink PATH]

Everything is auto-detected when the flags are omitted. Prints a table plus a final
list of genuinely unreachable routes. Read the 'feature flag' section of SKILL.md
before acting on the output: a gated-off route is NOT a defect.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

SKIP_BASENAMES = {"UiNavigationContracts.kt", "PawDeepLinks.kt"}


def read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def find_first(root: Path, name: str) -> Path | None:
    for p in root.rglob(name):
        if "/build/" not in str(p):
            return p
    return None


def find_routes_file(root: Path) -> Path | None:
    """Locate the enum/route table (first file declaring `enum class <X>Route`)."""
    for p in root.rglob("*.kt"):
        if "/build/" in str(p):
            continue
        text = read(p)
        if re.search(r"enum class \w*(Root|App)?Route\b", text):
            return p
    return None


def collect_kt(root: Path) -> list[Path]:
    return [p for p in root.rglob("*.kt") if "/build/" not in str(p)]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("repo_root")
    ap.add_argument("--routes-file")
    ap.add_argument("--navhost")
    ap.add_argument("--catalog")
    ap.add_argument("--deeplink")
    args = ap.parse_args()

    root = Path(args.repo_root).resolve()
    if not root.is_dir():
        print(f"not a directory: {root}", file=sys.stderr)
        return 2

    routes_file = Path(args.routes_file) if args.routes_file else find_routes_file(root)
    navhost = Path(args.navhost) if args.navhost else find_first(root, "AppNavHost.kt")
    catalog = Path(args.catalog) if args.catalog else find_first(root, "WorldSystemCatalog.kt")
    deeplink = Path(args.deeplink) if args.deeplink else find_first(root, "PawDeepLinks.kt")

    if not routes_file:
        print("could not locate the route enum; pass --routes-file", file=sys.stderr)
        return 2

    src = read(routes_file)
    m = re.search(r"enum class \w*Route\b[^{]*\{(.*?)\n\}", src, re.S)
    if not m:
        print(f"no enum route block found in {routes_file}", file=sys.stderr)
        return 2
    routes = [
        line.strip().rstrip(",")
        for line in m.group(1).splitlines()
        if re.match(r"^\s*[A-Z][A-Z0-9_]*\s*,?\s*$", line)
    ]

    navhost_text = read(navhost) if navhost else ""
    catalog_text = read(catalog) if catalog else ""
    deeplink_text = read(deeplink) if deeplink else ""

    # (1) direct emitters, everywhere EXCEPT the enum file and the deep-link parser.
    #     NOTE: the nav host IS scanned - its callbacks are real emitters.
    direct: dict[str, set[str]] = {}
    for p in collect_kt(root):
        if p.name in SKIP_BASENAMES:
            continue
        text = read(p)
        for r in re.findall(r"onNavigate\(\s*\w*Route\.([A-Z][A-Z0-9_]*)", text):
            direct.setdefault(r, set()).add(p.name)
        for r in re.findall(r"currentRoute\s*=\s*\w*Route\.([A-Z][A-Z0-9_]*)", text):
            direct.setdefault(r, set()).add(p.name)

    # (2) indirect: catalog/menu table entries -> onNavigate(entry.route)
    catalog_routes = set()
    if catalog_text:
        catalog_routes = set(
            re.findall(r"\w*Entry\(\s*\w*Route\.([A-Z][A-Z0-9_]*)", catalog_text)
        )

    # (3) deep-link parser
    dl_routes = set(re.findall(r"\w*Route\.([A-Z][A-Z0-9_]*)", deeplink_text)) if deeplink_text else set()

    print(f"repo        : {root}")
    print(f"routes file : {routes_file}")
    print(f"nav host    : {navhost}")
    print(f"catalog     : {catalog}")
    print(f"deep links  : {deeplink}")
    print(f"routes      : {len(routes)}")
    print()
    print(f"{'ROUTE':<28}{'RENDER':<8}{'CATALOG':<9}{'DEEPLINK':<10}STATUS")
    print("-" * 74)

    unreachable = []
    for r in routes:
        renders = bool(re.search(r"\w*Route\." + r + r"\s*->", navhost_text))
        in_cat = r in catalog_routes
        in_dl = r in dl_routes
        in_direct = r in direct
        reachable = in_cat or in_dl or in_direct
        if renders and not reachable:
            unreachable.append(r)
        print(
            f"{r:<28}{'yes' if renders else '-':<8}"
            f"{'yes' if in_cat else '-':<9}{'yes' if in_dl else '-':<10}"
            f"{'reachable' if reachable else '**NO ENTRY**'}"
        )

    print()
    print("RENDERS BUT NO PLAYER ENTRY (verify the feature flag before reporting):")
    for r in unreachable:
        print(f"  - {r}")
    if not unreachable:
        print("  (none)")

    print()
    print("Lifecycle routes are seeded by the root ViewModel and may appear above for a")
    print("different reason; check the root ViewModel's initial-route logic before acting.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
