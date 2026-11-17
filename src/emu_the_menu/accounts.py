"""Accounts: who may sign in, and what they may change (ADR 0008).

An account signs in as one Eater, by that Eater's name, with a password. Its role is an Adult or
a Teen, set by an Adult and separate from the Eater's calorie Category. Children are Eaters with no
account. An Adult changes everything; a Teen sees everything, marks Meals cooked and edits their own
dislikes and diet.

Passwords are kept as argon2id hashes. A session is a random token in a cookie, of which only the
SHA-256 is stored, with its own CSRF token. Failed sign-ins are counted by the name tried, and after
`FREE_FAILURES` the next try must wait, longer each time.

The first Adult is made in the container, since nobody can sign in yet:

    python -m emu_the_menu.accounts create-adult "Alex"
    python -m emu_the_menu.accounts reset-password "Alex"
"""

import argparse
import getpass
import hashlib
import secrets
import sys
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

import psycopg
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from emu_the_menu.db import Database, attach
from emu_the_menu.eaters import Category, EaterRepository

MIN_PASSWORD = 10
MAX_PASSWORD = 200
SESSION_DAYS = 30
FREE_FAILURES = 5  # failed sign-ins for a name before it has to wait
FIRST_WAIT = timedelta(seconds=30)  # then doubling with each failure...
LONGEST_WAIT = timedelta(minutes=15)  # ...up to this

# argon2id at OWASP's minimum (19 MiB, 2 passes, 1 lane): a sign-in costs a few tens of
# milliseconds and 19 MiB, which a small container can afford a few of at once.
# https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html#argon2id
_HASHER = PasswordHasher(time_cost=2, memory_cost=19 * 1024, parallelism=1)
# Each hash takes 19 MiB while it runs. Two at a time at most, so a burst of sign-in attempts
# (from anyone: unknown names cost the same) queues instead of taking the container's memory.
HASHING_AT_ONCE = 2
_hashing = threading.BoundedSemaphore(HASHING_AT_ONCE)


def _hash(password: str) -> str:
    with _hashing:
        return _HASHER.hash(password)


def _verify(password_hash: str, password: str) -> None:
    with _hashing:
        _HASHER.verify(password_hash, password)


# Checked when the name is unknown, so a wrong name takes as long as a wrong password.
_DUMMY_HASH = _hash(secrets.token_urlsafe(16))


class Role(StrEnum):
    ADULT = "adult"
    TEEN = "teen"

    @property
    def label(self) -> str:
        return self.value.capitalize()


class AccountError(ValueError):
    """A change to accounts that is not allowed, with a message for the person."""


@dataclass(frozen=True)
class Account:
    id: int
    eater_id: int
    name: str  # the Eater's name, which is also what they sign in with
    role: Role

    @property
    def is_adult(self) -> bool:
        return self.role is Role.ADULT


@dataclass(frozen=True)
class Session:
    account: Account
    csrf_token: str
    token_hash: str


def check_password(password: str) -> str:
    """A password that may be used, or AccountError saying why not."""
    if len(password) < MIN_PASSWORD:
        raise AccountError(f"A password needs at least {MIN_PASSWORD} characters")
    if len(password) > MAX_PASSWORD:
        raise AccountError(f"A password can be at most {MAX_PASSWORD} characters")
    return password


def hash_password(password: str) -> str:
    return _hash(check_password(password))


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _now() -> datetime:
    return datetime.now(UTC)


