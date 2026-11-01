"""Build the desktop app with PyInstaller, for the OS you run it on (PyInstaller
can't cross-compile: build the Windows app on Windows, the Linux app on Linux).

Windows:
    .venv/Scripts/python -m pip install -e ".[dev,build]"
    .venv/Scripts/python build.py              # dist/ExpenseKrafter/ExpenseKrafter.exe
    .venv/Scripts/python build.py --onefile    # dist/ExpenseKrafter.exe (slower to start)

Linux:
    .venv/bin/python -m pip install -e ".[dev,build]"
    .venv/bin/python build.py                  # dist/ExpenseKrafter/ExpenseKrafter
                                               # + dist/ExpenseKrafter-linux-<arch>.tar.gz
    Unpack the archive anywhere and run ./install.sh to add it to your app menu.

    --skip-tests   build even if the tests fail

Windows: also builds the Share sheet package (dist/ExpenseKrafter-share.msix)
once its signing certificate exists; see packaging/windows/share_target.py.

Runs the tests first, then packages the app with everything it needs (NiceGUI's
web assets, the theme, icons and description rules). Your data is not
included: the built app uses the same data folder as `python -m expensekrafter`.
"""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
APP_NAME = "ExpenseKrafter"
PKG = ROOT / "expensekrafter"
PACKAGING = ROOT / "packaging"
SEP = os.pathsep  # PyInstaller's src/dest separator: ';' on Windows, ':' elsewhere
WINDOWS = sys.platform == "win32"
LINUX = sys.platform.startswith("linux")

# Non-Python files the app reads at runtime, as (source, folder inside the bundle).
DATA = [
    (PKG / "ui" / "theme.css", "expensekrafter/ui"),
    (PKG / "ui" / "logo.svg", "expensekrafter/ui"),
    (PKG / "ui" / "logo.png", "expensekrafter/ui"),
    (PKG / "ui" / "bank_icons", "expensekrafter/ui/bank_icons"),
    (PKG / "services" / "narration_rules.toml", "expensekrafter/services"),
]


def run_tests() -> None:
    print("Running tests...")
    if subprocess.call([sys.executable, "-m", "pytest", "-q"], cwd=ROOT):
        sys.exit("Tests failed; not building. Use --skip-tests to build anyway.")


def _platform_args() -> list[str]:
    """What PyInstaller can't find on its own (loaded through entry points or
    picked at runtime), per OS."""
    if WINDOWS:
        # The manifest ties the exe to the Share sheet package (packaging/windows);
        # the winrt modules read a share when Windows starts the app with one.
        winrt = ["winrt.windows.applicationmodel", "winrt.windows.applicationmodel.activation",
                 "winrt.windows.applicationmodel.datatransfer",
                 "winrt.windows.applicationmodel.datatransfer.sharetarget", "winrt.windows.storage",
                 "winrt.windows.foundation", "winrt.windows.foundation.collections"]
        return ["--hidden-import", "keyring.backends.Windows",
                *[a for m in winrt for a in ("--hidden-import", m)],
                "--manifest", str(PACKAGING / "windows" / "ExpenseKrafter.exe.manifest"),
                "--icon", str(PACKAGING / "icon.ico")]
    if LINUX:
        # Saved passwords go to the Secret Service (GNOME Keyring / KWallet);
        # the window is a Qt WebEngine view.
        return ["--hidden-import", "keyring.backends.SecretService",
                "--hidden-import", "webview.platforms.qt",
                "--collect-submodules", "qtpy"]
    return []


def build(onefile: bool) -> Path:
    import nicegui
    import PyInstaller.__main__

    args = [
        str(PACKAGING / "launcher.py"),
        "--name", APP_NAME,
        "--windowed",  # no console window
        "--onefile" if onefile else "--onedir",
        "--noconfirm", "--clean",
        "--distpath", str(ROOT / "dist"),
        "--workpath", str(ROOT / "build"),
        "--specpath", str(ROOT / "build"),
        "--paths", str(ROOT),
        "--add-data", f"{Path(nicegui.__file__).parent}{SEP}nicegui",
        "--collect-submodules", "expensekrafter",
        "--collect-all", "desktop_notifier",
        "--copy-metadata", "keyring",
        *_platform_args(),
    ]
    for src, dest in DATA:
        args += ["--add-data", f"{src}{SEP}{dest}"]
    PyInstaller.__main__.run(args)
    name = f"{APP_NAME}.exe" if WINDOWS else APP_NAME
    return ROOT / "dist" / (name if onefile else APP_NAME)


def linux_package(app: Path) -> Path:
    """Add the icon, a menu entry and install.sh next to the app, and pack it
    all into dist/ExpenseKrafter-linux-<arch>.tar.gz."""
    folder = ROOT / "dist" / APP_NAME
    if not app.is_dir():  # --onefile: give the single binary a folder of its own
        binary = app.rename(app.with_suffix(".bin"))  # the folder takes its name
        folder.mkdir()
        binary.rename(folder / APP_NAME)
    shutil.copy2(PACKAGING / "icon.png", folder / "expensekrafter.png")
    shutil.copy2(PACKAGING / "linux" / "expensekrafter.desktop", folder)
    shutil.copy2(PACKAGING / "linux" / "install.sh", folder)
    (folder / "install.sh").chmod(0o755)
    archive = ROOT / "dist" / f"{APP_NAME}-linux-{platform.machine()}.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(folder, arcname=APP_NAME)
    return archive


def share_package(app: Path) -> None:
    """Build and sign the Share sheet package, if the signing certificate has
    been made (packaging/windows/share_target.py cert), and put its logos in
    the app folder. Register it after installing: share_target.py register
    <install folder>."""
    sys.path.insert(0, str(PACKAGING / "windows"))
    import share_target

    try:
        if share_target.thumbprint() and app.is_dir():
            print(f"Share sheet package: {share_target.pack(app)}")
    except RuntimeError as exc:
        print(f"Share sheet package skipped: {exc}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--onefile", action="store_true", help="a single executable instead of a folder")
    parser.add_argument("--skip-tests", action="store_true")
    opts = parser.parse_args()

    if not opts.skip_tests:
        run_tests()
    shutil.rmtree(ROOT / "build", ignore_errors=True)
    out = build(opts.onefile)
    print(f"\nBuilt: {out}")
    if WINDOWS:
        share_package(out)
    if LINUX:
        print(f"Packed: {linux_package(out)}")


if __name__ == "__main__":
    main()
