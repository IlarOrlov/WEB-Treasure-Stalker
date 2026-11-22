"""Direct handling of YouTube and Spotify links.

YouTube
  * video page (watch / youtu.be / shorts / live / embed): only the main video is
    downloaded; if there is none (channel, home, search, removed video) nothing is
    downloaded and the reason is shown.
  * playlist: the items are listed and the user picks all or specific ones.
Spotify
  * track / album / playlist metadata is read from Spotify's public embed page.
    Spotify audio itself is DRM-protected and is NOT downloaded; each track is
    matched to the corresponding YouTube upload, which is downloaded instead.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

import requests

from .discovery import DEFAULT_UA
from .downloader import Downloader, js_runtime_opts
from .media_scan import MEDIA_EXTS, scan_media
from .models import VideoCandidate, sev_rank
from .prompts import FormatChoice, Prompter, expand_items, safe_name

YT_HOSTS = ("youtube.com", "youtu.be", "youtube-nocookie.com")
YT_ID = r"[A-Za-z0-9_-]{11}"


@dataclass
class Target:
    platform: str  # youtube | spotify
    kind: str  # video | playlist | video-in-playlist | other | track | album
    id: str = ""
    list_id: str = ""
    url: str = ""


def _host(url: str) -> str:
    return (urlparse(url).hostname or "").lower()


def classify(url: str) -> Target | None:
    url = url.strip()
    m = re.fullmatch(r"spotify:(track|album|playlist|artist|show|episode):([A-Za-z0-9]+)", url)
    if m:
        return Target("spotify", m.group(1), m.group(2), url=f"https://open.spotify.com/{m.group(1)}/{m.group(2)}")
    host = _host(url)
    if host == "open.spotify.com" or host.endswith(".spotify.com") and "open" in host:
        m = re.search(r"/(?:intl-[a-z-]+/)?(?:embed/)?(track|album|playlist|artist|show|episode)/([A-Za-z0-9]+)", urlparse(url).path)
        if m:
            return Target("spotify", m.group(1), m.group(2), url=url)
        return Target("spotify", "other", url=url)
    if not any(host == h or host.endswith("." + h) for h in YT_HOSTS):
        return None
    p = urlparse(url)
    q = parse_qs(p.query)
    vid = ""
    if host.endswith("youtu.be"):
        vid = p.path.strip("/").split("/")[0]
    elif p.path == "/watch":
        vid = (q.get("v") or [""])[0]
    else:
        m = re.match(rf"/(?:shorts|live|embed|v|e)/({YT_ID})", p.path)
        vid = m.group(1) if m else ""
    vid = vid if re.fullmatch(YT_ID, vid or "") else ""
    list_id = (q.get("list") or [""])[0]
    if vid and list_id:
        return Target("youtube", "video-in-playlist", vid, list_id, url)
    if vid:
        return Target("youtube", "video", vid, url=url)
    if list_id:
        return Target("youtube", "playlist", list_id=list_id, url=url)
    return Target("youtube", "other", url=url)


# --------------------------------------------------------------------------- yt-dlp helpers


def _probe(url: str, flat: bool, noplaylist: bool = True, extra: dict | None = None) -> dict[str, Any]:
    import yt_dlp

    opts = {"quiet": True, "no_warnings": True, "skip_download": True, "noplaylist": noplaylist,
            "extract_flat": "in_playlist" if flat else False, "cachedir": False, **js_runtime_opts(), **(extra or {})}
    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=False) or {}


def _clean_err(exc: Exception) -> str:
    return re.sub(r"\x1b\[[0-9;]*m|^ERROR:\s*(\[[^\]]+\]\s*)?([A-Za-z0-9_-]+:\s*)?", "", str(exc)).strip()[:300]


def _fmt_dur(sec: Any) -> str:
    try:
        s = int(float(sec))
    except (TypeError, ValueError):
        return "--:--"
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


def _print_items(entries: list[dict[str, Any]], log: Callable[[str], None], limit: int = 300) -> None:
    for i, e in enumerate(entries[:limit], 1):
        log(f"  {i:>4}. [{_fmt_dur(e.get('duration'))}] {e.get('title') or e.get('id') or '?'}")
    if len(entries) > limit:
        log(f"  ... and {len(entries) - limit} more")


# --------------------------------------------------------------------------- YouTube


def spotify_metadata(kind: str, sid: str, timeout: int = 20) -> dict[str, Any]:
    """Read title/artists/durations from Spotify's public embed page (no API key)."""
    r = requests.get(f"https://open.spotify.com/embed/{kind}/{sid}", headers={"User-Agent": DEFAULT_UA}, timeout=timeout)
    r.raise_for_status()
    return parse_spotify_embed(r.text)


