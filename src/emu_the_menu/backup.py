"""Nightly backup of the SQLite database.

Run it with `python -m emu_the_menu.backup`. It reads `EMU_DB` (the database) and
`EMU_BACKUP_DIR` (where backups go), the same way the web app reads `EMU_DB`.

Each backup is a consistent copy made with SQLite's own backup API, so it is safe
while the app is running. The copy is checked before it is kept and is written to a
temporary file first, so a failed run never leaves a half-written backup behind.
"""

import gzip
import os
import re
import shutil
import sqlite3
import sys
import tempfile
from datetime import date
from pathlib import Path

_NAME = re.compile(r"emu-(\d{4})-(\d{2})-(\d{2})\.db\.gz")
_KEEP_DAYS = 14
_KEEP_MONTHS = 6


class BackupError(RuntimeError):
    """The database could not be backed up."""


def backup_database(source: str | Path, folder: str | Path, today: date) -> Path:
    """Write `emu-YYYY-MM-DD.db.gz` into `folder`, prune old backups and return the new file.

    Running again on the same day replaces that day's backup.
    """
    source, folder = Path(source), Path(folder)
    if not source.is_file():
        raise BackupError(f"database not found: {source}")
    folder.mkdir(parents=True, exist_ok=True)

    final = folder / f"emu-{today.isoformat()}.db.gz"
    copy = folder / f".emu-{today.isoformat()}.db.tmp"
    packed = folder / f".emu-{today.isoformat()}.db.gz.tmp"
    try:
        _copy_database(source, copy)
        with copy.open("rb") as raw, gzip.open(packed, "wb") as out:
            shutil.copyfileobj(raw, out)
        os.replace(packed, final)
    finally:
        copy.unlink(missing_ok=True)
        packed.unlink(missing_ok=True)
    prune_backups(folder, today)
    return final


def _copy_database(source: Path, destination: Path) -> None:
    """Copy with SQLite's backup API and check the copy is a healthy database."""
    reader = sqlite3.connect(f"{source.resolve().as_uri()}?mode=ro", uri=True)
    writer = sqlite3.connect(destination)
    try:
        reader.backup(writer)
        (result,) = writer.execute("PRAGMA integrity_check").fetchone()
        if result != "ok":
            raise BackupError(f"the copy failed its integrity check: {result}")
    except sqlite3.DatabaseError as exc:
        raise BackupError(f"could not read the database: {exc}") from exc
    finally:
        reader.close()
        writer.close()


def _dated_backups(folder: Path) -> dict[Path, date]:
    """The files in `folder` named like Emu backups, with the date in each name."""
    found: dict[Path, date] = {}
    if folder.is_dir():
        for path in folder.iterdir():
            if match := _NAME.fullmatch(path.name):
                try:
                    found[path] = date(*(int(part) for part in match.groups()))
                except ValueError:
                    continue
    return found


def latest_backup(folder: str | Path) -> date | None:
    """The date of the newest backup in `folder`, or None if there are none."""
    return max(_dated_backups(Path(folder)).values(), default=None)


def snapshot_gz(source: str | Path) -> bytes:
    """A fresh, consistent, compressed copy of the database, for downloading."""
    source = Path(source)
    if not source.is_file():
        raise BackupError(f"database not found: {source}")
    with tempfile.TemporaryDirectory() as scratch:
        copy = Path(scratch) / "snapshot.db"
        _copy_database(source, copy)
        return gzip.compress(copy.read_bytes())


def prune_backups(folder: str | Path, today: date) -> None:
    """Keep the last 14 days and the first backup of each of the last 6 months.

    Files that are not named like Emu backups are never touched.
    """
    backups = _dated_backups(Path(folder))
    keep = {path for path, day in backups.items() if (today - day).days < _KEEP_DAYS}
    year, month = today.year, today.month
    for _ in range(_KEEP_MONTHS):
        in_month = [
            (day, path) for path, day in backups.items() if (day.year, day.month) == (year, month)
        ]
        if in_month:
            keep.add(min(in_month)[1])
        year, month = (year, month - 1) if month > 1 else (year - 1, 12)

    for path in backups.keys() - keep:
        path.unlink()


def main() -> int:
    source = os.environ.get("EMU_DB", "emu.db")
    folder = os.environ.get("EMU_BACKUP_DIR", "backups")
    try:
        result = backup_database(source, folder, date.today())
    except BackupError as exc:
        print(f"backup failed: {exc}", file=sys.stderr)
        return 1
    print(f"backup written: {result} ({result.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
