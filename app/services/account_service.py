"""Accounts and character management (ARCHITECTURE.md, "API": register, login, characters).

Passwords are hashed with argon2. Hashing is deliberately slow, so it runs in a worker thread
and never blocks the event loop.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Final
from uuid import UUID

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlmodel import col

from app.content.registry import ContentRegistry
from app.db.models import Account, Player
from app.services.action_service import utc_now
from app.services.login_guard import LoginGuard

# Letters only (incl. German umlauts): names show up in every room and chat line.
CHARACTER_NAME: Final = re.compile(r"[A-Za-zÄÖÜäöüß]{3,20}")
USERNAME: Final = re.compile(r"[A-Za-z0-9_.-]{3,32}")
MIN_PASSWORD_LENGTH: Final = 8
MAX_PASSWORD_LENGTH: Final = 128


class AccountError(Exception):
    """Base class; the message is shown to the user (German)."""


class UsernameTakenError(AccountError):
    pass


class InvalidInputError(AccountError):
    pass


class CharacterNameTakenError(AccountError):
    pass


class CharacterLimitError(AccountError):
    pass


class CharacterNotFoundError(AccountError):
    pass


class CharacterOnlineError(AccountError):
    pass


@dataclass(frozen=True)
class CharacterInfo:
    id: UUID
    name: str
    level: int
    state: str
    room_id: str
    online: bool


def _info(row: Player) -> CharacterInfo:
    return CharacterInfo(
        id=row.id,
        name=row.name,
        level=row.level,
        state=row.state,
        room_id=row.current_room_id,
        online=row.online,
    )


class AccountService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        content: ContentRegistry,
        *,
        hasher: PasswordHasher | None = None,
        clock: Callable[[], datetime] = utc_now,
        guard: LoginGuard | None = None,
    ) -> None:
        self.sessions = sessions
        self.content = content
        # limits login/registration attempts (None: unlimited, e.g. in service tests)
        self.guard = guard
        self.hasher = hasher or PasswordHasher()
        self.clock = clock
        # verified against when the username does not exist: same cost, no user enumeration
        self._dummy_hash = self.hasher.hash("not a real password")

    # --- accounts ---------------------------------------------------------------------------

    async def register(
        self, username: str, password: str, *, address: str | None = None
    ) -> Account:
        """``address``: the client, for the registration limit per address."""
        if not USERNAME.fullmatch(username):
            raise InvalidInputError("Benutzername: 3–32 Zeichen, nur Buchstaben, Ziffern und _ . -")
        if not MIN_PASSWORD_LENGTH <= len(password) <= MAX_PASSWORD_LENGTH:
            raise InvalidInputError(
                f"Passwort: {MIN_PASSWORD_LENGTH}–{MAX_PASSWORD_LENGTH} Zeichen."
            )
        if self.guard is not None:
            await self.guard.registration_attempt(address)  # before the expensive hash
        password_hash = await asyncio.to_thread(self.hasher.hash, password)
        account = Account(username=username, password_hash=password_hash, created_at=self.clock())
        try:
            async with self.sessions() as session, session.begin():
                session.add(account)
        except IntegrityError as exc:  # unique, case-insensitive
            raise UsernameTakenError("Dieser Benutzername ist vergeben.") from exc
        return account

    async def login(
        self, username: str, password: str, *, address: str | None = None
    ) -> Account | None:
        """``authenticate`` behind the attempt limit; raises ``TooManyAttemptsError`` before
        any hashing when the user name or the client address ran out of attempts."""
        if self.guard is not None:
            await self.guard.login_attempt(username, address)
        account = await self.authenticate(username, password)
        if account is not None and self.guard is not None:
            await self.guard.login_succeeded(username, address)
        return account

    async def authenticate(self, username: str, password: str) -> Account | None:
        async with self.sessions() as session:
            account = (
                await session.scalars(
                    select(Account).where(func.lower(col(Account.username)) == username.lower())
                )
            ).first()
        if account is None:
            await asyncio.to_thread(self._verify, self._dummy_hash, password)
            return None
        if not await asyncio.to_thread(self._verify, account.password_hash, password):
            return None
        if self.hasher.check_needs_rehash(account.password_hash):
            await self._rehash(account.id, password)
        return account

    def _verify(self, password_hash: str, password: str) -> bool:
        try:
            return self.hasher.verify(password_hash, password)
        except (VerificationError, InvalidHashError):
            return False

    async def _rehash(self, account_id: UUID, password: str) -> None:
        password_hash = await asyncio.to_thread(self.hasher.hash, password)
        async with self.sessions() as session, session.begin():
            account = await session.get(Account, account_id)
            if account is not None:
                account.password_hash = password_hash

    # --- characters -------------------------------------------------------------------------

    async def list_characters(self, account_id: UUID) -> list[CharacterInfo]:
        async with self.sessions() as session:
            rows = await session.scalars(
                select(Player)
                .where(col(Player.account_id) == account_id, col(Player.deleted_at).is_(None))
                .order_by(col(Player.name))
            )
            return [_info(row) for row in rows.all()]

    async def create_character(self, account_id: UUID, name: str) -> CharacterInfo:
        """New character with level-1 stats in ``start_room_id`` (also its first checkpoint)."""
        if not CHARACTER_NAME.fullmatch(name):
            raise InvalidInputError("Name: 3–20 Buchstaben.")
        rules = self.content.rules
        start = rules.level_rule(1)
        try:
            async with self.sessions() as session, session.begin():
                # account row lock: concurrent creates cannot exceed the character limit
                account = await session.get(Account, account_id, with_for_update=True)
                if account is None:
                    raise CharacterNotFoundError("Account existiert nicht.")
                count = await session.scalar(
                    select(func.count())
                    .select_from(Player)
                    .where(col(Player.account_id) == account_id, col(Player.deleted_at).is_(None))
                )
                if (count or 0) >= rules.max_characters_per_account:
                    raise CharacterLimitError(
                        f"Höchstens {rules.max_characters_per_account} Charaktere pro Account."
                    )
                player = Player(
                    account_id=account_id,
                    name=name,
                    hp=start.max_hp,
                    base_max_hp=start.max_hp,
                    base_attack=start.attack,
                    base_defense=start.defense,
                    current_room_id=rules.start_room_id,
                    checkpoint_room_id=rules.start_room_id,
                )
                session.add(player)
        except IntegrityError as exc:  # unique, case-insensitive among non-deleted characters
            raise CharacterNameTakenError("Dieser Name ist vergeben.") from exc
        return _info(player)

    async def owned_character(self, account_id: UUID, character_id: UUID) -> CharacterInfo:
        async with self.sessions() as session:
            row = await session.get(Player, character_id)
            if row is None or row.account_id != account_id or row.deleted_at is not None:
                raise CharacterNotFoundError("Diesen Charakter gibt es nicht.")
            return _info(row)

    async def delete_character(self, account_id: UUID, character_id: UUID) -> None:
        """Soft delete, only while offline (the name becomes free again)."""
        async with self.sessions() as session, session.begin():
            row = await session.get(Player, character_id, with_for_update=True)
            if row is None or row.account_id != account_id or row.deleted_at is not None:
                raise CharacterNotFoundError("Diesen Charakter gibt es nicht.")
            if row.online:
                raise CharacterOnlineError("Logge den Charakter zuerst aus.")
            row.deleted_at = self.clock()
