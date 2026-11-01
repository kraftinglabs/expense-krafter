"""Entry point.

    expensekrafter                      desktop window
    expensekrafter --web                web app: serves on http://localhost:8080 and opens your browser
    expensekrafter --web --host 0.0.0.0 reachable from other machines (needs EXPENSEKRAFTER_PASSWORD)
    expensekrafter --import FILE...     open these statements on the Import page (Explorer's "Send to")

Run with --help for all options.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import os
import sys

from .config import CONFIG_DIR, adopt_legacy_install, override_data_dir, reload_settings, settings

# NiceGUI keeps sign-in sessions and UI choices in ./.nicegui by default, which
# isn't writable when the app is installed under Program Files. Must be set
# before nicegui is imported.
os.environ.setdefault("NICEGUI_STORAGE_PATH", str(CONFIG_DIR / "nicegui"))

from nicegui import app, background_tasks, ui
from sqlmodel import select

from .db import get_session, init_db
from .db.models import IntervalUnit, Label
from .db.session import acquire_lock, adopt_legacy_db, backup_db, release_lock
from .services.notify import send_due_notifications
from .services import app_lock, inbox
from .ui import auth, layout

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


LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


def startup(desktop: bool) -> None:
    adopt_legacy_db(settings)
    backup_db(settings)
    layout.startup_warning = acquire_lock(settings)
    init_db(settings)
    seed_labels()
    if desktop:  # due-date pop-ups; the web app shows pending bills on the dashboard instead
        background_tasks.create(due_date_loop(), name="due-date-alerts")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="expensekrafter", description="ExpenseKrafter expense tracker")
    parser.add_argument("--web", action="store_true",
                        help="run as a web app in your browser instead of a desktop window")
    parser.add_argument("--host", default="127.0.0.1",
                        help="address to serve on (default: this computer only). Anything else needs "
                             f"an app password (Settings) or one in the {auth.PASSWORD_ENV} environment variable")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--no-open", action="store_true", help="don't open a browser tab (web mode)")
    parser.add_argument("--no-password", action="store_true",
                        help="serve beyond localhost without a password. Only for a container whose "
                             "port is published to localhost alone")
    parser.add_argument("--reset-password", action="store_true",
                        help="remove the app password (if you've forgotten it), then exit")
    parser.add_argument("--browser", action="store_true", help=argparse.SUPPRESS)  # dev: --web --no-open
    parser.add_argument("--data-dir", help="use this data folder for this run only (dev/testing)")
    parser.add_argument("--import", dest="import_files", nargs="+", metavar="FILE",
                        help="statements to import; handed to the open app if it's already running")
    args = parser.parse_args(argv)
    if args.browser:
        args.web = args.no_open = True
    return args


def main() -> None:
    args = parse_args(sys.argv[1:])
    if args.data_dir:  # never saved to settings
        override_data_dir(args.data_dir)
    elif adopt_legacy_install():  # first run after the rename from ExpenseTracker
        reload_settings()
    if args.import_files:
        added = inbox.add(args.import_files)
        if inbox.app_running(settings):  # the open window picks them up from the inbox
            print(f"Sent {len(added)} file{'s' if len(added) != 1 else ''} to the open ExpenseKrafter.")
            return
    if args.reset_password:
        backup_db(settings)
        init_db(settings)
        app_lock.reset()
        print("App password removed. ExpenseKrafter will open without one; set a new one in Settings.")
        return
    if password := os.environ.get(auth.PASSWORD_ENV):
        auth.use_env_password(password)

    desktop = not args.web
    if desktop and importlib.util.find_spec("webview") is None:
        print("Desktop window support isn't installed (pip install 'expensekrafter[desktop]'); "
              "running as a web app instead.")
        desktop = False

    run_args: dict = {}
    if desktop:
        run_args.update(native=True, window_size=(1440, 900), show=False)
        if sys.platform.startswith("linux"):
            app.native.start_args["gui"] = "qt"
            # Linux takes the window icon from the app (Windows uses the exe's icon).
            app.native.start_args["icon"] = str(layout.LOGO_PNG)
    else:
        has_password = password or app_lock.is_set_in(settings.db_path)
        if args.host not in LOCAL_HOSTS and not has_password and not args.no_password:
            sys.exit(f"Refusing to serve your financial data on {args.host} without a password.\n"
                     f"Set an app password in Settings, or {auth.PASSWORD_ENV}, or use --host 127.0.0.1 "
                     "to keep it on this computer.")
        run_args.update(host=args.host, port=args.port, show=not args.no_open)

    run_args["storage_secret"] = auth.storage_secret()  # signs the sign-in session cookie
    auth.install()
    app.on_startup(lambda: startup(desktop))
    app.on_shutdown(lambda: release_lock(settings))
    ui.run(title="ExpenseKrafter", reload=False, favicon=layout.LOGO_SVG, **run_args)


if __name__ in {"__main__", "__mp_main__"}:
    main()
