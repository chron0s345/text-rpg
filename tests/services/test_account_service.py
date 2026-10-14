"""AccountService against PostgreSQL: registration, authentication, character management."""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest
from argon2 import PasswordHasher
from sqlalchemy import update
from sqlmodel import col

from app.db.models import Account, Player
from app.services.account_service import (
    AccountService,
    CharacterLimitError,
    CharacterNameTakenError,
    CharacterNotFoundError,
    CharacterOnlineError,
    InvalidInputError,
    UsernameTakenError,
    WrongPasswordError,
)
from tests.services.conftest import ServiceHarness

FAST_HASHER = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1)


@pytest.fixture
def accounts(harness: ServiceHarness) -> AccountService:
    return AccountService(harness.service.sessions, harness.content, hasher=FAST_HASHER)


async def new_account(
    h: ServiceHarness, accounts: AccountService, password: str = "geheim123"
) -> Account:
    account = await accounts.register(f"user_{uuid4().hex[:8]}", password)
    h.committed.accounts.append(account.id)
    return account


def unique_name() -> str:
    return "Held" + "".join(chr(ord("a") + int(c, 16)) for c in uuid4().hex[:8])


async def test_register_and_authenticate(harness: ServiceHarness, accounts: AccountService) -> None:
    account = await new_account(harness, accounts)
    assert account.password_hash != "geheim123"
    assert (await accounts.authenticate(account.username.upper(), "geheim123")) is not None
    assert await accounts.authenticate(account.username, "falsch!!!") is None
    assert await accounts.authenticate("gibt_es_nicht", "geheim123") is None


async def test_usernames_are_unique_ignoring_case(
    harness: ServiceHarness, accounts: AccountService
) -> None:
    account = await new_account(harness, accounts)
    with pytest.raises(UsernameTakenError):
        await accounts.register(account.username.upper(), "geheim123")


@pytest.mark.parametrize(
    ("username", "password"),
    [("ab", "geheim123"), ("mit leerzeichen", "geheim123"), ("gültig", "kurz")],
)
async def test_register_validates_input(
    accounts: AccountService, username: str, password: str
) -> None:
    with pytest.raises(InvalidInputError):
        await accounts.register(username, password)


async def test_new_character_starts_with_level_one_stats_in_start_room(
    harness: ServiceHarness, accounts: AccountService
) -> None:
    account = await new_account(harness, accounts)
    rules = harness.content.rules
    character = await accounts.create_character(account.id, unique_name())
    assert (character.level, character.state, character.online) == (1, "exploring", False)
    assert character.room_id == rules.start_room_id
    async with harness.committed.session() as db:
        row = await db.get(Player, character.id)
    assert row is not None
    start = rules.level_rule(1)
    assert (row.hp, row.base_max_hp, row.base_attack, row.base_defense) == (
        start.max_hp,
        start.max_hp,
        start.attack,
        start.defense,
    )
    assert row.checkpoint_room_id == rules.start_room_id
    assert [c.id for c in await accounts.list_characters(account.id)] == [character.id]


@pytest.mark.parametrize("name", ["Al", "Anna1", "<b>Bob</b>", "x" * 21])
async def test_character_names_are_restricted(
    harness: ServiceHarness, accounts: AccountService, name: str
) -> None:
    account = await new_account(harness, accounts)
    with pytest.raises(InvalidInputError):
        await accounts.create_character(account.id, name)


async def test_character_names_are_unique_ignoring_case(
    harness: ServiceHarness, accounts: AccountService
) -> None:
    first, second = await new_account(harness, accounts), await new_account(harness, accounts)
    name = unique_name()
    await accounts.create_character(first.id, name)
    with pytest.raises(CharacterNameTakenError):
        await accounts.create_character(second.id, name.upper())


async def test_character_limit_holds_under_concurrency(
    harness: ServiceHarness, accounts: AccountService
) -> None:
    account = await new_account(harness, accounts)
    limit = harness.content.rules.max_characters_per_account
    results = await asyncio.gather(
        *(accounts.create_character(account.id, unique_name()) for _ in range(limit + 2)),
        return_exceptions=True,
    )
    assert sum(not isinstance(r, Exception) for r in results) == limit
    assert all(isinstance(r, CharacterLimitError) for r in results if isinstance(r, Exception))


async def test_delete_only_offline_and_frees_the_name(
    harness: ServiceHarness, accounts: AccountService
) -> None:
    account = await new_account(harness, accounts)
    name = unique_name()
    character = await accounts.create_character(account.id, name)

    async with harness.committed.session() as db, db.begin():
        await db.execute(update(Player).where(col(Player.id) == character.id).values(online=True))
    with pytest.raises(CharacterOnlineError):
        await accounts.delete_character(account.id, character.id)

    async with harness.committed.session() as db, db.begin():
        await db.execute(update(Player).where(col(Player.id) == character.id).values(online=False))
    await accounts.delete_character(account.id, character.id)
    assert await accounts.list_characters(account.id) == []
    await accounts.create_character(account.id, name)  # soft-deleted names are free again


async def test_characters_of_other_accounts_are_invisible(
    harness: ServiceHarness, accounts: AccountService
) -> None:
    owner, stranger = await new_account(harness, accounts), await new_account(harness, accounts)
    character = await accounts.create_character(owner.id, unique_name())
    with pytest.raises(CharacterNotFoundError):
        await accounts.delete_character(stranger.id, character.id)
    with pytest.raises(CharacterNotFoundError):
        await accounts.owned_character(stranger.id, character.id)


async def test_set_password_replaces_it_and_raises_the_session_version(
    harness: ServiceHarness, accounts: AccountService
) -> None:
    account = await new_account(harness, accounts)
    assert await accounts.session_version(account.id) == 0

    assert await accounts.set_password(account.id, "ganz-neu-42") == 1

    assert await accounts.session_version(account.id) == 1
    assert await accounts.authenticate(account.username, "geheim123") is None
    assert await accounts.authenticate(account.username, "ganz-neu-42") is not None
    with pytest.raises(InvalidInputError):
        await accounts.set_password(account.id, "kurz")
    assert await accounts.session_version(account.id) == 1
    assert await accounts.session_version(uuid4()) is None


async def test_check_password(harness: ServiceHarness, accounts: AccountService) -> None:
    account = await new_account(harness, accounts)
    await accounts.check_password(account.id, "geheim123")
    with pytest.raises(WrongPasswordError):
        await accounts.check_password(account.id, "falsch!!!")
    with pytest.raises(WrongPasswordError):
        await accounts.check_password(uuid4(), "geheim123")
