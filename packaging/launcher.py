"""Entry point for the packaged desktop app (PyInstaller runs this file)."""

import multiprocessing
import sys

if __name__ in {"__main__", "__mp_main__"}:
    # The native window runs in a child process; a frozen exe must hand that
    # process over to multiprocessing instead of starting the app again.
    multiprocessing.freeze_support()

    # A windowed exe has no console, so stdout/stderr are None and logging
    # would crash. Send them to a log file next to the settings instead.
    if sys.stdout is None or sys.stderr is None:
        from expensekrafter.config import CONFIG_DIR

        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        log = open(CONFIG_DIR / "app.log", "a", encoding="utf-8", buffering=1)  # noqa: SIM115
        sys.stdout = sys.stdout or log
        sys.stderr = sys.stderr or log

    from expensekrafter.__main__ import main

    main()
