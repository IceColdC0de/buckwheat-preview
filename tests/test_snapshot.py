"""Snapshot tests: tmp_path only, no network."""

from __future__ import annotations

from pathlib import Path

import pytest

from buckwheat.safety import snapshot as snap


@pytest.fixture(autouse=True)
def _state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))


@pytest.fixture
def files(tmp_path: Path) -> tuple[Path, Path]:
    work = tmp_path / "work"
    work.mkdir()
    return work / "a.conf", work / "new.conf"


def test_round_trip_byte_for_byte(files: tuple[Path, Path]) -> None:
    a, _ = files
    original = "café\r\nline2\n\n  trailing  "
    a.write_bytes(original.encode())
    a.chmod(0o640)
    sid = snap.snapshot([a], None)
    a.write_text("changed")
    snap.restore(sid)
    assert a.read_bytes() == original.encode()
    assert a.stat().st_mode & 0o777 == 0o640


def test_new_file_removed_on_restore(files: tuple[Path, Path]) -> None:
    a, new = files
    a.write_text("x")
    sid = snap.snapshot([a, new], None)
    new.write_text("created by change")
    snap.restore(sid)
    assert not new.exists()
    assert a.read_text() == "x"


def test_state_transitions(files: tuple[Path, Path]) -> None:
    a, _ = files
    a.write_text("x")
    sid = snap.snapshot([a], None)
    snap.mark(sid, "kept")
    snap.mark(sid, "reverted")
    with pytest.raises(snap.SnapshotError):
        snap.mark(sid, "kept")
    sid2 = snap.snapshot([a], None)
    snap.mark(sid2, "reverted")
    with pytest.raises(snap.SnapshotError):
        snap.mark(sid2, "pending")  # type: ignore[arg-type]


def test_recover_pending(files: tuple[Path, Path]) -> None:
    a, _ = files
    a.write_text("x")
    done = snap.snapshot([a], None)
    snap.mark(done, "kept")
    open_id = snap.snapshot([a], None)
    assert [s.id for s in snap.recover_pending()] == [open_id]
    assert {s.id for s in snap.history()} == {done, open_id}


def test_modified_since_warns(files: tuple[Path, Path]) -> None:
    a, _ = files
    a.write_text("before")
    sid = snap.snapshot([a], None)
    a.write_text("after")
    snap.record_after(sid)
    a.write_text("user edit")
    with pytest.raises(snap.ModifiedSinceError):
        snap.restore(sid)
    snap.restore(sid, force=True)
    assert a.read_text() == "before"


def test_private_permissions(files: tuple[Path, Path]) -> None:
    a, _ = files
    a.write_text("x")
    sid = snap.snapshot([a], None)
    d = snap.snapshots_root() / sid
    assert d.stat().st_mode & 0o777 == 0o700
    assert (d / "meta.json").stat().st_mode & 0o777 == 0o600
    assert (d / "files" / "0").stat().st_mode & 0o777 == 0o600


def test_retention_keeps_pending(files: tuple[Path, Path]) -> None:
    a, _ = files
    a.write_text("x")
    first = snap.snapshot([a], None)
    for _ in range(4):
        snap.mark(snap.snapshot([a], None, retention=2), "kept")
    ids = [s.id for s in snap.history()]
    assert first in ids  # pending is never pruned
    assert len(ids) == 4  # first + the 3 newest kept (last was pending when pruned)


def test_bad_id_rejected() -> None:
    with pytest.raises(snap.SnapshotError):
        snap.restore(snap.SnapshotId("../../etc"))


def test_snapshot_before_create_records_absence_and_restore_deletes(
    files: tuple[Path, Path],
) -> None:
    _, new = files
    sid = snap.snapshot([new], None)
    meta = snap._load(sid)
    assert meta["files"][0]["existed"] is False
    new.write_text("created by change")
    snap.record_after(sid)
    snap.restore(sid)
    assert not new.exists()


def test_restore_after_delete_recreates_byte_for_byte(
    files: tuple[Path, Path],
) -> None:
    a, _ = files
    original = "# managed\r\nkey = 1\n  no final newline"
    a.write_bytes(original.encode())
    a.chmod(0o640)
    sid = snap.snapshot([a], None)
    a.unlink()  # the change deleted the file
    snap.record_after(sid)
    snap.restore(sid)  # not "modified since": after_sha256 recorded absence
    assert a.read_bytes() == original.encode()
    assert a.stat().st_mode & 0o777 == 0o640


def test_restore_after_delete_detects_recreated_file(
    files: tuple[Path, Path],
) -> None:
    a, _ = files
    a.write_text("ours")
    sid = snap.snapshot([a], None)
    a.unlink()
    snap.record_after(sid)
    a.write_text("the user made a new one")
    with pytest.raises(snap.ModifiedSinceError):
        snap.restore(sid)
    snap.restore(sid, force=True)
    assert a.read_text() == "ours"
