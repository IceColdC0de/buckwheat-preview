"""Snapshots: copy files before a write, restore them on rollback.

Layout, under ``state_dir()/snapshots/<UTC-timestamp>-<request_id>/``::

    meta.json     files, sha256 before/after, plan JSON, state, revert_deadline
    files/<n>     byte copy of the n-th file as it was before the change

Directories are 0700 and files 0600. Every file write goes through
``atomic_write`` (which calls ``guard_path``). Paths being snapshotted are the
allowlist for their own backups; restore only writes to paths recorded in the
snapshot's meta.json.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, NewType

from buckwheat.config import state_dir
from buckwheat.plan import ChangePlan
from buckwheat.safety.atomic import atomic_write, safe_delete
from buckwheat.safety.paths import guard_path

SnapshotId = NewType("SnapshotId", str)
State = Literal["pending", "kept", "reverted"]

STATES: tuple[State, ...] = ("pending", "kept", "reverted")
DEFAULT_RETENTION = 50
_ID_RE = re.compile(r"^\d{8}T\d{12}Z-[0-9a-f]{8}$")


class SnapshotError(Exception):
    """A snapshot is missing, malformed, or cannot be restored."""


class ModifiedSinceError(SnapshotError):
    """Files changed after the snapshot's change was applied (user edits)."""

    def __init__(self, paths: list[str]) -> None:
        super().__init__("files changed since the snapshot: " + ", ".join(paths))
        self.paths = paths


@dataclass(frozen=True)
class SnapshotInfo:
    id: SnapshotId
    created: str
    state: State
    revert_deadline: str | None
    paths: tuple[str, ...]


