"""Download video candidates in the best available quality using yt-dlp.

DRM-protected streams (Widevine/PlayReady/FairPlay) are detected and reported,
never circumvented.
"""
from __future__ import annotations

import os
import re
import shutil
import stat
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

from .models import DownloadResult, VideoCandidate
from .prompts import FormatChoice

INFO_KEYS = (
    "id", "title", "extractor", "extractor_key", "webpage_url", "uploader", "uploader_id", "channel",
    "upload_date", "duration", "width", "height", "fps", "vcodec", "acodec", "format", "format_id",
    "ext", "filesize", "filesize_approx", "tbr", "dynamic_range", "series", "season_number",
    "episode_number", "episode", "description", "license", "is_live", "was_live", "age_limit",
)


class _Logger:
    def __init__(self, log: Callable[[str], None]):
        self.log = log
        self.errors: list[str] = []

    def debug(self, msg: str) -> None:
        pass

    def info(self, msg: str) -> None:
        pass

    def warning(self, msg: str) -> None:
        if "DRM" in msg:
            self.errors.append(msg)

    def error(self, msg: str) -> None:
        self.errors.append(msg)


def _safe_label(label: str) -> str:
    s = re.sub(r"[^\w\-. ]+", "_", label.replace(" / ", " - ")).strip(" ._")
    return (s[:80] + " - ").replace("%", "%%") if s else ""


PARTIAL_RE = re.compile(r"(\.part(-Frag\d+)?|\.ytdl|\.temp(\.\w+)?|\.f\d+\.\w+|-Frag\d+)$", re.I)


def _snapshot(root: Path) -> set[Path]:
    try:
        return {p for p in root.rglob("*")}
    except OSError:
        return set()


def _remove_partials(root: Path, before: set[Path]) -> None:
    """After a cancel or failure, delete the partial/intermediate files this download left behind."""
    for p in sorted(_snapshot(root) - before, key=lambda x: len(x.parts), reverse=True):
        try:
            if p.is_file() and PARTIAL_RE.search(p.name):
                p.unlink()
            elif p.is_dir() and not any(p.iterdir()):
                p.rmdir()
        except OSError:
            pass


def quarantine(path: Path) -> None:
    """Make downloaded files read-only and non-executable."""
    try:
        path.chmod(stat.S_IRUSR | stat.S_IRGRP)
    except OSError:
        pass


def js_runtime_opts() -> dict:
    """yt-dlp needs a JS runtime for YouTube; it defaults to deno, fall back to node/bun."""
    if shutil.which("deno"):
        return {}
    for rt in ("node", "bun"):
        if shutil.which(rt):
            return {"js_runtimes": {rt: {}}}
    return {}


