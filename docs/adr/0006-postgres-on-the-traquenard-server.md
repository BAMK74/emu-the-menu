# Postgres on the Traquenard Server, replacing SQLite on the Pi

Emu moves from a Raspberry Pi to the Traquenard Server, where a Tenant gets no volume of its own (nothing backs one up yet) but does get its own database on the Shared Postgres, which is backed up off-site. So Emu stores everything in Postgres and drops SQLite entirely: the Server is the only install, and supporting two databases would double every query for no user. This supersedes ADR 0001's choice of SQLite and of the Pi. Python, FastAPI and server-rendered pages stay.

How:

- **Connection.** One URI from `EMU_DATABASE_URL` (on the Server, the Secret `emu-postgres`, key `uri`). Each process keeps a small pool per URI (psycopg 3 and psycopg-pool, at most 10 connections). A repository borrows a connection when it opens and gives it back when it closes, and repositories opened inside another (a Menu repository's Recipes, Eaters and Stock) share their parent's, so one page holds a handful of connections at most.
- **Queries.** The same hand-written SQL as before, adapted to Postgres, through psycopg rather than an ORM or query builder: the change stays small, and the SQL stays readable next to the rules it serves. Connections run in autocommit, and writes that must happen together go in an explicit transaction, which is how the SQLite code behaved.
- **Schema.** Numbered SQL files in `src/emu_the_menu/migrations/`, applied in order on start and recorded in `schema_migrations`, under an advisory lock so two processes starting together do not both apply them. No Alembic: with no ORM it would add ceremony and no safety. A shipped migration is never edited; a change is a new file. The column-by-column upgrades of older SQLite files are gone, since every Postgres database starts from 001.
- **Names.** Names that were unique `COLLATE NOCASE` are unique on `lower(name)`, in the `C` collation, which folds A to Z only and sorts by code point, as SQLite did. No `citext` extension, so the database owner needs no extra rights.
- **Types.** Dates are `date`, flags `boolean` and amounts `double precision`, instead of SQLite's text and integers.
- **Backups.** The Server backs the database up, so the nightly SQLite copy, `EMU_BACKUP_DIR`, the backup status in Settings and `/export.db.gz` are gone. The readable JSON export stays, for people to keep their own copy.
- **The Pi's data.** `python -m emu_the_menu.import_sqlite emu.db` copies a SQLite file, ids included, into an empty database in one transaction, and refuses a database that holds anything. It is the only module that imports `sqlite3`.
- **Tests.** They run against a real Postgres 18 (`EMU_TEST_DATABASE_URL`), locally from `compose.yaml` or Homebrew and in CI as a service container. One database per run, emptied before each test.

Rejected: keeping SQLite on a volume (the Server cannot back one up yet, and a Tenant has none); SQLAlchemy, Core or ORM (a rewrite of every repository for portability Emu no longer needs); a database cloned per test (the same isolation as emptying the tables, but a minute slower across the suite).
