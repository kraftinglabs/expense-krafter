"""Desktop notifications for upcoming card due dates."""

from __future__ import annotations

import logging

from ..config import settings
from .cards import due_soon
from .money import fmt

log = logging.getLogger(__name__)
_notifier = None


def due_messages() -> list[str]:
    msgs = []
    for x in due_soon(settings.due_alert_days):
        when = "overdue" if x.days_to_due < 0 else ("due today" if x.days_to_due == 0
                                                   else f"due in {x.days_to_due} days")
        msgs.append(f"{x.account_name}: {fmt(x.pending_minor)} {when} ({x.statement.due_date:%d %b})")
    return msgs


async def send_due_notifications() -> list[str]:
    global _notifier
    msgs = due_messages()
    if not msgs:
        return msgs
    try:
        from desktop_notifier import DesktopNotifier

        _notifier = _notifier or DesktopNotifier(app_name="ExpenseKrafter")
        await _notifier.send(title="Credit card bills due", message="\n".join(msgs))
    except Exception:  # notifications are best-effort
        log.exception("Desktop notification failed")
    return msgs