def parse_spotify_embed(html: str) -> dict[str, Any]:
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not m:
        raise ValueError("Spotify embed page did not contain metadata")
    entity = json.loads(m.group(1))["props"]["pageProps"]["state"]["data"]["entity"]
    kind = entity.get("type")
    if kind == "track":
        artists = ", ".join(a["name"] for a in entity.get("artists", []))
        tracks = [{"title": entity.get("name") or entity.get("title"), "artists": artists,
                   "duration": (entity.get("duration") or 0) / 1000}]
    else:
        tracks = [{"title": t.get("title"), "artists": (t.get("subtitle") or "").replace("\xa0", " "),
                   "duration": (t.get("duration") or 0) / 1000}
                  for t in entity.get("trackList", []) if t.get("entityType", "track") == "track"]
    return {"type": kind, "name": entity.get("name") or entity.get("title"),
            "owner": entity.get("subtitle") or "", "tracks": tracks}


def match_on_youtube(track: dict[str, Any], log: Callable[[str], None]) -> dict[str, Any] | None:
    """Find the YouTube upload that best matches a Spotify track (closest duration among top results)."""
    query = f"{track['artists'].split(',')[0]} - {track['title']}"
    try:
        info = _probe(f"ytsearch6:{query} audio", flat=True, noplaylist=False)
    except Exception as exc:  # noqa: BLE001
        log(f"  ! search failed: {_clean_err(exc)}")
        return None
    entries = [e for e in (info.get("entries") or []) if e and e.get("id")]
    if not entries:
        return None
    want = track.get("duration") or 0

    def score(e: dict[str, Any]) -> float:
        d = e.get("duration") or 0
        title = (e.get("title") or "").lower()
        s = abs(d - want) if want and d else 30.0
        if any(w in title for w in ("live", "cover", "karaoke", "reaction", "remix", "8d", "slowed", "sped up")) and \
                not any(w in track["title"].lower() for w in ("live", "remix", "cover")):
            s += 60
        if "official" in title or "topic" in (e.get("channel") or e.get("uploader") or "").lower():
            s -= 5
        return s

    best = min(entries, key=score)
    return {"url": f"https://www.youtube.com/watch?v={best['id']}", "title": best.get("title"),
            "duration": best.get("duration"), "channel": best.get("channel") or best.get("uploader")}




# --------------------------------------------------------------------------- inspection


@dataclass
class Inspection:
    target: Target
    ok: bool
    message: str = ""
    title: str = ""
    uploader: str = ""
    duration: float | None = None
    thumbnail: str = ""
    quality: str = ""
    is_live: bool = False
    video_url: str = ""
    playlist_url: str = ""
    playlist_title: str = ""
    entries: list[dict[str, Any]] = field(default_factory=list)  # {title, duration, artists?}

    @property
    def is_collection(self) -> bool:
        return bool(self.entries) and (self.target.kind in ("playlist", "album") or self.target.kind == "video-in-playlist")


def inspect(t: Target, log: Callable[[str], None] = lambda m: None) -> Inspection:
    """Read what a YouTube/Spotify link contains without downloading anything."""
    if t.platform == "youtube":
        return _inspect_youtube(t)
    return _inspect_spotify(t)