class Downloader:
    def __init__(self, dest: Path, cookiefile: str | None = None, cookies_from_browser: str | None = None,
                 choice: FormatChoice | None = None, max_playlist: int = 50, user_agent: str | None = None,
                 subtitles: bool = False, outtmpl: str | None = None, write_info: bool = True,
                 noplaylist: bool = False, playlist_items: str | None = None, use_archive: bool = True,
                 quarantine_files: bool = True,
                 progress: bool = False, log: Callable[[str], None] = print,
                 on_progress: Callable[[dict], None] | None = None, cancel=None):
        self.dest = dest
        self.cookiefile = cookiefile
        self.cookies_from_browser = cookies_from_browser
        self.choice = choice or FormatChoice()
        self.max_playlist = max_playlist
        self.user_agent = user_agent
        self.subtitles = subtitles
        self.outtmpl = outtmpl
        self.write_info = write_info
        self.noplaylist = noplaylist
        self.playlist_items = playlist_items
        self.progress = progress
        self.on_progress = on_progress
        self.cancel = cancel
        self.log = log
        # in-memory archive (a set): skips duplicates within this run without writing a file
        self.archive: set | None = set() if use_archive else None
        self.quarantine_files = quarantine_files
        self.dest.mkdir(parents=True, exist_ok=True)

    def _opts(self, cand: VideoCandidate, files: list[str], logger: _Logger, outtmpl: str | None = None) -> dict:
        headers = {}
        if cand.referer:
            headers["Referer"] = cand.referer
            p = urlparse(cand.referer)
            headers["Origin"] = f"{p.scheme}://{p.netloc}"
        if self.user_agent:
            headers["User-Agent"] = self.user_agent
        tmpl = outtmpl or self.outtmpl or str(Path("%(extractor_key)s") / (_safe_label(cand.label) + "%(title).120B [%(id)s].%(ext)s"))
        opts = {
            "outtmpl": {"default": str(self.dest / tmpl)},
            "http_headers": headers,
            "quiet": True, "no_warnings": True, "noprogress": not self.progress,
            "windowsfilenames": True,
            "noplaylist": self.noplaylist, "playlistend": self.max_playlist,
            # fail fast on a refused stream so the next source is tried instead of hanging
            "retries": 3, "fragment_retries": 3, "skip_unavailable_fragments": False, "extractor_retries": 1,
            "concurrent_fragment_downloads": 4,
            "writeinfojson": self.write_info, "clean_infojson": True,
            "writesubtitles": self.subtitles, "subtitleslangs": ["all"],
            "allow_unplayable_formats": False,  # never touch DRM-protected formats
            "logger": logger,
            "post_hooks": [files.append],
            "progress_hooks": [self._hook],
            "socket_timeout": 30,
            "cachedir": False,  # yt-dlp would otherwise keep a cache under the user profile
            **js_runtime_opts(),
            **self.choice.ytdl_opts(),
        }
        if self.playlist_items:
            opts["playlist_items"] = self.playlist_items
            opts.pop("playlistend")
        if self.archive is not None:
            opts["download_archive"] = self.archive
        if self.cookiefile:
            opts["cookiefile"] = self.cookiefile
        if self.cookies_from_browser:
            opts["cookiesfrombrowser"] = (self.cookies_from_browser,)
        return opts

    def _hook(self, d: dict) -> None:
        if self.cancel is not None and self.cancel.is_set():
            from yt_dlp.utils import DownloadCancelled

            raise DownloadCancelled("cancelled by user")
        if self.on_progress:
            info = d.get("info_dict") or {}
            self.on_progress({
                "status": d.get("status"), "downloaded": d.get("downloaded_bytes") or 0,
                "total": d.get("total_bytes") or d.get("total_bytes_estimate") or 0,
                "speed": d.get("speed") or 0, "eta": d.get("eta"), "title": info.get("title") or "",
                "index": info.get("playlist_index"), "count": info.get("n_entries") or info.get("playlist_count"),
                "filename": d.get("filename") or "",
            })

    def download(self, cand: VideoCandidate, outtmpl: str | None = None) -> DownloadResult:
        import yt_dlp

        files: list[str] = []
        logger = _Logger(self.log)
        before = _snapshot(self.dest)
        res = DownloadResult(candidate=cand, status="error")
        try:
            with yt_dlp.YoutubeDL(self._opts(cand, files, logger, outtmpl)) as ydl:
                info = ydl.extract_info(cand.url, download=True)
                if info:
                    entries = info.get("entries")
                    first = next(iter(entries), None) if entries else info
                    if isinstance(first, dict):
                        res.info = {k: first.get(k) for k in INFO_KEYS if first.get(k) is not None}
                        if entries:
                            res.info["playlist_title"] = info.get("title")
                            res.info["playlist_count"] = info.get("playlist_count")
            res.files = sorted({f for f in files if os.path.exists(f)})
            res.status = "downloaded" if res.files else "duplicate"
        except Exception as exc:  # noqa: BLE001  (yt_dlp raises DownloadError and friends)
            msg = re.sub(r"\x1b\[[0-9;]*m", "", str(exc))
            res.error = msg[:500]
            low = msg.lower()
            if "cancelled" in low:
                res.status = "cancelled"
            elif "drm" in low:
                res.status = "drm"
            elif any(s in low for s in ("unsupported url", "no video formats", "no video could be found",
                                        "there's no video", "does not contain any video", "is not a valid url")):
                res.status = "no-video"
            else:
                res.status = "error"
            res.files = sorted({f for f in files if os.path.exists(f)})
            if res.files:
                res.status = "downloaded"
        if res.status != "downloaded":
            _remove_partials(self.dest, before)
        if self.quarantine_files:
            for f in res.files:
                quarantine(Path(f))
        return res
