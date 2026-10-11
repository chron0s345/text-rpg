"""Accounts and characters (ARCHITECTURE.md, "Grundannahmen", API: /register, /login,
/characters).

- Passwords are hashed with argon2 (in a worker thread: hashing is deliberately slow). Unknown
  usernames are checked against a dummy hash, so response times do not reveal which exist.
- Usernames and character names are unique ignoring case (database indexes); names are limited
  to letters so they render safely and are easy to type in commands.
- New characters start at level 1 in ``rules.start_room_id`` with the level-1 base values.
- Characters are soft-deleted, and only while offline. Creating, deleting and logging in for one
  account are serialized by the same per-account advisory lock (character limit, online check).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

import anyio
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlmodel import col

from app.content.registry import ContentRegistry
from app.db.models import Account, Player
from app.services.session_service import UnknownCharacterError, lock_account

USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]{3,32}$")
CHARACTER_NAME_PATTERN = re.compile(r"^[A-Za-zÄÖÜäöüß]{3,20}$")
MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 128


class UsernameTakenError(Exception):
    pass


class InvalidCredentialsError(Exception):
    pass


class CharacterNameTakenError(Exception):
    pass


class CharacterLimitError(Exception):
    pass


class CharacterOnlineError(Exception):
    """Only offline characters can be deleted."""


@dataclass(frozen=True)
class CharacterSummary:
    id: UUID
    name: str
    level: int
    state: str
    online: bool
    room_id: str


def utc_now() -> datetime:
    return datetime.now(UTC)


class AccountService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        content: ContentRegistry,
        *,
        clock: Callable[[], datetime] = utc_now,
        hasher: PasswordHasher | None = None,
    ) -> None:
        self.sessions = sessions
        self.content = content
        self.clock = clock
        self.hasher = hasher or PasswordHasher()
        self._dummy_hash: str | None = None

    # --- accounts -------------------------------------------------------------------------------

    async def register(self, username: str, password: str) -> UUID:
        password_hash = await anyio.to_thread.run_sync(self.hasher.hash, password)
        account = Account(username=username, password_hash=password_hash)
        try:
            async with self.sessions() as session, session.begin():
                session.add(account)
        except IntegrityError as exc:
            raise UsernameTakenError(username) from exc
        return account.id

    async def authenticate(self, username: str, password: str) -> UUID:
        async with self.sessions() as session:
            account = await session.scalar(
                select(Account).where(func.lower(col(Account.username)) == username.lower())
            )
        stored = account.password_hash if account is not None else await self._dummy()
        if not await anyio.to_thread.run_sync(self._verify, stored, password) or account is None:
            raise InvalidCredentialsError(username)
        if self.hasher.check_needs_rehash(stored):
            await self._rehash(account.id, password)
        return account.id

    def _verify(self, stored: str, password: str) -> bool:
        try:
            return self.hasher.verify(stored, password)
        except (VerificationError, InvalidHashError):
            return False

    async def _dummy(self) -> str:
        if self._dummy_hash is None:
            self._dummy_hash = await anyio.to_thread.run_sync(self.hasher.hash, "not a password")
        return self._dummy_hash

    async def _rehash(self, account_id: UUID, password: str) -> None:
        password_hash = await anyio.to_thread.run_sync(self.hasher.hash, password)
        async with self.sessions() as session, session.begin():
            account = await session.get(Account, account_id)
            if account is not None:
                account.password_hash = password_hash

    async def account_exists(self, account_id: UUID) -> bool:
        async with self.sessions() as session:
            return await session.get(Account, account_id) is not None

    # --- characters -----------------------------------------------------------------------------

    async def characters(self, account_id: UUID) -> list[CharacterSummary]:
        async with self.sessions() as session:
            rows = await session.scalars(
                select(Player)
                .where(col(Player.account_id) == account_id, col(Player.deleted_at).is_(None))
                .order_by(col(Player.name))
            )
            return [
                CharacterSummary(p.id, p.name, p.level, p.state, p.online, p.current_room_id)
                for p in rows.all()
            ]

    async def active_character(self, account_id: UUID) -> UUID | None:
        """The account's character that is in the world (at most one, database index)."""
        async with self.sessions() as session:
            return await session.scalar(
                select(col(Player.id)).where(
                    col(Player.account_id) == account_id,
                    col(Player.online),
                    col(Player.deleted_at).is_(None),
                )
            )

    async def create_character(self, account_id: UUID, name: str) -> UUID:
        rules = self.content.rules
        start = rules.level_rule(1)
        try:
            async with self.sessions() as session, session.begin():
                await lock_account(session, account_id)
                count = await session.scalar(
                    select(func.count()).where(
                        col(Player.account_id) == account_id, col(Player.deleted_at).is_(None)
                    )
                )
                if (count or 0) >= rules.max_characters_per_account:
                    raise CharacterLimitError(account_id)
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
                await session.flush()
        except IntegrityError as exc:
            raise CharacterNameTakenError(name) from exc
        return player.id

    async def delete_character(self, account_id: UUID, player_id: UUID) -> None:
        async with self.sessions() as session, session.begin():
            await lock_account(session, account_id)
            player = await session.scalar(
                select(Player)
                .where(
                    col(Player.id) == player_id,
                    col(Player.account_id) == account_id,
                    col(Player.deleted_at).is_(None),
                )
                .with_for_update()
            )
            if player is None:
                raise UnknownCharacterError(player_id)
            if player.online:
                raise CharacterOnlineError(player_id)
            player.deleted_at = self.clock()