def _inspect_youtube(t: Target) -> Inspection:
    ins = Inspection(t, ok=False)
    if t.kind == "other":
        ins.message = ("This YouTube page has no main video (channel, home, search or feed page). "
                       "Nothing will be downloaded. Use a video (watch/shorts/youtu.be) or playlist link.")
        return ins
    if t.kind in ("video", "video-in-playlist"):
        ins.video_url = f"https://www.youtube.com/watch?v={t.id}"
        try:
            info = _probe(ins.video_url, flat=False)
        except Exception as exc:  # noqa: BLE001
            if t.kind == "video":
                ins.message = f"No downloadable main video on this page: {_clean_err(exc)}. Nothing will be downloaded."
                return ins
            info = {}
        ins.title = info.get("title") or ""
        ins.uploader = info.get("uploader") or info.get("channel") or ""
        ins.duration = info.get("duration")
        ins.thumbnail = info.get("thumbnail") or ""
        ins.is_live = bool(info.get("is_live"))
        if info:
            fps = f"{int(info['fps'])}fps " if info.get("fps") else ""
            ins.quality = f"{info.get('height') or '?'}p {fps}· {info.get('vcodec') or '?'} + {info.get('acodec') or '?'}"
    if t.kind in ("playlist", "video-in-playlist"):
        ins.playlist_url = f"https://www.youtube.com/playlist?list={t.list_id}"
        try:
            pl = _probe(ins.playlist_url, flat=True, noplaylist=False)
        except Exception as exc:  # noqa: BLE001
            if t.kind == "playlist":
                ins.message = f"Playlist could not be read: {_clean_err(exc)}. Nothing will be downloaded."
                return ins
            pl = {}
        ins.playlist_title = pl.get("title") or ""
        ins.entries = [{"title": e.get("title") or e.get("id") or "?", "duration": e.get("duration"),
                        "id": e.get("id"), "uploader": e.get("channel") or e.get("uploader") or ""}
                       for e in (pl.get("entries") or []) if e]
        if t.kind == "playlist":
            if not ins.entries:
                ins.message = "The playlist is empty or private. Nothing will be downloaded."
                return ins
            ins.title = ins.playlist_title
            ins.uploader = pl.get("uploader") or pl.get("channel") or ""
            thumbs = pl.get("thumbnails") or []
            ins.thumbnail = thumbs[-1]["url"] if thumbs else ""
    ins.ok = True
    return ins


def _inspect_spotify(t: Target) -> Inspection:
    ins = Inspection(t, ok=False)
    if t.kind not in ("track", "album", "playlist"):
        ins.message = (f"Spotify '{t.kind}' links are not supported (use a track, album or playlist link). "
                       "Nothing will be downloaded.")
        return ins
    try:
        meta = spotify_metadata(t.kind, t.id)
    except Exception as exc:  # noqa: BLE001
        ins.message = f"Could not read Spotify metadata: {exc}. Nothing will be downloaded."
        return ins
    if not meta["tracks"]:
        ins.message = "No tracks found on this Spotify page. Nothing will be downloaded."
        return ins
    ins.entries = meta["tracks"]
    if t.kind == "track":
        tr = meta["tracks"][0]
        ins.title, ins.uploader, ins.duration = tr["title"], tr["artists"], tr["duration"]
    else:
        ins.title, ins.uploader = meta["name"] or "", meta["owner"]
        ins.playlist_title = ins.title
        ins.duration = sum(x["duration"] or 0 for x in meta["tracks"])
    try:
        oe = requests.get("https://open.spotify.com/oembed", params={"url": f"https://open.spotify.com/{t.kind}/{t.id}"},
                          headers={"User-Agent": DEFAULT_UA}, timeout=15).json()
        ins.thumbnail = oe.get("thumbnail_url") or ""
    except Exception:  # noqa: BLE001
        pass
    ins.quality = "matched to YouTube uploads (Spotify audio is DRM-protected)"
    ins.ok = True
    return ins


# --------------------------------------------------------------------------- downloading


def _items_spec(items: list[int] | None) -> str | None:
    if not items:
        return None
    items = sorted(set(items))
    out, start, prev = [], items[0], items[0]
    for i in items[1:] + [None]:
        if i is not None and i == prev + 1:
            prev = i
            continue
        out.append(f"{start}-{prev}" if start != prev else str(start))
        if i is not None:
            start = prev = i
    return ",".join(out)


