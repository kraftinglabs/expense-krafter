"""Build the desktop app with PyInstaller.

    .venv/Scripts/python -m pip install -e ".[build]"
    .venv/Scripts/python build.py              # dist/ExpenseKrafter/ExpenseKrafter.exe
    .venv/Scripts/python build.py --onefile    # dist/ExpenseKrafter.exe (slower to start)
    .venv/Scripts/python build.py --skip-tests

Runs the tests first, then packages the app with everything it needs (NiceGUI's
web assets, the theme, bank icons and description rules). Your data is not
included: the built app uses the same data folder as `python -m expensekrafter`.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
APP_NAME = "ExpenseKrafter"
PKG = ROOT / "expensekrafter"
SEP = os.pathsep  # PyInstaller's src/dest separator: ';' on Windows, ':' elsewhere

# Non-Python files the app reads at runtime, as (source, folder inside the bundle).
DATA = [
    (PKG / "ui" / "theme.css", "expensekrafter/ui"),
    (PKG / "ui" / "logo.svg", "expensekrafter/ui"),
    (PKG / "ui" / "bank_icons", "expensekrafter/ui/bank_icons"),
    (PKG / "services" / "narration_rules.toml", "expensekrafter/services"),
]


def run_tests() -> None:
    print("Running tests...")
    if subprocess.call([sys.executable, "-m", "pytest", "-q"], cwd=ROOT):
        sys.exit("Tests failed; not building. Use --skip-tests to build anyway.")


def build(onefile: bool) -> Path:
    import nicegui
    import PyInstaller.__main__

    args = [
        str(ROOT / "packaging" / "launcher.py"),
        "--name", APP_NAME,
        "--windowed",  # no console window
        "--onefile" if onefile else "--onedir",
        "--noconfirm", "--clean",
        "--distpath", str(ROOT / "dist"),
        "--workpath", str(ROOT / "build"),
        "--specpath", str(ROOT / "build"),
        "--paths", str(ROOT),
        "--add-data", f"{Path(nicegui.__file__).parent}{SEP}nicegui",
        # Picked up through entry points / dynamic imports, which PyInstaller can't see.
        "--collect-submodules", "expensekrafter",
        "--collect-all", "desktop_notifier",
        "--copy-metadata", "keyring",
        "--hidden-import", "keyring.backends.Windows",
    ]
    for src, dest in DATA:
        args += ["--add-data", f"{src}{SEP}{dest}"]
    if (icon := ROOT / "packaging" / "icon.ico").exists():
        args += ["--icon", str(icon)]
    PyInstaller.__main__.run(args)
    return ROOT / "dist" / (f"{APP_NAME}.exe" if onefile else APP_NAME)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--onefile", action="store_true", help="a single .exe instead of a folder")
    parser.add_argument("--skip-tests", action="store_true")
    opts = parser.parse_args()

    if not opts.skip_tests:
        run_tests()
    shutil.rmtree(ROOT / "build", ignore_errors=True)
    out = build(opts.onefile)
    print(f"\nBuilt: {out}")


if __name__ == "__main__":
    main()
