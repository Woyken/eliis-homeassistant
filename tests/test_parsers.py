"""Parser tests for defensive Eliis payload handling."""

from __future__ import annotations

from datetime import UTC, date, datetime

from custom_components.eliis.api.parsers import (
    latest_feed_summary,
    menu_day_for_date,
    parse_child_day,
    parse_events,
    parse_feed,
    parse_menu,
    parse_notification_count,
    parse_status_history,
    sanitize_text,
)

from .fixtures import (
    CHILD_CALENDAR_PAYLOAD,
    EVENTS_PAYLOAD,
    FEED_PAYLOAD,
    INSTITUTION_ID,
    MENU_PAYLOAD,
    STATUS_LOG_PAYLOAD,
    TODAY,
)


def test_notification_count_valid_and_invalid() -> None:
    """Only non-negative integral values become counts."""
    assert parse_notification_count({"notifications_count": 4}).count == 4
    assert parse_notification_count({"notifications_count": "4"}).count == 4
    assert parse_notification_count({"notifications_count": 4.0}).count == 4

    for payload in (
        {"notifications_count": None},
        {"notifications_count": True},
        {"notifications_count": -1},
        {"notifications_count": "many"},
        {},
        [],
    ):
        parsed = parse_notification_count(payload)
        assert parsed.count is None
        assert parsed.malformed is True


def test_sanitize_text_strips_markup_and_bounds_length() -> None:
    """Menu and event text is plain, readable and bounded."""
    assert sanitize_text("<b>Soup</b><br/>Bread") == "Soup\nBread"
    assert sanitize_text("  a   b  ") == "a b"
    assert sanitize_text("<script>alert(1)</script>") is None
    assert sanitize_text("x" * 20, max_length=10) == "x" * 10


def test_parse_menu_distinguishes_missing_and_empty() -> None:
    """Null and empty leaves are skipped without becoming dishes."""
    result = parse_menu(MENU_PAYLOAD)
    assert result.malformed is False
    assert len(result.days) == 2

    first = menu_day_for_date(result, date(2026, 11, 2))
    assert first is not None
    assert [meal.key for meal in first.meals] == ["breakfast", "lunch", "supper"]
    assert first.meals[1].text == "Soup and bread"

    second = menu_day_for_date(result, date(2026, 11, 3))
    assert second is not None
    assert [meal.key for meal in second.meals] == ["breakfast", "lunch"]
    assert second.meals[0].text == "Sandwich"


def test_parse_menu_marks_non_string_leaves_malformed() -> None:
    """Structured values are not guessed into dish text."""
    result = parse_menu(
        {
            "days": [
                {
                    "date": "2026-11-03",
                    "breakfast": {"name": "Porridge"},
                    "lunch": "Soup",
                }
            ]
        }
    )
    assert result.malformed is True
    assert result.days[0].meals[0].key == "lunch"


def test_parse_feed_uses_latest_fetched_diary_date() -> None:
    """Evaluations-only and empty older groups do not advance summaries."""
    page = parse_feed(FEED_PAYLOAD, fetched_at=datetime(2026, 11, 3, tzinfo=UTC))
    latest, count = latest_feed_summary(page)
    assert latest == date(2026, 11, 3)
    assert count == 2
    assert page.next_date == "2026-11-01"
    assert page.malformed is False


def test_parse_feed_deduplicates_ids() -> None:
    """A repeated record on the same date is counted once."""
    payload = {
        "data": [
            {
                "date": "2026-11-03",
                "diaries": [
                    {"id": 1, "course": "Art"},
                    {"id": 1, "course": "Art"},
                ],
            }
        ]
    }
    page = parse_feed(payload)
    latest, count = latest_feed_summary(page)
    assert latest == date(2026, 11, 3)
    assert count == 1


def test_parse_feed_malformed_never_becomes_zero() -> None:
    """Malformed collections yield unknown, not an empty result."""
    page = parse_feed({"data": "not-a-list"})
    assert page.malformed is True
    assert latest_feed_summary(page) == (None, None)

    page = parse_feed({"data": [{"date": "2026-11-03", "diaries": [{}]}]})
    assert page.malformed is True
    assert latest_feed_summary(page) == (None, None)


def test_parse_feed_empty_is_zero_without_a_date() -> None:
    """A successful empty feed has zero records and no fabricated date."""
    page = parse_feed({"data": []})
    assert latest_feed_summary(page) == (None, 0)


def test_parse_child_day_ignores_default_and_reports_multiple() -> None:
    """Only an explicit matching date is used; courses stay transparent."""
    status = parse_child_day(CHILD_CALENDAR_PAYLOAD, TODAY)
    assert status is not None
    assert status.labels == ("Present", "Field trip")
    assert status.course_count == 2

    assert parse_child_day(CHILD_CALENDAR_PAYLOAD, date(2026, 11, 4)) is None


def test_parse_status_history() -> None:
    """Status records keep labels and real supplied timestamps."""
    history = parse_status_history(STATUS_LOG_PAYLOAD)
    assert history.malformed is False
    assert history.records[0].label == "Present"
    assert history.records[0].created_at == datetime(2026, 11, 3, 8, 0, tzinfo=UTC)
    assert history.records[0].total_time == "6h"


def test_parse_events_omits_recurring_and_nonpositive() -> None:
    """Unsupported recurrence and zero durations are omitted and counted."""
    result = parse_events(
        EVENTS_PAYLOAD,
        institution_id=INSTITUTION_ID,
        fallback_summary="Eliis event",
    )
    assert result.omitted_count == 2
    assert result.complete is False
    assert len(result.events) == 2

    timed = result.events[0]
    assert timed.summary == "Autumn fair"
    assert timed.start == datetime(2026, 11, 4, 10, 0, tzinfo=UTC)
    assert timed.end == datetime(2026, 11, 4, 12, 0, tzinfo=UTC)
    assert timed.description == "Bring a mug"
    assert timed.uid is not None

    all_day = result.events[1]
    assert all_day.all_day is True
    assert all_day.start == date(2026, 11, 5)
    assert all_day.end == date(2026, 11, 6)


def test_parse_events_all_day_missing_end_falls_back_one_day() -> None:
    """A recognized all-day record without an end gets one day only."""
    result = parse_events(
        [
            {
                "id": 1,
                "title": "No end",
                "is_recurring": False,
                "start_date": "2026-11-05",
                "start_time": None,
            }
        ],
        institution_id=INSTITUTION_ID,
        fallback_summary="Eliis event",
    )
    assert result.omitted_count == 0
    assert result.events[0].end == date(2026, 11, 6)


def test_parse_events_explicit_null_end_is_omitted() -> None:
    """Explicit null is different from a missing end."""
    result = parse_events(
        [
            {
                "id": 1,
                "title": "Null end",
                "is_recurring": False,
                "start_date": "2026-11-05",
                "start_time": None,
                "end_date": None,
            }
        ],
        institution_id=INSTITUTION_ID,
        fallback_summary="Eliis event",
    )
    assert result.omitted_count == 1
    assert result.events == ()


def test_parse_events_missing_start_time_is_unclassified() -> None:
    """A missing time is not silently treated as all-day or midnight."""
    result = parse_events(
        [{"id": 1, "title": "No time", "is_recurring": False}],
        institution_id=INSTITUTION_ID,
        fallback_summary="Eliis event",
    )
    assert result.omitted_count == 1
    assert result.events == ()