def download(ins: Inspection, choice: FormatChoice, base: Path, mode: str = "auto", items: list[int] | None = None,
             log: Callable[[str], None] = print, on_progress: Callable[[dict], None] | None = None, cancel=None,
             cookies: str | None = None, cookies_from_browser: str | None = None, scan: bool = True,
             use_av: bool = True, save_report: bool = False) -> dict[str, Any]:
    """Download an inspected link.

    mode: 'video' (only the main video), 'playlist' (collection; `items` = 1-based indexes or None for all),
          'auto' (video for single links, playlist for collections).
    """
    t = ins.target
    if mode == "auto":
        mode = "video" if t.kind in ("video", "video-in-playlist", "track") else "playlist"
    base = Path(base).expanduser()
    base.mkdir(parents=True, exist_ok=True)
    kw = dict(cookiefile=cookies, cookies_from_browser=cookies_from_browser, choice=choice, write_info=False,
              use_archive=False, quarantine_files=False, log=log, on_progress=on_progress, cancel=cancel)
    downloads: list[dict[str, Any]] = []
    if t.platform == "youtube":
        if mode == "video":
            dest = base
            dl = Downloader(dest, outtmpl="%(title).150B [%(id)s].%(ext)s", noplaylist=True, **kw)
            log(f"Downloading: {ins.title or ins.video_url}")
            r = dl.download(VideoCandidate(ins.video_url, "platform-link", "user", label=ins.title))
            downloads.append(r.to_dict())
        else:
            dest = base / safe_name(ins.playlist_title or t.list_id, "playlist")
            spec = _items_spec(items)
            n = len(items) if items else len(ins.entries)
            log(f"Downloading {n} item(s) of playlist '{ins.playlist_title}'")
            dl = Downloader(dest, outtmpl="%(playlist_index)03d - %(title).150B [%(id)s].%(ext)s", noplaylist=False,
                            playlist_items=spec, max_playlist=100_000, **kw)
            r = dl.download(VideoCandidate(ins.playlist_url, "platform-link", "user", label=ins.playlist_title))
            downloads.append(r.to_dict())
        for d in downloads:
            log(f"-> {d['status']}" + (f": {len(d['files'])} file(s)" if d["files"] else "")
                + (f" ({d['error'][:200]})" if d["error"] and d["status"] != "downloaded" else ""))
    else:
        tracks = ins.entries
        chosen = [1] if t.kind == "track" else (sorted(set(items)) if items else list(range(1, len(tracks) + 1)))
        dest = base if t.kind == "track" else base / safe_name(ins.playlist_title or t.id, "spotify")
        dest.mkdir(parents=True, exist_ok=True)
        width = len(str(len(tracks)))
        for n, idx in enumerate(chosen, 1):
            if cancel is not None and cancel.is_set():
                log("Cancelled.")
                break
            tr = tracks[idx - 1]
            name = f"{tr['artists'].split(',')[0].strip()} - {tr['title']}"
            log(f"[{n}/{len(chosen)}] {name}")
            if on_progress:
                on_progress({"status": "searching", "title": name, "index": n, "count": len(chosen),
                             "downloaded": 0, "total": 0})
            match = match_on_youtube(tr, log)
            if not match:
                log("   -> no matching YouTube upload found; skipped")
                downloads.append({"candidate": {"url": "", "label": name}, "status": "no-match", "files": [],
                                  "info": {}, "spotify": tr, "error": "no YouTube match"})
                continue
            log(f"   matched: {match['title']}  [{_fmt_dur(match['duration'])}]  ({match['channel']})")
            stem = safe_name((f"{idx:0{width}d} - " if t.kind != "track" else "") + name).replace("%", "%%")

            def prog(d, n=n):
                if on_progress:
                    on_progress({**d, "title": name, "index": n, "count": len(chosen)})

            dl = Downloader(dest, outtmpl=stem + ".%(ext)s", noplaylist=True,
                            **{**kw, "on_progress": prog, "log": lambda m: None})
            r = dl.download(VideoCandidate(match["url"], "platform-link", "spotify-match", label=name)).to_dict()
            log(f"   -> {r['status']}" + (f": {Path(r['files'][0]).name}" if r["files"] else f" ({r['error'][:150]})"))
            r["spotify"] = tr
            r["youtube_match"] = match
            downloads.append(r)

    worst = 0
    if scan:
        for d in downloads:
            d["media"] = []
            for f in d.get("files", []):
                p = Path(f)
                if p.suffix.lower() in MEDIA_EXTS and p.exists():
                    if on_progress:
                        on_progress({"status": "scanning", "title": p.name, "downloaded": 0, "total": 0})
                    m = scan_media(p, use_av=use_av)
                    d["media"].append(m)
                    worst = max([worst] + [sev_rank(x["severity"]) for x in m["findings"]])
                    serious = [x for x in m["findings"] if x["severity"] in ("high", "critical")]
                    if serious:
                        log(f"  ! {p.name}: " + "; ".join(f"[{x['severity'].upper()}] {x['message']}" for x in serious))
    report = {"target": t.url, "platform": t.platform, "kind": t.kind, "mode": mode, "message": ins.message,
              "destination": str(dest), "downloads": downloads,
              "highest_severity": ["info", "low", "medium", "high", "critical"][worst],
              "downloaded_files": sum(len(d.get("files", [])) for d in downloads)}
    if save_report:  # only when explicitly requested (CLI --save-report)
        rdir = dest / ".wts"
        rdir.mkdir(parents=True, exist_ok=True)
        (rdir / f"report-{time.strftime('%Y%m%d-%H%M%S')}.json").write_text(json.dumps(report, indent=2, default=str))
    return report


