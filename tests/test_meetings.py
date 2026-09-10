# SPDX-License-Identifier: MIT

"""
Tests for L{megingjord.meetings}.
"""

import time
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable

import pytest

from megingjord.meetings import (
    MeetingNotifier,
    get_next_meeting,
    parse_calendar_time,
)
from megingjord.status_bar import Notification


class FakeHA:
    """
    Fake Home Assistant client.
    """

    def __init__(self) -> None:
        self.states: dict[str, dict[str, Any]] = {}
        self.subscribed: list[str] = []

    def get_state(self, entity_id: str) -> dict[str, Any] | None:
        return self.states.get(entity_id)

    def subscribe(
        self, entity_id: str, callback: Callable[[Any], Awaitable[None]]
    ) -> Callable[[], None]:
        self.subscribed.append(entity_id)
        return lambda: None


class FakeStatusBar:
    """
    Fake status bar.
    """

    def __init__(self) -> None:
        self.notifications: dict[str, Notification] = {}

    def show(self, notification: Notification) -> None:
        self.notifications[notification.key] = notification

    def clear(self, key: str) -> None:
        self.notifications.pop(key, None)


def fmt(timestamp: float) -> str:
    """
    Format a timestamp like a Home Assistant calendar attribute.
    """
    return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")


def make_ha(start: float | None = None, end: float | None = None) -> FakeHA:
    """
    Create a fake HA client with an optional calendar event.
    """
    ha = FakeHA()
    if start is not None and end is not None:
        ha.states = {
            "calendar.work": {
                "state": "off",
                "attributes": {
                    "message": "Team meeting",
                    "start_time": fmt(start),
                    "end_time": fmt(end),
                },
            }
        }
    return ha


class TestParseCalendarTime:
    """
    Tests for L{megingjord.meetings.parse_calendar_time}.
    """

    def test_naive_local(self) -> None:
        """
        A naive local time is parsed as local time.
        """
        timestamp = time.time()
        parsed = parse_calendar_time(fmt(timestamp))
        assert parsed is not None
        assert abs(parsed - timestamp) < 2

    def test_invalid(self) -> None:
        """
        An invalid time returns None.
        """
        assert parse_calendar_time("not a time") is None
        assert parse_calendar_time(None) is None

    def test_aware(self) -> None:
        """
        A timezone-aware time is used as-is.
        """
        value = "2026-09-02T10:00:00+02:00"
        parsed = parse_calendar_time(value)
        expected = datetime(
            2026, 9, 2, 10, 0, 0, tzinfo=timezone(timedelta(hours=2))
        ).timestamp()
        assert parsed == expected


class TestGetNextMeeting:
    """
    Tests for L{megingjord.meetings.get_next_meeting}.
    """

    def test_no_meeting(self) -> None:
        """
        No meeting when the calendar has no event.
        """
        ha = FakeHA()
        assert get_next_meeting(ha, ["calendar.work"]) is None

    def test_past_meeting_ignored(self) -> None:
        """
        A meeting that already ended is ignored.
        """
        now = time.time()
        ha = make_ha(start=now - 3600, end=now - 1800)
        assert get_next_meeting(ha, ["calendar.work"]) is None

    def test_earliest_meeting(self) -> None:
        """
        The earliest meeting is returned.
        """
        now = time.time()
        ha = make_ha(start=now + 3600, end=now + 7200)
        ha.states["calendar.personal"] = {
            "state": "off",
            "attributes": {
                "message": "Personal",
                "start_time": fmt(now + 600),
                "end_time": fmt(now + 1200),
            },
        }
        meeting = get_next_meeting(ha, ["calendar.work", "calendar.personal"])
        assert meeting is not None
        assert meeting.title == "Personal"

    def test_missing_calendar(self) -> None:
        """
        A calendar without a state is skipped.
        """
        ha = FakeHA()
        assert get_next_meeting(ha, ["calendar.work"]) is None

    def test_title_falls_back_to_calendar(self) -> None:
        """
        A meeting without a message uses the calendar id as title.
        """
        now = time.time()
        ha = FakeHA()
        ha.states = {
            "calendar.work": {
                "state": "off",
                "attributes": {
                    "start_time": fmt(now + 600),
                    "end_time": fmt(now + 1200),
                },
            }
        }
        meeting = get_next_meeting(ha, ["calendar.work"])
        assert meeting is not None
        assert meeting.title == "calendar.work"

    def test_all_day_event_ignored(self) -> None:
        """
        An all-day event is not a meeting.
        """
        now = time.time()
        ha = FakeHA()
        ha.states = {
            "calendar.work": {
                "state": "off",
                "attributes": {
                    "message": "Birthday",
                    "all_day": True,
                    "start_time": fmt(now - 3600),
                    "end_time": fmt(now + 86400),
                },
            }
        }
        assert get_next_meeting(ha, ["calendar.work"]) is None


