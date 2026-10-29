"""Sign-in gate: the app password, asked for each time the app starts.

The password is either the one set in Settings (stored hashed in the database,
see services/app_lock.py) or, for a server, the EXPENSEKRAFTER_PASSWORD
environment variable (never a command-line argument, which other users on the
machine could see). Serving beyond localhost requires one of them.

A sign-in is tied to the current run of the app, so closing and reopening it
(or restarting the server) asks again; "Sign out" in the sidebar locks it
straight away.
"""

from __future__ import annotations

import asyncio
import hmac
import secrets

from fastapi import Request
from fastapi.responses import RedirectResponse
from nicegui import app, ui
from starlette.middleware.base import BaseHTTPMiddleware

from ..config import CONFIG_DIR
from ..services import app_lock

PASSWORD_ENV = "EXPENSEKRAFTER_PASSWORD"
LOGIN = "/login"
_RUN = app_lock.session_token()  # changes on every start: old sign-ins stop counting
_env_password: str | None = None
_lock_set: bool | None = None  # cached app_lock.is_set(); refresh() after changes


def use_env_password(password: str) -> None:
    global _env_password
    _env_password = password


def env_password_active() -> bool:
    return bool(_env_password)


def refresh() -> None:
    """Call after setting or removing the app password."""
    global _lock_set
    _lock_set = None


def required() -> bool:
    global _lock_set
    if _env_password:
        return True
    if _lock_set is None:
        _lock_set = app_lock.is_set()
    return _lock_set


def signed_in() -> bool:
    return app.storage.user.get("signed_in") == _RUN


def mark_signed_in() -> None:
    app.storage.user["signed_in"] = _RUN


def check(password: str) -> bool:
    if _env_password and hmac.compare_digest(password.encode(), _env_password.encode()):
        return True
    return app_lock.is_set() and app_lock.verify(password)


def storage_secret() -> str:
    """Signs the session cookie. Kept in the settings folder; deleting
    web_secret signs every browser out."""
    path = CONFIG_DIR / "web_secret"
    if not path.exists():
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(secrets.token_urlsafe(32), encoding="utf-8")
    return path.read_text(encoding="utf-8").strip()


class _RequireLogin(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        # NiceGUI's own assets and websocket must load for the login page itself.
        if path != LOGIN and not path.startswith("/_nicegui") and required() and not signed_in():
            return RedirectResponse(f"{LOGIN}?next={path}")
        return await call_next(request)


def install() -> None:
    app.add_middleware(_RequireLogin)


def safe_next(target: str) -> str:
    """Only same-site paths: "//evil.example" (or "/" plus a backslash) would
    send you to another site after signing in."""
    if target.startswith("/") and not target.startswith(("//", "/\\")):
        return target
    return "/"


def sign_out() -> None:
    app.storage.user.pop("signed_in", None)
    ui.navigate.to(LOGIN)


@ui.page(LOGIN)
def login_page(next: str = "/") -> None:  # noqa: A002 (query parameter name)
    from .layout import FONTS, LOGO_SVG, THEME_CSS, is_dark

    if not required() or signed_in():
        ui.navigate.to(safe_next(next))
        return
    ui.dark_mode(is_dark())
    ui.add_head_html(FONTS)
    ui.add_css(THEME_CSS)

    async def attempt() -> None:
        if await asyncio.to_thread(check, password.value or ""):
            mark_signed_in()
            ui.navigate.to(safe_next(next))
        else:
            await asyncio.sleep(1)  # slows down guessing
            password.value = ""
            error.set_visibility(True)

    with ui.column().classes("absolute-center items-center gap-6 w-80"):
        ui.html(f'<div style="width:56px;height:56px">{LOGO_SVG.read_text(encoding="utf-8")}</div>',
                sanitize=False)
        ui.label("ExpenseKrafter").classes("et-display text-2xl font-semibold")
        with ui.card().classes("w-full gap-3"):
            password = ui.input("Password", password=True, password_toggle_button=True) \
                .classes("w-full").props("outlined dense autofocus")
            password.on("keydown.enter", attempt)
            error = ui.label("Wrong password").classes("et-neg text-sm")
            error.set_visibility(False)
            ui.button("Sign in", on_click=attempt).classes("w-full")
            ui.label("Forgot it? Run expensekrafter --reset-password").classes("text-xs et-faint")
