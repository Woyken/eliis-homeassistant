"""Account discovery and institution/child pairing."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Any

from ..util import opaque_id, scalar_id, scalar_text
from .errors import EliisSchemaError
from .models import Child, Discovery, Institution

if TYPE_CHECKING:
    from .client import EliisClient

_MAX_PROBES = 8


def parse_init(payload: Any, *, fallback_seed: str | None = None) -> Discovery:
    """Parse the account init payload into institutions and children.

    The account identifier prefers an explicit user identifier. When none is
    evidenced, a hash of the fallback seed (the login name) and the visible
    institution set is used. Home names or e-mail addresses are never stored;
    only the opaque hash leaves this module.
    """
    if not isinstance(payload, dict):
        raise EliisSchemaError("Account discovery response was not an object")

    malformed = False
    institutions: list[Institution] = []
    seen_institutions: set[str] = set()
    raw_institutions = payload.get("kindergartens")
    if not isinstance(raw_institutions, list):
        malformed = True
        raw_institutions = []
    for item in raw_institutions:
        if not isinstance(item, dict):
            malformed = True
            continue
        institution_id = scalar_id(item.get("id"))
        if institution_id is None:
            malformed = True
            continue
        if institution_id in seen_institutions:
            continue
        seen_institutions.add(institution_id)
        institutions.append(Institution(institution_id, scalar_text(item.get("name"))))

    children: list[Child] = []
    seen_children: set[str] = set()
    raw_children = payload.get("children")
    if not isinstance(raw_children, list):
        malformed = True
        raw_children = []
    for item in raw_children:
        if not isinstance(item, dict):
            malformed = True
            continue
        child_id = scalar_id(item.get("id"))
        if child_id is None:
            malformed = True
            continue
        if child_id in seen_children:
            continue
        seen_children.add(child_id)
        child_institution = scalar_id(item.get("kindergarten_id"))
        children.append(
            Child(
                child_id,
                scalar_text(item.get("name")),
                child_institution,
            )
        )

    user = payload.get("user")
    user_id = scalar_id(user.get("id")) if isinstance(user, dict) else None
    user_id = user_id or (
        scalar_id(user.get("user_id")) if isinstance(user, dict) else None
    )
    if not institutions and not children:
        raise EliisSchemaError("Account discovery returned no institutions")
    if user_id is not None:
        account_id = opaque_id("user", user_id)
    elif fallback_seed is not None:
        scope = [item.id for item in institutions] or [item.id for item in children]
        account_id = opaque_id("account", fallback_seed, *sorted(scope))
    elif institutions:
        account_id = opaque_id(
            "institutions", *sorted(item.id for item in institutions)
        )
    else:
        account_id = opaque_id("children", *sorted(item.id for item in children))

    return Discovery(
        institutions=tuple(institutions),
        children=tuple(children),
        account_id=account_id,
        malformed=malformed,
    )


async def pair_children(
    client: EliisClient,
    discovery: Discovery,
    *,
    known_pairs: dict[str, str] | None = None,
) -> tuple[Child, ...]:
    """Pair children to institutions using explicit or verified evidence.

    Explicit ``kindergarten_id`` values and stored selections are preferred.
    Remaining children are verified with the documented child-profile route,
    with a small fixed probe budget. Unverified children are left unselected
    rather than guessed.
    """
    institutions = {item.id for item in discovery.institutions}
    known_pairs = known_pairs or {}
    paired: list[Child] = []
    unpaired: list[Child] = []
    probes = 0

    for child in discovery.children:
        explicit = child.institution_id
        if explicit in institutions:
            paired.append(child)
            continue
        known = known_pairs.get(child.id)
        if known in institutions:
            paired.append(replace(child, institution_id=known))
            continue
        unpaired.append(child)

    if len(institutions) == 1:
        only = next(iter(institutions))
        paired.extend(replace(child, institution_id=only) for child in unpaired)
        return tuple(paired)

    for child in unpaired:
        for institution_id in sorted(institutions):
            if probes >= _MAX_PROBES:
                return tuple(paired)
            probes += 1
            if await client.async_probe_child(institution_id, child.id):
                paired.append(replace(child, institution_id=institution_id))
                break

    return tuple(paired)
