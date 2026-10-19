import uuid

import pytest

from quadra import cards
from quadra.sessions import create_session, get_client


def _quadrants(session_id: str) -> dict[str, str]:
    rows = (
        get_client()
        .table("quadrants")
        .select("id, name, canvases!inner(workspace_id)")
        .eq("canvases.workspace_id", session_id)
        .execute()
        .data
    )
    return {row["name"]: row["id"] for row in rows}


def _seed_session(count: int) -> tuple[str, list[str]]:
    """A session with `count` active cards, a soft-deleted one and a foreign one.

    Returns the session id and the contents of the active cards in listing order.
    """
    session_id = create_session("Pagination").session_id
    quadrants = _quadrants(session_id)
    low, high = sorted(str(uuid.uuid4()) for _ in range(2))
    # (quadrant, index, content, card id) in the order list_cards must return them.
    layout = [
        ("IS", 0, "is-0", None),
        ("IS", 1, "is-1-low", low),
        ("IS", 1, "is-1-high", high),
        ("IS", 2, "is-2", None),
        ("DOES", 0, "does-0", None),
    ][:count]
    # Insert out of order so the result cannot depend on insertion order.
    for quadrant, index, content, card_id in reversed(layout):
        cards.insert_card(
            session_id, quadrants[quadrant], index, content, "Alice", card_id=card_id
        )
    deleted = cards.insert_card(session_id, quadrants["IS"], 9, "deleted", "Alice")
    cards.soft_delete_card(session_id, deleted.id)
    other_id = create_session("Other").session_id
    cards.insert_card(other_id, _quadrants(other_id)["IS"], 0, "elsewhere", "Alice")
    return session_id, [content for _, _, content, _ in layout]


@pytest.mark.parametrize("count", [5, 4], ids=["partial last page", "exact multiple"])
def test_list_cards_reads_every_page_in_order_without_duplicates(monkeypatch, count):
    session_id, expected = _seed_session(count)
    monkeypatch.setattr(cards, "_PAGE_SIZE", 2)

    listed = cards.list_cards(session_id)

    assert [card.content for card in listed] == expected
    assert len({card.id for card in listed}) == count
    assert all(card.deleted_at is None for card in listed)


def test_default_page_size_is_one_thousand():
    assert cards._PAGE_SIZE == 1000


def test_list_cards_survives_soft_delete_between_pages(monkeypatch):
    session_id, expected = _seed_session(5)
    monkeypatch.setattr(cards, "_PAGE_SIZE", 2)
    fetch_page = cards._fetch_page
    pages = []

    def delete_after_first_page(session, after_id, client):
        page = fetch_page(session, after_id, client)
        pages.append(page)
        if len(pages) == 1:
            cards.soft_delete_card(session_id, page[0]["id"], client)
        return page

    monkeypatch.setattr(cards, "_fetch_page", delete_after_first_page)

    listed = cards.list_cards(session_id)

    removed = pages[0][0]["content"]
    contents = [card.content for card in listed]
    # The page already read may still carry the card deleted afterwards.
    assert [c for c in contents if c != removed] == [c for c in expected if c != removed]
    assert len(set(card.id for card in listed)) == len(listed)
