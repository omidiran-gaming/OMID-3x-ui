#!/usr/bin/env python3
"""Overlay OMID customizations onto a fresh upstream tree and sync it into this repo."""
from __future__ import annotations
import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

PROTECTED = {".git", ".github", ".omid"}


def run(args: list[str], cwd: Path | None = None) -> str:
    return subprocess.check_output(args, cwd=cwd, text=True).strip()


def sync_path(source: Path, dest: Path) -> None:
    if source.is_dir() and not source.is_symlink():
        if dest.exists() and (not dest.is_dir() or dest.is_symlink()):
            dest.unlink() if not dest.is_dir() else shutil.rmtree(dest)
        dest.mkdir(parents=True, exist_ok=True)
        names = {p.name for p in source.iterdir()}
        for existing in dest.iterdir():
            if existing.name not in names:
                if existing.is_dir() and not existing.is_symlink():
                    shutil.rmtree(existing)
                else:
                    existing.unlink()
        for child in source.iterdir():
            sync_path(child, dest / child.name)
    else:
        if dest.exists() and dest.is_dir() and not dest.is_symlink():
            shutil.rmtree(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if source.is_symlink():
            if dest.exists() or dest.is_symlink():
                dest.unlink()
            dest.symlink_to(os.readlink(source))
        else:
            shutil.copy2(source, dest, follow_symlinks=False)


def sync_tree(source: Path, dest: Path) -> None:
    # Mirror upstream-managed paths exactly (including removals) while keeping
    # the Git metadata, the custom workflow, and the OMID overlay independent.
    source_names = {p.name for p in source.iterdir() if p.name not in {".git", ".github", ".omid"}}
    for existing in dest.iterdir():
        if existing.name in PROTECTED or existing.name in source_names:
            continue
        if existing.is_dir() and not existing.is_symlink():
            shutil.rmtree(existing)
        else:
            existing.unlink()
    for child in source.iterdir():
        if child.name in {".git", ".github", ".omid"}:
            continue
        sync_path(child, dest / child.name)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--upstream", required=True, type=Path)
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--github-output", type=Path)
    args = parser.parse_args()
    upstream = args.upstream.resolve()
    repo = args.repo.resolve()
    overlay = repo / ".omid"
    if not (overlay / "scripts" / "apply_omid_overlay.py").is_file():
        raise SystemExit("sync_upstream: .omid/scripts/apply_omid_overlay.py missing")

    new_sha = run(["git", "rev-parse", "HEAD"], cwd=upstream)
    sha_file = overlay / "upstream-commit.txt"
    old_sha = sha_file.read_text().strip() if sha_file.exists() else ""

    subprocess.check_call([sys.executable, str(overlay / "scripts" / "apply_omid_overlay.py"), str(overlay), str(upstream)])
    sync_tree(upstream, repo)
    sha_file.write_text(new_sha + "\n")

    output = args.github_output or (Path(os.environ["GITHUB_OUTPUT"]) if os.environ.get("GITHUB_OUTPUT") else None)
    if output:
        with output.open("a", encoding="utf-8") as f:
            f.write(f"upstream_sha={new_sha}\n")
            f.write(f"upstream_changed={'true' if old_sha != new_sha else 'false'}\n")
    print(f"Synced MHSanaei/3x-ui {new_sha}; previous upstream revision: {old_sha or 'none'}")


if __name__ == "__main__":
    main()
