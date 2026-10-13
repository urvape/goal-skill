#!/usr/bin/env python3
"""undo_journal.py - git-independent, milestone-scoped undo journal.

Snapshots the pre-edit bytes of files so a milestone's edits can be rolled
back precisely, even in workspaces without git, and without ever touching
changes that predate the snapshot.

Journal layout (default: ./.freebuff/undo-journal):

    index.json                    milestone metadata and entry records
    snapshots/<milestone-id>/     byte copies of pre-edit files

Commands:
    snapshot --milestone <id> <file> [<file> ...]   record pre-edit state
    rollback --milestone <id>                       restore that state
    list                                            show recorded milestones
    clear --yes                                     delete the whole journal

Rules enforced:
    * Paths must stay inside the working directory.
    * The first snapshot for a file in a milestone wins (later calls keep it),
      so rollback targets the state before the milestone's first edit.
    * rollback restores exactly the snapshotted files: existing files get
      their pre-milestone bytes back; paths that did not exist are removed;
      files the milestone never touched are left alone.
    * A milestone can be rolled back only once; a second call is a no-op.

Exit codes: 0 ok, 1 usage/safety refusal, 2 corrupt or incomplete journal.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import NoReturn

DEFAULT_JOURNAL_DIR = Path(".freebuff") / "undo-journal"
INDEX_VERSION = 1


def die(code: int, message: str) -> NoReturn:
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(code)


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def milestone_dirname(name: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-._") or "milestone"
    suffix = hashlib.sha1(name.encode("utf-8")).hexdigest()[:6]
    return f"{slug}-{suffix}"


def load_index(journal: Path) -> dict:
    index_path = journal / "index.json"
    if not index_path.exists():
        return {"version": INDEX_VERSION, "milestones": {}}
    try:
        data = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        die(2, f"journal index is unreadable: {index_path}: {exc}")
    if not isinstance(data, dict) or not isinstance(data.get("milestones"), dict):
        die(2, f"journal index has an unexpected shape: {index_path}")
    return data


def save_index(journal: Path, index: dict) -> None:
    journal.mkdir(parents=True, exist_ok=True)
    index_path = journal / "index.json"
    tmp_path = index_path.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(index, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp_path, index_path)


def safe_relative(raw: str) -> Path:
    """Resolve raw against cwd, refusing anything that escapes the workspace."""
    root = Path.cwd().resolve()
    target = (root / raw).resolve()
    try:
        rel = target.relative_to(root)
    except ValueError:
        die(1, f"refusing to journal a path outside the workspace: {raw}")
    if rel == Path("."):
        die(1, f"refusing to journal a directory: {raw}")
    return rel


def cmd_snapshot(args: argparse.Namespace) -> int:
    journal = Path(args.journal_dir)
    index = load_index(journal)
    milestones = index["milestones"]
    record = milestones.get(args.milestone)
    if record is None:
        record = {
            "name": args.milestone,
            "created_at": utcnow(),
            "rolled_back": False,
            "rolled_back_at": None,
            "entries": [],
        }
        milestones[args.milestone] = record
    if record["rolled_back"]:
        die(1, f"milestone {args.milestone!r} was already rolled back; use a new milestone id")

    known = {entry["path"] for entry in record["entries"]}
    for raw in args.files:
        rel = safe_relative(raw)
        key = rel.as_posix()
        if key in known:
            print(f"keep     {key} (first snapshot for this milestone is the rollback target)")
            continue
        source = Path.cwd() / rel
        if source.is_dir():
            die(1, f"refusing to journal a directory: {key}")
        entry = {
            "path": key,
            "existed": source.is_file(),
            "snapshot": None,
            "sha256": None,
            "size": None,
            "snapshotted_at": utcnow(),
        }
        if entry["existed"]:
            snapshot_dir = journal / "snapshots" / milestone_dirname(args.milestone)
            snapshot_dir.mkdir(parents=True, exist_ok=True)
            snap_path = snapshot_dir / f"{len(record['entries']) + 1:04d}__{key.replace('/', '__')}"
            shutil.copy2(source, snap_path)
            entry["snapshot"] = snap_path.relative_to(journal).as_posix()
            entry["sha256"] = sha256_of(snap_path)
            entry["size"] = snap_path.stat().st_size
            print(f"snapshot {key} -> {entry['snapshot']} ({entry['size']} bytes, sha256 {entry['sha256'][:12]})")
        else:
            print(f"snapshot {key} (absent; rollback will remove it if the milestone creates it)")
        record["entries"].append(entry)
        known.add(key)

    save_index(journal, index)
    print(f"journal: {len(record['entries'])} file(s) tracked in milestone {args.milestone!r}")
    return 0


def cmd_rollback(args: argparse.Namespace) -> int:
    journal = Path(args.journal_dir)
    index = load_index(journal)
    record = index["milestones"].get(args.milestone)
    if record is None:
        die(1, f"unknown milestone {args.milestone!r}; run 'list' to see recorded milestones")
    if record["rolled_back"]:
        print(f"milestone {args.milestone!r} was already rolled back; no files touched")
        return 0

    # Verify every snapshot exists before touching anything.
    for entry in record["entries"]:
        if entry["existed"]:
            snap_path = journal / entry["snapshot"]
            if not snap_path.is_file():
                die(2, f"snapshot missing for {entry['path']}: {snap_path}; refusing to touch the workspace")

    restored = removed = 0
    for entry in reversed(record["entries"]):
        target = Path.cwd() / entry["path"]
        if entry["existed"]:
            snap_path = journal / entry["snapshot"]
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(snap_path, target)
            print(f"restored {entry['path']} (sha256 {entry['sha256'][:12]}, {entry['size']} bytes)")
            restored += 1
        else:
            if target.is_file():
                target.unlink()
                print(f"removed  {entry['path']} (created during the milestone)")
                removed += 1
            elif target.exists():
                die(1, f"refusing to remove a non-file path created during the milestone: {entry['path']}")
            else:
                print(f"kept     {entry['path']} (already absent)")

    record["rolled_back"] = True
    record["rolled_back_at"] = utcnow()
    save_index(journal, index)
    print(f"rolled back milestone {args.milestone!r}: {restored} restored, {removed} removed")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    journal = Path(args.journal_dir)
    index = load_index(journal)
    milestones = index["milestones"]
    if not milestones:
        print(f"journal is empty ({journal})")
        return 0
    for name in sorted(milestones):
        record = milestones[name]
        state = "rolled-back" if record["rolled_back"] else "active"
        print(f"{name} [{state}] {len(record['entries'])} file(s), created {record['created_at']}")
        for entry in record["entries"]:
            kind = "restore" if entry["existed"] else "remove "
            detail = entry["sha256"][:12] if entry["sha256"] else "absent at snapshot"
            print(f"  [{kind}] {entry['path']} ({detail})")
    return 0


def cmd_clear(args: argparse.Namespace) -> int:
    journal = Path(args.journal_dir)
    if not args.yes:
        die(1, "refusing to delete the journal without --yes")
    if journal.exists():
        shutil.rmtree(journal)
        print(f"deleted journal {journal}")
    else:
        print(f"no journal at {journal}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="undo_journal.py",
        description="Git-independent, milestone-scoped undo journal for pre-edit snapshots.",
    )
    parser.add_argument(
        "--journal-dir",
        default=str(DEFAULT_JOURNAL_DIR),
        help="journal directory (default: %(default)s)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_snapshot = sub.add_parser("snapshot", help="record pre-edit state of files for a milestone")
    p_snapshot.add_argument("--milestone", required=True, help="milestone id (free-form)")
    p_snapshot.add_argument("files", nargs="+", help="files to snapshot before editing")
    p_snapshot.set_defaults(func=cmd_snapshot)

    p_rollback = sub.add_parser("rollback", help="restore a milestone's snapshots")
    p_rollback.add_argument("--milestone", required=True, help="milestone id to roll back")
    p_rollback.set_defaults(func=cmd_rollback)

    p_list = sub.add_parser("list", help="show recorded milestones and their state")
    p_list.set_defaults(func=cmd_list)

    p_clear = sub.add_parser("clear", help="delete the whole journal")
    p_clear.add_argument("--yes", action="store_true", help="required to actually delete")
    p_clear.set_defaults(func=cmd_clear)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
