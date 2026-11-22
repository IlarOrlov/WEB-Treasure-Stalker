"""External tool management: ffmpeg/ffprobe, JS runtime (deno), browser, AV engines.

The packaged app ships without these large binaries. They are taken from the
system when present, otherwise downloaded on demand into the app data folder
(checksums verified) and put on PATH for this process.
"""
from __future__ import annotations

import glob
import hashlib
import io
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import threading
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import requests

APP_NAME = "WEB-Treasure-Stalker"
IS_WIN = sys.platform == "win32"
EXE = ".exe" if IS_WIN else ""

ProgressCB = Callable[[int, int], None]  # (done_bytes, total_bytes)


def data_dir(create: bool = False) -> Path:
    """Per-user app folder. Only created when the user asks for something to be stored there
    (installing a tool, or enabling 'remember preferences and history')."""
    if IS_WIN:
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
        d = base / APP_NAME
    elif sys.platform == "darwin":
        d = Path.home() / "Library" / "Application Support" / APP_NAME
    else:
        d = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "web-treasure-stalker"
    if create:
        d.mkdir(parents=True, exist_ok=True)
    return d


def bin_dir(create: bool = False) -> Path:
    d = data_dir(create) / "bin"
    if create:
        d.mkdir(parents=True, exist_ok=True)
    return d


def browsers_dir() -> Path:
    if IS_WIN:
        return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "ms-playwright"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "ms-playwright"
    return Path.home() / ".cache" / "ms-playwright"


_SESSION_TMP: Path | None = None


def session_temp() -> Path:
    """One private temporary folder for this run of the app. Everything temporary (browser profile, helper
    caches, intermediate files of child processes) goes here and the folder is deleted when the app exits."""
    global _SESSION_TMP
    if _SESSION_TMP is None:
        import atexit
        import tempfile

        base = tempfile.gettempdir()
        _SESSION_TMP = Path(tempfile.mkdtemp(prefix="wts-session-", dir=base))
        atexit.register(cleanup_session_temp)
    return _SESSION_TMP


def cleanup_session_temp() -> None:
    global _SESSION_TMP
    if _SESSION_TMP is not None:
        shutil.rmtree(_SESSION_TMP, ignore_errors=True)
        if not _SESSION_TMP.exists():
            _SESSION_TMP = None


_ENV_READY = False


def setup_environment() -> None:
    """Prepare PATH and redirect every temp/cache location of helper programs into the session temp folder.

    Nothing persistent is created here."""
    global _ENV_READY
    # PyInstaller's playwright hook sets PLAYWRIGHT_BROWSERS_PATH=0 (inside the temporary bundle); use the
    # normal per-user location so an engine installed from the Tools page is found.
    if os.environ.get("PLAYWRIGHT_BROWSERS_PATH") in (None, "", "0"):
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(browsers_dir())
    b = str(bin_dir())
    parts = os.environ.get("PATH", "").split(os.pathsep)
    if b not in parts:
        os.environ["PATH"] = os.pathsep.join([b] + parts)
    if _ENV_READY:
        return
    _ENV_READY = True
    import tempfile

    tmp = session_temp()
    for var in ("TMP", "TEMP", "TMPDIR"):  # child processes (Chromium, ffmpeg, node, deno) inherit this
        os.environ[var] = str(tmp)
    tempfile.tempdir = str(tmp)
    os.environ["DENO_DIR"] = str(tmp / "deno")  # deno would otherwise cache under the user profile
    os.environ.setdefault("DENO_NO_UPDATE_CHECK", "1")
    if not IS_WIN:  # generic per-user cache of Linux helper programs (fontconfig, mesa, ...)
        os.environ["XDG_CACHE_HOME"] = str(tmp / "cache")


def _no_window() -> dict:
    if IS_WIN:
        return {"creationflags": 0x08000000}  # CREATE_NO_WINDOW
    return {}


def run_quiet(args: list[str], timeout: int = 15) -> str:
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout, **_no_window())
        return (p.stdout or p.stderr).strip()
    except (OSError, subprocess.SubprocessError):
        return ""


