"""Entry point: `python -m expense_tracker` (desktop window) or `--browser`."""

from __future__ import annotations

import asyncio
import sys

from nicegui import app, background_tasks, ui
from sqlmodel import select

from .config import override_data_dir, settings
from .db import get_session, init_db
from .db.models import IntervalUnit, Label
from .db.session import acquire_lock, backup_db, release_lock
from .services.notify import send_due_notifications
from .ui import layout

DEFAULT_LABELS = [
    ("Food & Dining", "#e45756"), ("Groceries", "#54a24b"), ("Shopping", "#f58518"),
    ("Travel", "#4c78a8"), ("Fuel", "#9d755d"), ("Bills & Utilities", "#72b7b2"),
    ("Rent", "#b279a2"), ("Health", "#ff9da6"), ("Entertainment", "#eeca3b"),
    ("Education", "#79706e"), ("Other", "#bab0ac"),
]
DUE_CHECK_INTERVAL_S = 6 * 3600


def seed_labels() -> None:
    with get_session() as s:
        if s.exec(select(Label)).first():
            return
        s.add_all(Label(name=n, color=c) for n, c in DEFAULT_LABELS)
        s.add(Label(name="Subscriptions", color="#8e6c8a", is_recurring=True, every=1,
                    unit=IntervalUnit.month))
        s.commit()


async def due_date_loop() -> None:
    while True:
        await send_due_notifications()
        await asyncio.sleep(DUE_CHECK_INTERVAL_S)


def startup() -> None:
    backup_db(settings)
    layout.startup_warning = acquire_lock(settings)
    init_db(settings)
    seed_labels()
    background_tasks.create(due_date_loop(), name="due-date-alerts")


def main() -> None:
    browser = "--browser" in sys.argv
    if "--data-dir" in sys.argv:  # dev/testing: use a throwaway data folder
        override_data_dir(sys.argv[sys.argv.index("--data-dir") + 1])  # never saved to settings
    app.on_startup(startup)
    app.on_shutdown(lambda: release_lock(settings))
    ui.run(
        title="Expense Tracker",
        native=not browser,
        window_size=None if browser else (1440, 900),
        port=8080 if browser else None,
        show=False,
        reload=False,
        favicon="💸",
    )


if __name__ in {"__main__", "__mp_main__"}:
    main()
