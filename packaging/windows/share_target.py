"""Put ExpenseKrafter in the Windows Share sheet (Windows 10 2004+ / 11).

The Share sheet only lists apps with a package identity. This builds a sparse
package (AppxManifest.xml: no files, just an identity that points at the
install folder and accepts shared statement files), signs it and registers it.

    python packaging/windows/share_target.py cert       # once: make the signing certificate
    python packaging/windows/share_target.py pack dist/ExpenseKrafter   # build + sign dist/ExpenseKrafter-share.msix,
                                                                         # logos into the app folder
    python packaging/windows/share_target.py register "C:\\Program Files\\ExpenseKrafter"
    python packaging/windows/share_target.py unregister

The certificate is self-made, so Windows only accepts the package once you
trust it on this computer (`cert` prints the one admin command for that). Its
private key stays in your Windows certificate store (Current User > Personal),
never in the repo. build.py runs `pack` when the certificate exists.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SUBJECT = "CN=ExpenseKrafter Local Signing"  # = Publisher in both manifests
PACKAGE = "Kraftinglabs.ExpenseKrafter"
LAYOUT = ROOT / "build" / "share-target"
MSIX = ROOT / "dist" / "ExpenseKrafter-share.msix"
CER = ROOT / "dist" / "ExpenseKrafter-signing.cer"
SDK = Path(r"C:\Program Files (x86)\Windows Kits\10\bin")
# Logo files at the sizes Windows asks for. The Share sheet wants the small
# icon by target size ("targetsize-32_altform-unplated": no tile background);
# resources.pri (made by makepri) maps the manifest's plain names to these.
LOGOS = {"StoreLogo": 50, "Square150x150Logo": 150, "Square44x44Logo": 44}
TARGET_SIZES = (16, 20, 24, 30, 32, 36, 40, 48, 60, 64, 72, 80, 96, 256)


def _ps(script: str) -> str:
    out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                         capture_output=True, text=True)
    if out.returncode:
        raise RuntimeError(out.stderr.strip() or out.stdout.strip())
    return out.stdout.strip()


def _sdk_tool(name: str) -> Path:
    found = sorted(SDK.glob(f"10.*/x64/{name}"))
    if not found:
        raise RuntimeError(f"{name} not found: install the Windows SDK (it's in Visual Studio's installer)")
    return found[-1]


def thumbprint() -> str | None:
    """The signing certificate in your store, if it's been made."""
    return _ps(f"Get-ChildItem Cert:\\CurrentUser\\My | Where-Object Subject -eq '{SUBJECT}' | "
               "Sort-Object NotAfter -Descending | Select-Object -First 1 -ExpandProperty Thumbprint") or None


def trusted() -> bool:
    tp = thumbprint()
    return bool(tp) and bool(_ps(f"Get-ChildItem Cert:\\LocalMachine\\TrustedPeople | "
                                 f"Where-Object Thumbprint -eq '{tp}'"))


def make_cert() -> None:
    if not thumbprint():
        _ps(f"New-SelfSignedCertificate -Type CodeSigningCert -Subject '{SUBJECT}' "
            "-CertStoreLocation Cert:\\CurrentUser\\My -KeyUsage DigitalSignature -NotAfter (Get-Date).AddYears(10) "
            "-TextExtension @('2.5.29.19={text}false') | Out-Null")
    CER.parent.mkdir(parents=True, exist_ok=True)
    _ps(f"Export-Certificate -Cert Cert:\\CurrentUser\\My\\{thumbprint()} -FilePath '{CER}' | Out-Null")
    print(f"Signing certificate: {SUBJECT} ({thumbprint()})\nPublic part: {CER}")
    if trusted():
        print("Already trusted on this computer.")
    else:
        print("\nTrust it once, in an admin PowerShell:\n"
              f"    Import-Certificate -FilePath '{CER}' -CertStoreLocation Cert:\\LocalMachine\\TrustedPeople")


