"""Windows Share sheet: ExpenseKrafter as a share target.

The app is registered with a package identity (packaging/windows/AppxManifest.xml,
a sparse package pointing at the install folder) that declares it accepts
shared statement files. Windows then starts ExpenseKrafter.exe with share
activation; the shared files go into the inbox like "Send to" files.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from . import inbox


def take_shared_files() -> list[Path] | None:
    """If Windows started us to receive a share, copy the files into the inbox,
    tell Windows the share is done and return the inbox copies. None for any
    other launch (no package identity, or not a share)."""
    if sys.platform != "win32":
        return None
    try:
        from winrt.windows.applicationmodel import AppInstance
        from winrt.windows.applicationmodel.activation import ActivationKind, ShareTargetActivatedEventArgs
        from winrt.windows.storage import IStorageItem  # noqa: F401  (projection for the shared items)
    except ImportError:
        return None
    try:
        args = AppInstance.get_activated_event_args()
    except OSError:  # no package identity: an ordinary launch
        return None
    if args is None or args.kind != ActivationKind.SHARE_TARGET:
        return None
    operation = ShareTargetActivatedEventArgs._from(args).share_operation
    try:
        items = asyncio.run(_storage_items(operation.data))
        added = inbox.add([item.path for item in items if item.path])
    finally:
        operation.report_completed()  # closes the Share sheet's progress
    return added


async def _storage_items(data) -> list:
    return list(await data.get_storage_items_async())