class AccountRepository:
    def __init__(self, database: str | Database | None = None):
        self._db, self._owned = attach(database)

    def __enter__(self) -> "AccountRepository":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        if self._owned:
            self._db.close()

    @property
    def database(self) -> Database:
        return self._db

    # --- reading ------------------------------------------------------------------------------

    _COLUMNS = (
        "SELECT a.id, a.eater_id, e.name, a.role FROM accounts a JOIN eaters e ON e.id = a.eater_id"
    )

    def _account(self, row) -> Account:
        account_id, eater_id, name, role = row
        return Account(account_id, eater_id, name, Role(role))

    def accounts(self) -> list[Account]:
        """Every account, by the order its Eater was added."""
        rows = self._db.execute(self._COLUMNS + " ORDER BY e.id").fetchall()
        return [self._account(row) for row in rows]

    def get(self, account_id: int) -> Account | None:
        row = self._db.execute(self._COLUMNS + " WHERE a.id = %s", (account_id,)).fetchone()
        return self._account(row) if row else None

    def for_eater(self, eater_id: int) -> Account | None:
        row = self._db.execute(self._COLUMNS + " WHERE a.eater_id = %s", (eater_id,)).fetchone()
        return self._account(row) if row else None

    def adult_count(self) -> int:
        (count,) = self._db.execute("SELECT COUNT(*) FROM accounts WHERE role = 'adult'").fetchone()
        return count

    # --- changing -----------------------------------------------------------------------------

    def create(self, eater_id: int, role: Role, password: str) -> Account:
        """Give an Eater an account. Children cannot have one; an Adult manages them."""
        role = Role(role)
        password_hash = hash_password(password)
        with EaterRepository(self._db) as eaters:
            eater = eaters.get(eater_id)
        if eater is None:
            raise AccountError("There is no such eater")
        if eater.category is Category.CHILD:
            raise AccountError(
                f"{eater.name} is a child. Children have no account: an Adult manages them"
            )
        try:
            with self._db.transaction():
                (account_id,) = self._db.execute(
                    "INSERT INTO accounts (eater_id, role, password_hash) VALUES (%s, %s, %s)"
                    " RETURNING id",
                    (eater_id, role.value, password_hash),
                ).fetchone()
        except psycopg.errors.UniqueViolation:
            raise AccountError(f"{eater.name} already has an account") from None
        return self.get(account_id)

    def set_role(self, account_id: int, role: Role) -> None:
        role = Role(role)
        with self._db.transaction():
            account = self._locked(account_id)
            if account.is_adult and role is not Role.ADULT and self.adult_count() == 1:
                raise AccountError("This is the only Adult. Make someone else an Adult first")
            self._db.execute(
                "UPDATE accounts SET role = %s WHERE id = %s", (role.value, account_id)
            )

    def set_password(self, account_id: int, password: str, keep_session: str | None = None) -> None:
        """Change a password and end the account's sessions, except `keep_session` (a token hash)
        so that someone changing their own password stays signed in where they did it."""
        password_hash = hash_password(password)
        with self._db.transaction():
            self._locked(account_id)
            self._db.execute(
                "UPDATE accounts SET password_hash = %s WHERE id = %s", (password_hash, account_id)
            )
            self._db.execute(
                "DELETE FROM sessions WHERE account_id = %s AND token_hash IS DISTINCT FROM %s",
                (account_id, keep_session),
            )

    def remove(self, account_id: int) -> None:
        """Remove an account (its Eater stays) and sign it out everywhere."""
        with self._db.transaction():
            account = self._locked(account_id)
            if account.is_adult and self.adult_count() == 1:
                raise AccountError("This is the only Adult, so it cannot be removed")
            self._db.execute("DELETE FROM accounts WHERE id = %s", (account_id,))

    def _locked(self, account_id: int) -> Account:
        account = self.get(account_id)
        if account is None:
            raise AccountError("There is no such account")
        return account

    # --- signing in ---------------------------------------------------------------------------

    def wait_before_trying(self, name: str, now: datetime | None = None) -> timedelta:
        """How long a sign-in as `name` must wait because of earlier failures (zero if none)."""
        row = self._db.execute(
            "SELECT failures, last_failed_at FROM sign_in_failures WHERE name_key = %s",
            (_name_key(name),),
        ).fetchone()
        if row is None or row[0] < FREE_FAILURES:
            return timedelta(0)
        failures, last = row
        wait = min(FIRST_WAIT * 2 ** (failures - FREE_FAILURES), LONGEST_WAIT)
        return max(last + wait - (now or _now()), timedelta(0))

    def check_sign_in(
        self, name: str, password: str, now: datetime | None = None
    ) -> Account | None:
        """The account if the name and password match, else None (and the failure counts).

        Callers check `wait_before_trying` first. An unknown name takes as long as a wrong
        password and counts the same, so neither says whether the name has an account.
        """
        row = self._db.execute(
            "SELECT a.id, a.password_hash FROM accounts a JOIN eaters e ON e.id = a.eater_id"
            " WHERE lower(e.name) = lower(%s)",
            (" ".join(name.split()),),
        ).fetchone()
        try:
            _verify(row[1] if row else _DUMMY_HASH, password)
            matched = row is not None
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            matched = False
        with self._db.transaction():
            if not matched:
                self._db.execute(
                    "INSERT INTO sign_in_failures (name_key, failures, last_failed_at)"
                    " VALUES (%s, 1, %s) ON CONFLICT (name_key) DO UPDATE"
                    " SET failures = sign_in_failures.failures + 1,"
                    " last_failed_at = excluded.last_failed_at",
                    (_name_key(name), now or _now()),
                )
                return None
            self._db.execute("DELETE FROM sign_in_failures WHERE name_key = %s", (_name_key(name),))
        if _HASHER.check_needs_rehash(row[1]):
            self._db.execute(
                "UPDATE accounts SET password_hash = %s WHERE id = %s",
                (_hash(password), row[0]),
            )
        return self.get(row[0])

    def start_session(self, account_id: int, now: datetime | None = None) -> tuple[str, Session]:
        """A new session for the account: the token for the cookie, and the session."""
        token = secrets.token_urlsafe(32)
        csrf = secrets.token_urlsafe(32)
        expires = (now or _now()) + timedelta(days=SESSION_DAYS)
        with self._db.transaction():
            self._db.execute(
                "DELETE FROM sessions WHERE expires_at < %s", (now or _now(),)
            )  # tidy expired ones while here
            self._db.execute(
                "INSERT INTO sessions (token_hash, account_id, csrf_token, expires_at)"
                " VALUES (%s, %s, %s, %s)",
                (_hash_token(token), account_id, csrf, expires),
            )
        return token, Session(self.get(account_id), csrf, _hash_token(token))

    def session(self, token: str | None, now: datetime | None = None) -> Session | None:
        """The session a cookie token belongs to, if it exists and has not expired."""
        if not token or len(token) > 100:
            return None
        row = self._db.execute(
            "SELECT s.account_id, s.csrf_token, s.token_hash FROM sessions s"
            " WHERE s.token_hash = %s AND s.expires_at > %s",
            (_hash_token(token), now or _now()),
        ).fetchone()
        if row is None:
            return None
        account = self.get(row[0])
        return Session(account, row[1], row[2]) if account else None

    def end_session(self, token: str | None) -> None:
        if token:
            self._db.execute("DELETE FROM sessions WHERE token_hash = %s", (_hash_token(token),))