# --------------------------------------------------------------------------- status


@dataclass
class ToolStatus:
    key: str
    name: str
    found: bool
    detail: str
    installable: bool
    purpose: str


def _arch() -> str:
    m = platform.machine().lower()
    return "arm64" if m in ("aarch64", "arm64") else "x64"


def find_browser() -> tuple[str, str] | None:
    """Return (kind, location) for a usable Chromium-family browser."""
    env = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    pw_root = Path(env) if env and env != "0" else browsers_dir()
    for pattern in ("chromium_headless_shell-*", "chromium-*"):
        hits = sorted(glob.glob(str(pw_root / pattern)))
        if hits:
            return "playwright", hits[-1]
    if IS_WIN:
        for env in ("PROGRAMFILES(X86)", "PROGRAMFILES", "LOCALAPPDATA"):
            base = os.environ.get(env)
            if not base:
                continue
            for rel, kind in (("Microsoft/Edge/Application/msedge.exe", "msedge"),
                              ("Google/Chrome/Application/chrome.exe", "chrome")):
                p = Path(base) / rel
                if p.exists():
                    return kind, str(p)
    else:
        for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "microsoft-edge",
                     "microsoft-edge-stable", "brave-browser"):
            p = shutil.which(name)
            if p:
                return "system", p
    return None


def status() -> list[ToolStatus]:
    setup_environment()
    out = []
    ff = shutil.which("ffmpeg")
    fp = shutil.which("ffprobe")
    ver = run_quiet([ff, "-version"]).split("\n")[0][:60] if ff else ""
    out.append(ToolStatus("ffmpeg", "FFmpeg + FFprobe", bool(ff and fp), ver or "not found", True,
                          "merges best video+audio, converts formats, inspects media"))
    js = next(((n, shutil.which(n)) for n in ("deno", "node", "bun") if shutil.which(n)), None)
    out.append(ToolStatus("js", "JavaScript runtime", js is not None,
                          f"{js[0]} ({js[1]})" if js else "not found", True,
                          "needed by yt-dlp to unlock all YouTube formats"))
    br = find_browser()
    out.append(ToolStatus("browser", "Chromium browser engine", br is not None,
                          f"{br[0]}: {br[1]}" if br else "not found", True,
                          "renders web sites, captures streams, walks episode menus"))
    clam = shutil.which("clamdscan") or shutil.which("clamscan")
    out.append(ToolStatus("clamav", "ClamAV antivirus", bool(clam), clam or "not installed (optional)", False,
                          "signature-based malware scan of downloads"))
    try:
        import yara  # noqa: F401
        y = "yara-python"
    except ImportError:
        y = shutil.which("yara") or ""
    out.append(ToolStatus("yara", "YARA rules engine", bool(y), y or "not installed (optional)", False,
                          "bundled rules for payloads hidden in media"))
    return out


# --------------------------------------------------------------------------- downloads


class Cancelled(Exception):
    pass


def _download(url: str, progress: ProgressCB | None, cancel: threading.Event | None) -> bytes:
    with requests.get(url, stream=True, timeout=60, headers={"User-Agent": APP_NAME}) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length") or 0)
        buf = io.BytesIO()
        for chunk in r.iter_content(256 * 1024):
            if cancel is not None and cancel.is_set():
                raise Cancelled()
            buf.write(chunk)
            if progress:
                progress(buf.tell(), total)
    return buf.getvalue()


def _verify(data: bytes, expected: str | None, name: str) -> None:
    if not expected:
        raise RuntimeError(f"no published checksum found for {name}; refusing to install")
    got = hashlib.sha256(data).hexdigest()
    if got.lower() != expected.lower():
        raise RuntimeError(f"checksum mismatch for {name} (expected {expected[:16]}…, got {got[:16]}…)")


def _place(name: str, data: bytes) -> Path:
    dest = bin_dir(create=True) / name
    tmp = dest.with_suffix(dest.suffix + ".part")
    tmp.write_bytes(data)
    if not IS_WIN:
        tmp.chmod(0o755)
    os.replace(tmp, dest)
    return dest