class TestMeetingNotifier:
    """
    Tests for L{megingjord.meetings.MeetingNotifier}.
    """

    def make_notifier(
        self, start: float | None = None, end: float | None = None
    ) -> tuple[MeetingNotifier, FakeHA, FakeStatusBar]:
        """
        Create a notifier with fakes.
        """
        ha = make_ha(start=start, end=end)
        status_bar = FakeStatusBar()
        notifier = MeetingNotifier(
            ha=ha,
            calendars=["calendar.work"],
            status_bar=status_bar,
        )
        return notifier, ha, status_bar

    def test_start_subscribes(self) -> None:
        """
        Starting subscribes to the calendars.
        """
        notifier, ha, _status_bar = self.make_notifier()
        notifier.start()
        assert ha.subscribed == ["calendar.work"]

    def test_no_meeting_clears(self) -> None:
        """
        Without a meeting, the notification is cleared.
        """
        notifier, _ha, status_bar = self.make_notifier()
        notifier._update()
        assert status_bar.notifications == {}

    def test_far_meeting_clears(self) -> None:
        """
        A meeting outside the preview window clears the notification.
        """
        now = time.time()
        notifier, _ha, status_bar = self.make_notifier(
            start=now + 7200, end=now + 10800
        )
        notifier._update()
        assert status_bar.notifications == {}

    def test_preview_shows_countdown_to_start(self) -> None:
        """
        A meeting in the preview window counts down to the start.
        """
        now = time.time()
        notifier, _ha, status_bar = self.make_notifier(
            start=now + 300, end=now + 3900
        )
        notifier._update()
        notification = status_bar.notifications["meeting"]
        assert notification.title == "Team meeting"
        assert notification.icon == "calendar-clock-outline"
        assert notification.countdown_to is not None
        assert abs(notification.countdown_to - (now + 300)) < 2

    def test_meeting_shows_countdown_to_end(self) -> None:
        """
        A meeting in progress counts down to the end.
        """
        now = time.time()
        notifier, _ha, status_bar = self.make_notifier(
            start=now - 60, end=now + 3540
        )
        notifier._update()
        notification = status_bar.notifications["meeting"]
        assert notification.title == "Team meeting"
        assert notification.icon == "video-outline"
        assert notification.countdown_to is not None
        assert abs(notification.countdown_to - (now + 3540)) < 2

    @pytest.mark.asyncio
    async def test_calendar_change_updates(self) -> None:
        """
        A calendar state change updates the notification.
        """
        now = time.time()
        notifier, ha, status_bar = self.make_notifier()
        notifier.start()
        ha.states = {
            "calendar.work": {
                "state": "off",
                "attributes": {
                    "message": "Team meeting",
                    "start_time": fmt(now + 300),
                    "end_time": fmt(now + 3900),
                },
            }
        }
        await notifier._on_calendar(None)
        assert "meeting" in status_bar.notifications
