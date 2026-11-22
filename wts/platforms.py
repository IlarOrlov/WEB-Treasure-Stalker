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
from dataclasses import dataclass
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
            "extract_flat": "in_playlist" if flat else False, **js_runtime_opts(), **(extra or {})}
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


@dataclass
class PlatformRun:
    downloads: list[dict[str, Any]]
    dest: Path | None
    message: str = ""


def _download(urls_labels: list[tuple[str, str]], dest: Path, choice: FormatChoice, outtmpl: str,
              log: Callable[[str], None], cookies: str | None, cookies_from_browser: str | None,
              playlist_items: str | None = None, noplaylist: bool = True, max_playlist: int = 10_000) -> list[dict]:
    dl = Downloader(dest, cookiefile=cookies, cookies_from_browser=cookies_from_browser, choice=choice,
                    outtmpl=outtmpl, write_info=False, noplaylist=noplaylist, playlist_items=playlist_items,
                    use_archive=False, max_playlist=max_playlist, log=log)
    out = []
    for n, (url, label) in enumerate(urls_labels, 1):
        prefix = f"[{n}/{len(urls_labels)}] " if len(urls_labels) > 1 else ""
        log(f"{prefix}downloading {label or url}")
        r = dl.download(VideoCandidate(url=url, kind="platform-link", source="user", label=label))
        log(f"{' ' * len(prefix)}-> {r.status}" + (f": {', '.join(Path(f).name for f in r.files)}" if r.files else "")
            + (f" ({r.error[:200]})" if r.error and r.status != "downloaded" else ""))
        out.append(r.to_dict())
    return out


def handle_youtube(t: Target, prompter: Prompter, log: Callable[[str], None], cookies: str | None = None,
                   cookies_from_browser: str | None = None) -> PlatformRun:
    if t.kind == "other":
        return PlatformRun([], None, "This YouTube page has no main video (channel, home, search or feed page). "
                                     "Nothing was downloaded. Pass a video (watch/shorts/youtu.be) or playlist link.")
    mode = "video" if t.kind == "video" else "playlist"
    if t.kind == "video-in-playlist":
        mode = prompter.choice("This video is part of a playlist. What should be downloaded?",
                               [("video", "Only this video"), ("playlist", "The whole playlist"),
                                ("select", "Choose specific playlist items")],
                               "select" if prompter.preset_items else "video")
    if mode == "video":
        video_url = f"https://www.youtube.com/watch?v={t.id}"
        try:
            info = _probe(video_url, flat=False)
        except Exception as exc:  # noqa: BLE001
            return PlatformRun([], None, f"No downloadable main video on this page: {_clean_err(exc)}. Nothing was downloaded.")
        if info.get("is_live"):
            log("note: this is a live stream; it will be recorded from now until it ends or you press Ctrl+C")
        log(f"Video : {info.get('title')}  [{_fmt_dur(info.get('duration'))}]  by {info.get('uploader') or info.get('channel')}")
        log(f"Best  : {info.get('height') or '?'}p {info.get('vcodec') or ''} + {info.get('acodec') or ''}")
        dest = prompter.path("~/Downloads/WTS")
        choice = prompter.format("best")
        log(f"Format: {choice.describe()}  ->  {dest}")
        return PlatformRun(_download([(video_url, info.get("title", ""))], dest, choice,
                                     "%(title).150B [%(id)s].%(ext)s", log, cookies, cookies_from_browser), dest)

    pl_url = f"https://www.youtube.com/playlist?list={t.list_id}"
    try:
        info = _probe(pl_url, flat=True, noplaylist=False)
    except Exception as exc:  # noqa: BLE001
        return PlatformRun([], None, f"Playlist could not be read: {_clean_err(exc)}. Nothing was downloaded.")
    entries = [e for e in (info.get("entries") or []) if e]
    if not entries:
        return PlatformRun([], None, "The playlist is empty or private. Nothing was downloaded.")
    log(f"Playlist: {info.get('title')}  ({len(entries)} items, by {info.get('uploader') or info.get('channel') or '?'})")
    _print_items(entries, log)
    items = None
    if mode == "select" or (mode == "playlist" and t.kind == "playlist"):
        items = prompter.items(len(entries), "all")
    chosen = expand_items(items, len(entries))
    log(f"Selected {len(chosen)} of {len(entries)} item(s)")
    base = prompter.path("~/Downloads/WTS")
    dest = base / safe_name(info.get("title") or t.list_id, "playlist")
    dest.mkdir(parents=True, exist_ok=True)
    choice = prompter.format("best")
    log(f"Format: {choice.describe()}  ->  {dest}")
    downloads = _download([(pl_url, info.get("title", ""))], dest, choice,
                          "%(playlist_index)03d - %(title).150B [%(id)s].%(ext)s", log, cookies, cookies_from_browser,
                          playlist_items=items, noplaylist=False)
    return PlatformRun(downloads, dest)


# --------------------------------------------------------------------------- Spotify


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