def install_ffmpeg(progress: ProgressCB | None = None, cancel: threading.Event | None = None) -> str:
    base = "https://github.com/yt-dlp/FFmpeg-Builds/releases/download/latest/"
    if IS_WIN:
        asset = f"ffmpeg-master-latest-win{'arm' if _arch() == 'arm64' else ''}64-gpl.zip"
    else:
        asset = f"ffmpeg-master-latest-linux{'arm' if _arch() == 'arm64' else ''}64-gpl.tar.xz"
    sums = requests.get(base + "checksums.sha256", timeout=30).text
    expected = next((ln.split()[0] for ln in sums.splitlines() if ln.strip().endswith(asset)), None)
    data = _download(base + asset, progress, cancel)
    _verify(data, expected, asset)
    wanted = {f"ffmpeg{EXE}", f"ffprobe{EXE}"}
    found = 0
    if asset.endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            for n in z.namelist():
                if Path(n).name in wanted and "/bin/" in n:
                    _place(Path(n).name, z.read(n))
                    found += 1
    else:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:xz") as t:
            for m in t.getmembers():
                if Path(m.name).name in wanted and "/bin/" in m.name and m.isfile():
                    _place(Path(m.name).name, t.extractfile(m).read())
                    found += 1
    if found < 2:
        raise RuntimeError("ffmpeg archive did not contain ffmpeg and ffprobe")
    setup_environment()
    return str(bin_dir())


def install_deno(progress: ProgressCB | None = None, cancel: threading.Event | None = None) -> str:
    triple = {("win", "x64"): "x86_64-pc-windows-msvc", ("linux", "x64"): "x86_64-unknown-linux-gnu",
              ("linux", "arm64"): "aarch64-unknown-linux-gnu", ("win", "arm64"): "x86_64-pc-windows-msvc"}
    key = ("win" if IS_WIN else "linux", _arch())
    asset = f"deno-{triple[key]}.zip"
    base = "https://github.com/denoland/deno/releases/latest/download/"
    expected = requests.get(base + asset + ".sha256sum", timeout=30).text.split()
    data = _download(base + asset, progress, cancel)
    _verify(data, expected[0] if expected else None, asset)
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        _place(f"deno{EXE}", z.read(f"deno{EXE}"))
    setup_environment()
    return str(bin_dir() / f"deno{EXE}")


def install_browser(line: Callable[[str], None] | None = None, cancel: threading.Event | None = None) -> str:
    """Download Playwright's headless Chromium using the bundled Playwright driver."""
    from playwright._impl._driver import compute_driver_executable, get_driver_env

    node, cli = compute_driver_executable()
    proc = subprocess.Popen([node, cli, "install", "--only-shell", "chromium"], env=get_driver_env(),
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, **_no_window())
    assert proc.stdout is not None
    for ln in proc.stdout:
        if cancel is not None and cancel.is_set():
            proc.kill()
            raise Cancelled()
        if line:
            line(ln.rstrip())
    if proc.wait() != 0:
        raise RuntimeError("browser installation failed")
    found = find_browser()
    return found[1] if found else ""


def launch_chromium(pw, headless: bool = True, args: list[str] | None = None):
    """Launch Playwright Chromium, falling back to Edge/Chrome/Chromium installed on the system."""
    errors = []
    try:
        return pw.chromium.launch(headless=headless, args=args or [])
    except Exception as exc:  # noqa: BLE001
        errors.append(str(exc).splitlines()[0])
    for channel in ("msedge", "chrome"):
        try:
            return pw.chromium.launch(channel=channel, headless=headless, args=args or [])
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{channel}: {str(exc).splitlines()[0]}")
    br = find_browser()
    if br and br[0] != "playwright":
        return pw.chromium.launch(executable_path=br[1], headless=headless, args=args or [])
    raise RuntimeError("No Chromium browser available. Install it from the Tools page "
                       "(or run: WEB-Treasure-Stalker --install browser). Details: " + " | ".join(errors))