def _name_key(name: str) -> str:
    return " ".join(name.split()).lower()[:100]


# --- the command line, for the first Adult and for getting back in --------------------------------


def _read_password(from_stdin: bool) -> str:
    if from_stdin:
        return sys.stdin.readline().rstrip("\n")
    first = getpass.getpass("New password: ")
    if getpass.getpass("Same password again: ") != first:
        raise AccountError("The two passwords are not the same")
    return first


def create_adult(name: str, password: str, category: Category = Category.WOMAN) -> Account:
    """Make the first Adult. Refused once any Adult exists: from then on, Adults add accounts
    in the app. The Eater is used if one has this name, else added with `category`."""
    with AccountRepository() as accounts, EaterRepository(accounts.database) as eaters:
        if accounts.adult_count():
            raise AccountError(
                "An Adult account already exists. Sign in as an Adult to add accounts, or use"
                " reset-password if nobody can sign in"
            )
        check_password(password)
        eater = next((e for e in eaters.list() if e.name.lower() == name.strip().lower()), None)
        if eater is None:
            eater = eaters.add(name, category)
        return accounts.create(eater.id, Role.ADULT, password)


def reset_password(name: str, password: str) -> Account:
    """Set a new password for the account signing in as `name`, ending its sessions."""
    with AccountRepository() as accounts:
        account = next(
            (a for a in accounts.accounts() if a.name.lower() == name.strip().lower()), None
        )
        if account is None:
            raise AccountError(f"Nobody called {name} has an account")
        accounts.set_password(account.id, password)
        return account


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m emu_the_menu.accounts",
        description="Make the first Adult account, or set a password when nobody can sign in.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    first = commands.add_parser("create-adult", help="the first Adult, on a new install")
    first.add_argument("name", help="the Eater to sign in as (added if new)")
    first.add_argument(
        "--category",
        choices=[Category.MAN.value, Category.WOMAN.value],
        default=Category.WOMAN.value,
        help="the calorie Category, if the Eater is new (default: woman)",
    )
    reset = commands.add_parser("reset-password", help="a new password for any account")
    reset.add_argument("name")
    for command in (first, reset):
        command.add_argument(
            "--password-stdin", action="store_true", help="read the password from standard input"
        )
    args = parser.parse_args(argv)
    try:
        password = _read_password(args.password_stdin)
        if args.command == "create-adult":
            account = create_adult(args.name, password, Category(args.category))
            print(f"{account.name} is an Adult and can sign in now.")
        else:
            account = reset_password(args.name, password)
            print(f"{account.name} has a new password and was signed out everywhere.")
    except ValueError as exc:  # AccountError, or a name the Eaters refuse
        print(f"Not done: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