# --------------------------------------------------------------------------- CLI flow


def run_platform(t: Target, prompter: Prompter, log: Callable[[str], None] = print, cookies: str | None = None,
                 cookies_from_browser: str | None = None, scan: bool = True, use_av: bool = True,
                 save_report: bool = False) -> dict[str, Any]:
    ins = inspect(t)
    empty = {"target": t.url, "platform": t.platform, "kind": t.kind, "message": ins.message, "destination": None,
             "downloads": [], "highest_severity": "info", "downloaded_files": 0}
    if not ins.ok:
        log(ins.message)
        return empty
    if t.platform == "spotify" and t.kind != "track":
        log("note: Spotify streams are DRM-protected and are not downloaded; each track is matched to its YouTube upload.")
    mode, items = "auto", None
    if t.kind == "video-in-playlist" and ins.entries:
        mode = prompter.choice("This video is part of a playlist. What should be downloaded?",
                               [("video", "Only this video"), ("playlist", "The whole playlist"),
                                ("select", "Choose specific playlist items")],
                               "select" if prompter.preset_items else "video")
    elif t.kind == "video-in-playlist":
        mode = "video"
    if mode == "auto" and t.kind in ("video", "track"):
        log(f"{'Video' if t.platform == 'youtube' else 'Track'} : {ins.title}  [{_fmt_dur(ins.duration)}]  by {ins.uploader}")
        if ins.quality:
            log(f"Best  : {ins.quality}")
        if ins.is_live:
            log("note: this is a live stream; it will be recorded until it ends or you press Ctrl+C")
        mode = "video"
    elif mode != "video":
        log(f"{t.kind.replace('-', ' ').title()}: {ins.playlist_title or ins.title}  ({len(ins.entries)} items)")
        _print_items([{"title": (f"{x['artists']} - {x['title']}" if "artists" in x else x["title"]),
                       "duration": x.get("duration")} for x in ins.entries], log)
        if t.platform == "spotify" and t.kind == "playlist" and len(ins.entries) in (50, 100):
            log("note: Spotify's public embed lists at most this many tracks; very long playlists may be truncated.")
        spec = None
        if mode == "select" or t.kind in ("playlist", "album"):
            spec = prompter.items(len(ins.entries), "all")
        items = expand_items(spec, len(ins.entries)) if spec else None
        log(f"Selected {len(items) if items else len(ins.entries)} of {len(ins.entries)} item(s)")
        mode = "playlist"
    base = prompter.path("~/Downloads/WTS")
    choice = prompter.format("mp3" if t.platform == "spotify" else "best")
    log(f"Format: {choice.describe()}  ->  {base}")
    return download(ins, choice, base, mode, items, log=log, cookies=cookies,
                    cookies_from_browser=cookies_from_browser, scan=scan, use_av=use_av, save_report=save_report)
