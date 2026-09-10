# SPDX-License-Identifier: MIT

"""
Meeting data support.
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import TYPE_CHECKING, Any

from attrs import define, field

from .status_bar import Notification

if TYPE_CHECKING:
    from .ha import HAWebSocketClient
    from .status_bar import StatusBar


@define
class Meeting:
    """
    A meeting with a title, start and end time.
    """

    title: str
    start: float
    end: float


def parse_calendar_time(value: Any) -> float | None:
    """
    Parse a calendar event time to an epoch timestamp.
    """
    if not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.replace(" ", "T"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.astimezone()
    return dt.timestamp()


def get_next_meeting(
    ha: HAWebSocketClient, calendars: list[str]
) -> Meeting | None:
    """
    Get the next meeting from the calendars.
    """
    now = time.time()
    candidates: list[Meeting] = []
    for calendar in calendars:
        state = ha.get_state(calendar)
        if state is None:
            continue
        attributes = state.get("attributes", {})
        start = parse_calendar_time(attributes.get("start_time"))
        end = parse_calendar_time(attributes.get("end_time"))
        if start is None or end is None or end <= now:
            continue
        title = attributes.get("message") or calendar
        candidates.append(Meeting(title=title, start=start, end=end))
    if not candidates:
        return None
    return min(candidates, key=lambda meeting: meeting.start)


@define
class MeetingNotifier:
    """
    Push meeting notifications to the status bar from calendars.

    Subscribes to the configured calendars and shows a notification
    with a countdown to the meeting start within the preview window,
    and a countdown to the meeting end while it is in progress.
    """

    ha: HAWebSocketClient
    calendars: list[str]
    status_bar: StatusBar
    preview_minutes: int = 10

    key: str = field(init=False, default="meeting")

    def start(self) -> None:
        """
        Subscribe to the calendars.
        """
        for calendar in self.calendars:
            self.ha.subscribe(calendar, self._on_calendar)

    async def _on_calendar(self, _state: dict[str, Any] | None) -> None:
        """
        A calendar entity changed.
        """
        self._update()

    def _update(self) -> None:
        """
        Update the meeting notification.
        """
        meeting = get_next_meeting(self.ha, self.calendars)
        now = time.time()
        if meeting is None or meeting.start - now > self.preview_minutes * 60:
            self.status_bar.clear(self.key)
        elif now < meeting.start:
            self.status_bar.show(
                Notification(
                    key=self.key,
                    title=meeting.title,
                    countdown_to=meeting.start,
                    icon="calendar-clock-outline",
                )
            )
        else:
            self.status_bar.show(
                Notification(
                    key=self.key,
                    title=meeting.title,
                    countdown_to=meeting.end,
                    icon="video-outline",
                )
            )