def pack(app_dir: Path | None = None) -> Path:
    """Build and sign the package. With app_dir (the built app folder), also
    copy the logos and resources.pri there: a package with an external location
    takes its logos from that folder, not from the package."""
    from PIL import Image

    tp = thumbprint()
    if not tp:
        raise RuntimeError("No signing certificate yet: run `share_target.py cert` first")
    shutil.rmtree(LAYOUT, ignore_errors=True)
    assets = LAYOUT / "Assets"
    assets.mkdir(parents=True)
    # A new version each build, so Windows refreshes its cached icons on re-register.
    now = datetime.now()
    version = f"1.{now:%y%m}.{now.day * 100 + now.hour}.{now.minute}"
    manifest = (HERE / "AppxManifest.xml").read_text(encoding="utf-8")
    (LAYOUT / "AppxManifest.xml").write_text(manifest.replace('Version="1.0.0.0"', f'Version="{version}"'),
                                             encoding="utf-8")
    icon = Image.open(ROOT / "packaging" / "icon.png").convert("RGBA")

    def save(name: str, size: int) -> None:
        icon.resize((size, size), Image.LANCZOS).save(assets / name)

    for name, size in LOGOS.items():
        save(f"{name}.png", size)
        for scale in (100, 125, 150, 200, 400):
            save(f"{name}.scale-{scale}.png", size * scale // 100)
    for size in TARGET_SIZES:
        for form in ("", "_altform-unplated", "_altform-lightunplated"):
            save(f"Square44x44Logo.targetsize-{size}{form}.png", size)

    config = LAYOUT.parent / "share-target-priconfig.xml"
    makepri = str(_sdk_tool("makepri.exe"))
    MSIX.parent.mkdir(parents=True, exist_ok=True)
    MSIX.unlink(missing_ok=True)
    _run([makepri, "createconfig", "/cf", str(config), "/dq", "en-US", "/o"])
    # One resources.pri with every scale: split resource packs are for bundles only.
    config.write_text(re.sub(r"\s*<packaging>.*?</packaging>", "", config.read_text(encoding="utf-8"), flags=re.S),
                      encoding="utf-8")
    for cmd in ([makepri, "new", "/pr", str(LAYOUT), "/cf", str(config), "/mn", str(LAYOUT / "AppxManifest.xml"),
                 "/of", str(LAYOUT / "resources.pri"), "/o"],
                [str(_sdk_tool("makeappx.exe")), "pack", "/d", str(LAYOUT), "/p", str(MSIX), "/nv", "/o"],
                [str(_sdk_tool("signtool.exe")), "sign", "/fd", "SHA256", "/sha1", tp, "/s", "My", str(MSIX)]):
        _run(cmd)
    if app_dir is not None:
        shutil.rmtree(app_dir / "Assets", ignore_errors=True)
        shutil.copytree(assets, app_dir / "Assets")
        shutil.copy2(LAYOUT / "resources.pri", app_dir)
    return MSIX


def _run(cmd: list[str]) -> None:
    out = subprocess.run(cmd, capture_output=True, text=True)
    if out.returncode:
        raise RuntimeError(f"{Path(cmd[0]).name} failed:\n{out.stdout}{out.stderr}")


def unregister() -> None:
    _ps(f"Get-AppxPackage -Name '{PACKAGE}' | Remove-AppxPackage")


def register(install_dir: Path) -> None:
    """(Re-)register the package for the app in install_dir. Run after every install."""
    if not (install_dir / "ExpenseKrafter.exe").exists():
        raise RuntimeError(f"No ExpenseKrafter.exe in {install_dir}")
    if not trusted():
        raise RuntimeError("The signing certificate isn't trusted on this computer yet; run `cert` for the command")
    unregister()
    _ps(f"Add-AppxPackage -Path '{MSIX}' -ExternalLocation '{install_dir}'")
    print(f"Registered: ExpenseKrafter is in the Share sheet for {', '.join(('.pdf', '.xlsx', '.xls', '.csv'))}")


def main() -> None:
    if sys.platform != "win32":
        sys.exit("Windows only")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("cert")
    pk = sub.add_parser("pack")
    pk.add_argument("app_dir", type=Path, nargs="?", help="built app folder to put the logos in")
    reg = sub.add_parser("register")
    reg.add_argument("install_dir", type=Path)
    sub.add_parser("unregister")
    args = parser.parse_args()
    try:
        if args.cmd == "cert":
            make_cert()
        elif args.cmd == "pack":
            print(f"Packed and signed: {pack(args.app_dir)}")
        elif args.cmd == "register":
            register(args.install_dir)
        else:
            unregister()
    except RuntimeError as exc:
        sys.exit(str(exc))


if __name__ == "__main__":
    main()