def handle_spotify(t: Target, prompter: Prompter, log: Callable[[str], None], cookies: str | None = None,
                   cookies_from_browser: str | None = None) -> PlatformRun:
    if t.kind not in ("track", "album", "playlist"):
        return PlatformRun([], None, f"Spotify '{t.kind}' links are not supported (use a track, album or playlist link). "
                                     "Nothing was downloaded.")
    try:
        meta = spotify_metadata(t.kind, t.id)
    except Exception as exc:  # noqa: BLE001
        return PlatformRun([], None, f"Could not read Spotify metadata: {exc}. Nothing was downloaded.")
    tracks = meta["tracks"]
    if not tracks:
        return PlatformRun([], None, "No tracks found on this Spotify page. Nothing was downloaded.")
    log("note: Spotify streams are DRM-protected and are not downloaded; each track is matched to its YouTube upload.")
    if t.kind == "track":
        tr = tracks[0]
        log(f"Track : {tr['artists']} - {tr['title']}  [{_fmt_dur(tr['duration'])}]")
        chosen = [1]
        dest = prompter.path("~/Downloads/WTS")
    else:
        log(f"Spotify {t.kind}: {meta['name']}  ({len(tracks)} tracks{', by ' + meta['owner'] if meta['owner'] else ''})")
        _print_items([{"title": f"{x['artists']} - {x['title']}", "duration": x["duration"]} for x in tracks], log)
        if t.kind == "playlist" and len(tracks) in (50, 100):
            log("note: Spotify's public embed lists at most this many tracks; very long playlists may be truncated.")
        chosen = expand_items(prompter.items(len(tracks), "all"), len(tracks))
        dest = prompter.path("~/Downloads/WTS") / safe_name(meta["name"] or t.id, "spotify")
        dest.mkdir(parents=True, exist_ok=True)
    choice = prompter.format("mp3")
    log(f"Format: {choice.describe()}  ->  {dest}")
    downloads: list[dict[str, Any]] = []
    width = len(str(len(tracks)))
    for n, idx in enumerate(chosen, 1):
        tr = tracks[idx - 1]
        name = f"{tr['artists'].split(',')[0].strip()} - {tr['title']}"
        log(f"[{n}/{len(chosen)}] {name}")
        match = match_on_youtube(tr, log)
        if not match:
            log("   -> no matching YouTube upload found; skipped")
            downloads.append({"candidate": {"url": "", "label": name}, "status": "no-match", "files": [], "info": {},
                              "spotify": tr, "error": "no YouTube match"})
            continue
        log(f"   matched: {match['title']}  [{_fmt_dur(match['duration'])}]  ({match['channel']})")
        stem = safe_name((f"{idx:0{width}d} - " if t.kind != "track" else "") + name).replace("%", "%%")
        r = _download([(match["url"], name)], dest, choice, stem + ".%(ext)s", lambda m: None, cookies, cookies_from_browser)[0]
        log(f"   -> {r['status']}" + (f": {Path(r['files'][0]).name}" if r["files"] else f" ({r['error'][:150]})"))
        r["spotify"] = tr
        r["youtube_match"] = match
        downloads.append(r)
    return PlatformRun(downloads, dest)


# --------------------------------------------------------------------------- entry point


def run_platform(t: Target, prompter: Prompter, log: Callable[[str], None] = print, cookies: str | None = None,
                 cookies_from_browser: str | None = None, scan: bool = True, use_av: bool = True) -> dict[str, Any]:
    handler = handle_youtube if t.platform == "youtube" else handle_spotify
    res = handler(t, prompter, log, cookies, cookies_from_browser)
    if res.message:
        log(res.message)
    findings_total, worst = 0, 0
    if scan:
        for d in res.downloads:
            d["media"] = []
            for f in d.get("files", []):
                p = Path(f)
                if p.suffix.lower() in MEDIA_EXTS and p.exists():
                    m = scan_media(p, use_av=use_av)
                    d["media"].append(m)
                    serious = [x for x in m["findings"] if x["severity"] in ("high", "critical")]
                    findings_total += len(m["findings"])
                    worst = max([worst] + [sev_rank(x["severity"]) for x in m["findings"]])
                    if serious:
                        log(f"  ! {p.name}: " + "; ".join(f"[{x['severity'].upper()}] {x['message']}" for x in serious))
    report = {"target": t.url, "platform": t.platform, "kind": t.kind, "message": res.message,
              "destination": str(res.dest) if res.dest else None, "downloads": res.downloads,
              "highest_severity": ["info", "low", "medium", "high", "critical"][worst],
              "downloaded_files": sum(len(d.get("files", [])) for d in res.downloads)}
    if res.dest:
        rdir = res.dest / ".wts"
        rdir.mkdir(exist_ok=True)
        (rdir / f"report-{time.strftime('%Y%m%d-%H%M%S')}.json").write_text(json.dumps(report, indent=2, default=str))
    return report