def snapshots_root() -> Path:
    return state_dir() / "snapshots"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_bytes(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None


def _write_bytes(path: Path, data: bytes, allowlist: Iterable[Path]) -> None:
    """Write bytes via atomic_write; only valid UTF-8 text is supported."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise SnapshotError(f"not UTF-8 text, cannot handle: {path}") from None
    atomic_write(path, text, allowlist)


def _write_private(path: Path, text: str) -> None:
    atomic_write(path, text, [path])
    os.chmod(path, 0o600)


def _dir(snapshot_id: str) -> Path:
    if not _ID_RE.match(snapshot_id):
        raise SnapshotError(f"invalid snapshot id: {snapshot_id!r}")
    return snapshots_root() / snapshot_id


def _load(snapshot_id: str) -> dict[str, Any]:
    try:
        meta = json.loads((_dir(snapshot_id) / "meta.json").read_text("utf-8"))
    except FileNotFoundError:
        raise SnapshotError(f"no such snapshot: {snapshot_id}") from None
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise SnapshotError(f"corrupt meta.json in snapshot {snapshot_id}") from None
    if not isinstance(meta, dict) or meta.get("state") not in STATES:
        raise SnapshotError(f"corrupt meta.json in snapshot {snapshot_id}")
    return meta


def _save(snapshot_id: str, meta: dict[str, Any]) -> None:
    _write_private(_dir(snapshot_id) / "meta.json", json.dumps(meta, indent=2))


def snapshot(
    paths: Iterable[str | Path],
    plan: ChangePlan | None,
    revert_deadline: datetime | None = None,
    retention: int = DEFAULT_RETENTION,
) -> SnapshotId:
    """Copy every file in ``paths`` (or record that it is absent) and return the id.

    The new snapshot starts in state ``pending``. Old kept/reverted snapshots
    beyond ``retention`` are deleted; pending ones are never pruned.
    """
    real = list(dict.fromkeys(Path(p).expanduser().resolve() for p in paths))
    now = datetime.now(UTC)
    snapshot_id = SnapshotId(f"{now:%Y%m%dT%H%M%S%fZ}-{uuid.uuid4().hex[:8]}")
    root = snapshots_root()
    snap = root / snapshot_id
    (snap / "files").mkdir(parents=True)
    for d in (root, snap, snap / "files"):
        os.chmod(d, 0o700)  # mkdir's mode is masked by umask and skips parents

    files: list[dict[str, Any]] = []
    for i, path in enumerate(real):
        data = _read_bytes(path)
        entry: dict[str, Any] = {"path": str(path), "existed": data is not None}
        if data is not None:
            backup = snap / "files" / str(i)
            _write_bytes(backup, data, [backup])
            os.chmod(backup, 0o600)
            entry["backup"] = f"files/{i}"
            entry["before_sha256"] = _sha256(data)
            entry["mode"] = path.stat().st_mode & 0o7777
        files.append(entry)

    _save(
        snapshot_id,
        {
            "id": snapshot_id,
            "created": now.isoformat(),
            "state": "pending",
            "revert_deadline": revert_deadline.isoformat() if revert_deadline else None,
            "plan": json.loads(plan.model_dump_json()) if plan is not None else None,
            "files": files,
        },
    )
    _prune(retention)
    return snapshot_id


def record_after(snapshot_id: SnapshotId) -> None:
    """Record sha256 of each file's current contents (call after applying)."""
    meta = _load(snapshot_id)
    for entry in meta["files"]:
        data = _read_bytes(Path(entry["path"]))
        entry["after_sha256"] = None if data is None else _sha256(data)
    _save(snapshot_id, meta)


def mark(snapshot_id: SnapshotId, state: State) -> None:
    """Move a snapshot to ``state``. Only pending -> kept|reverted, kept -> reverted."""
    if state not in STATES:
        raise SnapshotError(f"unknown state: {state!r}")
    meta = _load(snapshot_id)
    current = meta["state"]
    allowed = {("pending", "kept"), ("pending", "reverted"), ("kept", "reverted")}
    if (current, state) not in allowed:
        raise SnapshotError(f"cannot move snapshot from {current} to {state}")
    meta["state"] = state
    _save(snapshot_id, meta)


def restore(snapshot_id: SnapshotId, force: bool = False) -> None:
    """Put every file back as it was; delete files that did not exist.

    If after-hashes were recorded and a file has changed since (the user edited
    it), raise ``ModifiedSinceError`` unless ``force`` is true. Does not change
    the snapshot state; call ``mark(id, "reverted")`` afterwards.
    """
    meta = _load(snapshot_id)
    entries = meta["files"]
    paths = [Path(e["path"]) for e in entries]
    if not force:
        changed = []
        for e in entries:
            if "after_sha256" not in e:
                continue
            data = _read_bytes(Path(e["path"]))
            if (None if data is None else _sha256(data)) != e["after_sha256"]:
                changed.append(e["path"])
        if changed:
            raise ModifiedSinceError(changed)

    # Read every backup first so a bad backup fails before anything is touched.
    backups: dict[str, bytes] = {}
    for e in entries:
        if e["existed"]:
            data = (_dir(snapshot_id) / e["backup"]).read_bytes()
            if _sha256(data) != e["before_sha256"]:
                raise SnapshotError(f"backup is corrupt for {e['path']}")
            backups[e["path"]] = data

    for e in entries:
        path = guard_path(e["path"], paths)
        if e["existed"]:
            path.parent.mkdir(parents=True, exist_ok=True)
            _write_bytes(path, backups[e["path"]], paths)
            os.chmod(path, e["mode"])
        else:
            safe_delete(path, paths)


def _info(meta: dict[str, Any]) -> SnapshotInfo:
    return SnapshotInfo(
        id=SnapshotId(meta["id"]),
        created=meta["created"],
        state=meta["state"],
        revert_deadline=meta.get("revert_deadline"),
        paths=tuple(e["path"] for e in meta["files"]),
    )


def history() -> list[SnapshotInfo]:
    """All readable snapshots, newest first. Unreadable ones are skipped."""
    root = snapshots_root()
    if not root.is_dir():
        return []
    out = []
    for d in sorted(root.iterdir(), reverse=True):
        if not _ID_RE.match(d.name):
            continue
        try:
            out.append(_info(_load(d.name)))
        except (SnapshotError, KeyError):
            continue
    return out


def recover_pending() -> list[SnapshotInfo]:
    """Snapshots still ``pending`` (never kept or reverted), e.g. after a crash.

    The caller decides whether to restore them; nothing is changed here.
    """
    return [s for s in history() if s.state == "pending"]


def _prune(retention: int) -> None:
    done = [s for s in history() if s.state != "pending"]
    for s in done[max(retention, 0) :]:
        shutil.rmtree(_dir(s.id), ignore_errors=True)
