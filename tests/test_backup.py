import gzip
import sqlite3
from datetime import date

import pytest

from emu_the_menu.backup import (
    BackupError,
    backup_database,
    latest_backup,
    prune_backups,
    snapshot_gz,
)
from emu_the_menu.repository import StockRepository
from emu_the_menu.stock import Location

TODAY = date(2026, 10, 27)


@pytest.fixture
def source(tmp_path):
    path = tmp_path / "emu.db"
    with StockRepository(path) as repo:
        repo.add("flour", 500, "g", Location.PANTRY)
        repo.add("milk", 1, "l", Location.FRIDGE)
    return path


@pytest.fixture
def backups(tmp_path):
    folder = tmp_path / "backups"
    folder.mkdir()
    return folder


def restore(backup_file, tmp_path):
    """Unpack a backup the way someone restoring it would, and open it."""
    restored = tmp_path / "restored.db"
    restored.write_bytes(gzip.decompress(backup_file.read_bytes()))
    return restored


def test_a_backup_is_a_dated_compressed_copy_that_restores(source, backups, tmp_path):
    result = backup_database(source, backups, TODAY)
    assert result == backups / "emu-2026-10-27.db.gz"
    with StockRepository(restore(result, tmp_path)) as repo:
        assert [(i.ingredient, i.quantity) for i in repo.list_items()] == [
            ("flour", 500),
            ("milk", 1000),
        ]


def test_backing_up_never_changes_the_source(source, backups):
    before = source.read_bytes()
    backup_database(source, backups, TODAY)
    assert source.read_bytes() == before


def test_a_backup_taken_while_the_database_is_in_use_is_consistent(source, backups, tmp_path):
    writer = sqlite3.connect(source)
    writer.execute("BEGIN IMMEDIATE")
    writer.execute("INSERT INTO ingredients (name, base_unit) VALUES ('uncommitted', 'g')")
    # An uncommitted change must not leak into the copy.
    try:
        result = backup_database(source, backups, TODAY)
    finally:
        writer.rollback()
        writer.close()
    names = {
        row[0]
        for row in sqlite3.connect(restore(result, tmp_path)).execute(
            "SELECT name FROM ingredients"
        )
    }
    assert "uncommitted" not in names


def test_running_twice_on_the_same_day_replaces_the_backup(source, backups):
    backup_database(source, backups, TODAY)
    with StockRepository(source) as repo:
        repo.add("sugar", 1, "kg", Location.PANTRY)
    backup_database(source, backups, TODAY)
    assert [p.name for p in backups.iterdir()] == ["emu-2026-10-27.db.gz"]


def test_no_temporary_files_are_left_behind(source, backups):
    backup_database(source, backups, TODAY)
    assert [p.name for p in backups.iterdir()] == ["emu-2026-10-27.db.gz"]


def test_a_missing_database_is_an_error_and_creates_nothing(tmp_path, backups):
    with pytest.raises(BackupError, match="not found"):
        backup_database(tmp_path / "nothing.db", backups, TODAY)
    assert list(backups.iterdir()) == []


def test_a_file_that_is_not_a_database_is_an_error_and_creates_nothing(tmp_path, backups):
    bad = tmp_path / "bad.db"
    bad.write_bytes(b"this is not a sqlite database at all" * 50)
    with pytest.raises(BackupError):
        backup_database(bad, backups, TODAY)
    assert list(backups.iterdir()) == []


def test_the_backup_folder_is_created_if_missing(source, tmp_path):
    folder = tmp_path / "new" / "backups"
    backup_database(source, folder, TODAY)
    assert (folder / "emu-2026-10-27.db.gz").exists()


def make(folder, *days):
    for day in days:
        (folder / f"emu-{day.isoformat()}.db.gz").write_bytes(b"x")
    return sorted(p.name for p in folder.iterdir())


def test_pruning_keeps_the_last_fourteen_days(backups):
    days = [date(2026, 10, d) for d in range(1, 28)]  # 27 daily backups
    make(backups, *days)
    prune_backups(backups, TODAY)
    kept = {p.name for p in backups.iterdir()}
    for d in range(14, 28):  # the newest fourteen
        assert f"emu-2026-10-{d:02d}.db.gz" in kept


def test_pruning_also_keeps_the_first_backup_of_each_of_the_last_six_months(backups):
    make(
        backups,
        date(2026, 4, 9), date(2026, 4, 20),  # April: older than six months
        date(2026, 5, 2), date(2026, 5, 15),
        date(2026, 6, 1), date(2026, 6, 30),
        date(2026, 7, 5), date(2026, 8, 3), date(2026, 8, 30),
        date(2026, 9, 7), date(2026, 9, 28),
        date(2026, 10, 3), date(2026, 10, 27),
    )  # fmt: skip
    prune_backups(backups, TODAY)
    kept = {p.name for p in backups.iterdir()}
    assert kept == {
        "emu-2026-05-02.db.gz",
        "emu-2026-06-01.db.gz",
        "emu-2026-07-05.db.gz",
        "emu-2026-08-03.db.gz",
        "emu-2026-09-07.db.gz",
        "emu-2026-10-03.db.gz",
        "emu-2026-10-27.db.gz",
    }


def test_pruning_never_touches_files_it_did_not_make(backups):
    (backups / "notes.txt").write_text("keep me")
    (backups / "emu-final-copy.db.gz").write_bytes(b"x")
    make(backups, date(2020, 1, 1))
    prune_backups(backups, TODAY)
    kept = {p.name for p in backups.iterdir()}
    assert {"notes.txt", "emu-final-copy.db.gz"} <= kept
    assert "emu-2020-01-01.db.gz" not in kept


def test_pruning_an_empty_or_missing_folder_is_fine(backups, tmp_path):
    prune_backups(backups, TODAY)
    prune_backups(tmp_path / "missing", TODAY)


def test_a_backup_run_prunes_old_ones(source, backups):
    make(backups, date(2020, 1, 1), date(2026, 10, 20))
    backup_database(source, backups, TODAY)
    assert sorted(p.name for p in backups.iterdir()) == [
        "emu-2026-10-20.db.gz",
        "emu-2026-10-27.db.gz",
    ]


def test_a_snapshot_is_a_restorable_database_in_memory(source, tmp_path):
    packed = snapshot_gz(source)
    restored = tmp_path / "restored.db"
    restored.write_bytes(gzip.decompress(packed))
    with StockRepository(restored) as repo:
        assert len(repo.list_items()) == 2


def test_a_snapshot_of_a_missing_database_is_an_error(tmp_path):
    with pytest.raises(BackupError, match="not found"):
        snapshot_gz(tmp_path / "nothing.db")


def test_the_latest_backup_is_the_newest_by_date(backups):
    make(backups, date(2026, 10, 20), date(2026, 10, 26), date(2026, 10, 24))
    assert latest_backup(backups) == date(2026, 10, 26)


def test_there_is_no_latest_backup_in_an_empty_or_missing_folder(backups, tmp_path):
    assert latest_backup(backups) is None
    assert latest_backup(tmp_path / "missing") is None


def test_the_latest_backup_ignores_files_that_are_not_backups(backups):
    (backups / "notes.txt").write_text("x")
    make(backups, date(2026, 10, 1))
    assert latest_backup(backups) == date(2026, 10, 1)
