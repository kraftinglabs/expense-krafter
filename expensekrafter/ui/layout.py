from __future__ import annotations

from contextlib import contextmanager
from datetime import date
from pathlib import Path

from nicegui import app, ui

from ..config import save_settings, settings
from ..services import inbox, money

NAV = [
    ("Dashboard", "/", "space_dashboard"),
    ("Transactions", "/transactions", "receipt_long"),
    ("Import", "/import", "upload_file"),
    ("Credit cards", "/cards", "credit_card"),
    ("Splits & reimbursements", "/splits", "call_split"),
    ("Expense groups", "/groups", "folder_special"),
    ("Accounts", "/accounts", "account_balance"),
    ("Labels", "/labels", "sell"),
    ("Settings", "/settings", "tune"),
]

THEME_CSS = Path(__file__).with_name("theme.css")
# Pixel-art app icon (packaging/make_icon.py draws it).
LOGO_SVG = Path(__file__).with_name("logo.svg")
LOGO_PNG = Path(__file__).with_name("logo.png")
FONTS = ('<link rel="preconnect" href="https://fonts.googleapis.com">'
         '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?'
         'family=Geist:wght@300;400;500;600&display=swap">')

# Amounts are hidden per browser / window (the eye button in each page header).
HIDE_KEY = "hide_money"
money.set_hidden_check(lambda: app.storage.user.get(HIDE_KEY, False))

# Set at startup if another machine seems to have the DB open.
startup_warning: str | None = None


def _current_path() -> str:
    try:
        return ui.context.client.request.url.path
    except AttributeError:
        return "/"


def is_dark() -> bool:
    return settings.theme == "dark"


def set_theme(theme: str) -> None:
    """Save the theme and reload so charts (drawn server-side) pick it up too."""
    if settings.theme != theme:
        settings.theme = theme
        save_settings(settings)
        ui.navigate.reload()


def _theme_switch() -> None:
    with ui.element("div").classes("et-theme-switch w-full mb-2"):
        for theme, icon, tip in (("light", "light_mode", "Light"), ("dark", "dark_mode", "Dark")):
            ui.button(icon=icon, on_click=lambda t=theme: set_theme(t)) \
                .props("flat dense size=sm").classes("selected" if settings.theme == theme else "") \
                .tooltip(f"{tip} theme")


def _sidebar(active: str) -> None:
    with ui.left_drawer(value=True, fixed=True).classes("et-drawer").props("width=248 bordered=false"):
        with ui.column().classes("w-full h-full px-3 py-5 gap-1 no-wrap"):
            with ui.row().classes("items-center gap-3 px-2 pb-6"):
                ui.html(f'<div class="et-brand-mark">{LOGO_SVG.read_text(encoding="utf-8")}</div>', sanitize=False)
                with ui.column().classes("gap-0"):
                    ui.label("ExpenseKrafter").classes("et-display text-base font-semibold")
                    ui.label("Personal finance").classes("et-faint text-xs")
            ui.label("Menu").classes("et-eyebrow px-3 pb-1")
            for label, path, icon in NAV:
                with ui.link(target=path).classes("et-nav-item" + (" active" if path == active else "")):
                    ui.icon(icon)
                    ui.label(label)
            ui.space()
            from . import auth

            if auth.required():
                with ui.element("div").classes("et-nav-item mb-2 cursor-pointer").on("click", auth.sign_out):
                    ui.icon("logout")
                    ui.label("Sign out")
            _theme_switch()
            data_dir = str(settings.data_path)  # includes a --data-dir override
            in_drive = "drive" in data_dir.lower()
            with ui.element("div").classes("et-card w-full p-3"):
                with ui.row().classes("items-center gap-2 no-wrap"):
                    ui.icon("cloud_done" if in_drive else "computer").classes(
                        "text-lg " + ("et-pos" if in_drive else "et-faint"))
                    with ui.column().classes("gap-0 min-w-0"):
                        ui.label("Synced to Google Drive" if in_drive else "Stored on this PC") \
                            .classes("text-xs font-medium")
                        ui.label(data_dir).classes("et-faint text-[11px] truncate w-40").tooltip(data_dir)


# Inbox files already shown once, so a file you leave there doesn't keep pulling
# you back to the Import page.
_announced: set[str] = set()


def _check_inbox() -> None:
    """Statements sent from Explorer: bring the window forward and open Import."""
    new = {p.name for p in inbox.pending()} - _announced
    if not new:
        return
    _announced.update(new)
    if _current_path() == "/import":
        return  # the Import page shows them itself
    try:
        if app.native.main_window:
            app.native.main_window.restore()
    except Exception:  # noqa: BLE001  (web mode, or a window that can't be restored)
        pass
    ui.navigate.to("/import")


def _hide_toggle() -> None:
    hidden = money.hidden()

    def flip() -> None:
        app.storage.user[HIDE_KEY] = not hidden
        ui.navigate.reload()  # charts and tables are drawn on the server, so redraw them

    ui.button(icon="visibility_off" if hidden else "visibility", on_click=flip) \
        .props("flat round color=grey-7").classes("et-hide-toggle") \
        .tooltip("Show amounts" if hidden else "Hide amounts (shows **** instead)")


@contextmanager
def frame(title: str, subtitle: str = ""):
    ui.dark_mode(is_dark())
    ui.colors(primary="#171a18", secondary="#007d56", positive="#007d56", negative="#b42318",
              warning="#9a5b00")
    ui.add_head_html(FONTS)
    ui.add_css(THEME_CSS)

    _sidebar(_current_path())
    ui.timer(1.5, _check_inbox)
    with ui.column().classes("w-full max-w-[1500px] mx-auto px-8 py-7 gap-6"):
        with ui.row().classes("w-full items-end"):
            with ui.column().classes("gap-1"):
                ui.label(date.today().strftime("%A, %d %B %Y")).classes("et-eyebrow")
                ui.label(title).classes("et-display text-3xl font-semibold")
                if subtitle:
                    ui.label(subtitle).classes("et-muted text-sm")
            ui.space()
            _hide_toggle()
        if startup_warning:
            with ui.element("div").classes("et-card w-full p-4 border-amber-500/40"):
                with ui.row().classes("items-center gap-3"):
                    ui.icon("warning").classes("et-warn text-xl")
                    ui.label(startup_warning).classes("et-warn")
        yield
