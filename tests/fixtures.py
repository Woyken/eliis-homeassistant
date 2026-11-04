"""Sanitized fixtures with fictional values."""

from __future__ import annotations

from datetime import date
from typing import Any

INSTITUTION_ID = "1001"
CHILD_ID = "2002"
SECOND_CHILD_ID = "2003"

INIT_PAYLOAD: dict[str, Any] = {
    "kindergartens": [
        {"id": 1001, "name": "Test Kindergarten"},
    ],
    "children": [
        {"id": 2002, "name": "Test Child"},
        {"id": 2003, "name": "Second Child", "kindergarten_id": 1001},
    ],
    "user": {"id": 9009},
}

LOGIN_PAYLOAD: dict[str, Any] = {"ok": True}

TWO_FACTOR_PAYLOAD: dict[str, Any] = {"login_identifier": "challenge-token"}

MENU_PAYLOAD: dict[str, Any] = {
    "id": 1,
    "info": "Allergen information available at the front desk.",
    "days": [
        {
            "id": 11,
            "date": "2026-11-02",
            "breakfast": "Porridge & berries",
            "lunch": "<b>Soup</b> and bread",
            "supper": "Yogurt",
            "dinner": None,
        },
        {
            "id": 12,
            "date": "2026-11-03",
            "breakfast": "Sandwich",
            "lunch": "Pasta",
            "supper": "",
            "dinner": None,
        },
    ],
    "uploaded_files": [],
}

FEED_PAYLOAD: dict[str, Any] = {
    "data": [
        {
            "date": "2026-11-03",
            "diaries": [
                {
                    "id": 501,
                    "course": "Main course",
                    "status": {"name": "Present"},
                    "total_time": "6h",
                },
                {
                    "id": 502,
                    "course": "Art",
                    "status": {"name": "Present"},
                },
            ],
            "evaluations": [],
        },
        {
            "date": "2026-11-02",
            "diaries": [],
            "evaluations": [{"id": 1}],
        },
    ],
    "next_date": "2026-11-01",
}

CHILD_CALENDAR_PAYLOAD: dict[str, Any] = {
    "default_status": {"id": 1, "name": "Default must be ignored"},
    "days": [
        {
            "date": "2026-11-02",
            "course_id": 1,
            "entry": {
                "id": 1,
                "diary_status": {"id": 10, "name": "Present"},
            },
        },
        {
            "date": "2026-11-03",
            "course_id": 1,
            "entry": {
                "id": 2,
                "diary_status": {"id": 11, "name": "Present"},
            },
        },
        {
            "date": "2026-11-03",
            "course_id": 2,
            "entry": {
                "id": 3,
                "diary_status": {"id": 12, "name": "Field trip"},
            },
        },
    ],
}

STATUS_LOG_PAYLOAD: list[dict[str, Any]] = [
    {
        "id": 1,
        "diary_status": {"id": 10, "name": "Present"},
        "created_at": "2026-11-03T08:00:00Z",
        "total_time": "6h",
    },
    {
        "id": 2,
        "diary_status": {"id": 11, "name": "Left"},
        "created_at": "2026-11-03T14:00:00Z",
    },
]

EVENTS_PAYLOAD: list[dict[str, Any]] = [
    {
        "id": 77,
        "unique_id": "evt-77",
        "title": "Autumn fair",
        "description": "<p>Bring <b>a mug</b></p>",
        "is_recurring": False,
        "repeat_start": None,
        "rrule": None,
        "event_type": 1,
        "start_date": "2026-11-04",
        "start_time": "2026-11-04T10:00:00Z",
        "end_date": "2026-11-04",
        "end_time": "2026-11-04T12:00:00Z",
    },
    {
        "id": 78,
        "unique_id": "evt-78",
        "title": "All day",
        "is_recurring": False,
        "start_date": "2026-11-05",
        "start_time": None,
        "end_date": "2026-11-05",
    },
    {
        "id": 79,
        "unique_id": "evt-79",
        "title": "Recurring event",
        "is_recurring": True,
        "start_date": "2026-11-06",
        "start_time": "2026-11-06T10:00:00Z",
        "end_date": "2026-11-06",
        "end_time": "2026-11-06T11:00:00Z",
    },
    {
        "id": 80,
        "unique_id": "evt-80",
        "title": "Missing end",
        "is_recurring": False,
        "start_date": "2026-11-07",
        "start_time": "2026-11-07T10:00:00Z",
        "end_date": "2026-11-07",
        "end_time": "2026-11-07T10:00:00Z",
    },
]

TODAY = date(2026, 11, 3)
