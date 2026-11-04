"""Discovery and pairing tests."""

from __future__ import annotations

from dataclasses import replace

import pytest
from custom_components.eliis.api.discovery import pair_children, parse_init
from custom_components.eliis.api.errors import EliisSchemaError

from .fixtures import CHILD_ID, INIT_PAYLOAD, INSTITUTION_ID, SECOND_CHILD_ID


def test_parse_init_uses_explicit_user_id() -> None:
    """Account identity prefers an explicit opaque user identifier."""
    discovery = parse_init(INIT_PAYLOAD)
    assert len(discovery.institutions) == 1
    assert discovery.institutions[0].id == INSTITUTION_ID
    assert {child.id for child in discovery.children} == {
        CHILD_ID,
        SECOND_CHILD_ID,
    }
    assert discovery.account_id != "9009"
    assert len(discovery.account_id) == 24


def test_parse_init_without_user_id_uses_institution_set() -> None:
    """Institution-based identity is stable and never a name."""
    payload = {key: value for key, value in INIT_PAYLOAD.items() if key != "user"}
    first = parse_init(payload)
    second = parse_init(payload)
    assert first.account_id == second.account_id
    assert len(first.account_id) == 24


def test_parse_init_requires_an_institution() -> None:
    """An account with no visible institution cannot be set up."""
    with pytest.raises(EliisSchemaError):
        parse_init({"kindergartens": [], "children": [], "user": {}})


class FakeProbeClient:
    """Probe client with a fixed readable pair."""

    def __init__(self, allowed: set[tuple[str, str]]) -> None:
        self.allowed = allowed
        self.calls: list[tuple[str, str]] = []

    async def async_probe_child(self, institution_id: str, child_id: str) -> bool:
        self.calls.append((institution_id, child_id))
        return (institution_id, child_id) in self.allowed


@pytest.mark.asyncio
async def test_pair_children_uses_single_institution_without_probes() -> None:
    """One visible institution pairs children without extra requests."""
    discovery = parse_init(INIT_PAYLOAD)
    client = FakeProbeClient(set())
    paired = await pair_children(client, discovery)
    assert {child.institution_id for child in paired} == {INSTITUTION_ID}
    assert client.calls == []


@pytest.mark.asyncio
async def test_pair_children_probes_only_when_needed() -> None:
    """Multiple institutions are verified through the profile route."""
    discovery = parse_init(INIT_PAYLOAD)
    discovery = replace(
        discovery,
        institutions=(
            *discovery.institutions,
            replace(discovery.institutions[0], id="1002", name="Other"),
        ),
        children=tuple(
            replace(child, institution_id=None) for child in discovery.children
        ),
    )
    client = FakeProbeClient({("1002", CHILD_ID), ("1001", SECOND_CHILD_ID)})
    paired = await pair_children(client, discovery)
    by_id = {child.id: child.institution_id for child in paired}
    assert by_id[CHILD_ID] == "1002"
    assert by_id[SECOND_CHILD_ID] == "1001"


@pytest.mark.asyncio
async def test_pair_children_leaves_unverified_children_out() -> None:
    """Unverified children are not guessed into a selection."""
    discovery = parse_init(INIT_PAYLOAD)
    discovery = replace(
        discovery,
        institutions=(
            *discovery.institutions,
            replace(discovery.institutions[0], id="1002"),
        ),
        children=tuple(
            replace(child, institution_id=None) for child in discovery.children
        ),
    )
    client = FakeProbeClient({("1001", CHILD_ID)})
    paired = await pair_children(client, discovery)
    assert [child.id for child in paired] == [CHILD_ID]
