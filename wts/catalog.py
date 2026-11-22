"""Turn discovered candidates into a catalogue of downloadable media the user can choose from.

Items:
  episode  - one episode of a series (sources = streams/files found, or a page to open at download time)
  movie    - the page's main video (film), all players/mirrors/qualities as fallback sources
  video    - any other downloadable video (trailer, embedded clip, platform video)
  audio    - downloadable audio (mp3/flac links, audio tags, audio streams)
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any
from urllib.parse import unquote, urlparse

from bs4 import BeautifulSoup

from .episodes import episode_key, episode_path, group_episodes, label_of, series_title
from .models import VideoCandidate
from .prompts import safe_name
from .signatures import PIRATE_HOST_RE

NON_MEDIA_FRAME_RE = re.compile(r"storage\.html|/share2?/|share\.|controller\.php|/ajax/|comments|disqus|"
                                r"recaptcha|captcha|analytics|metrika|counter|/ads?/|banner", re.I)
_KIND_RANK = {"stream": 0, "direct": 1, "audio": 2, "platform-link": 3, "embed": 4}


@dataclass
class MediaItem:
    id: str
    kind: str  # episode | movie | video | audio
    group: str
    title: str
    season: int | None = None
    episode: int | None = None
    sources: list[VideoCandidate] = field(default_factory=list)
    page: str = ""  # opened at download time when no source was captured yet
    outtmpl: str = ""  # relative output template ("" = default naming)
    status: str = ""
    files: list[str] = field(default_factory=list)

    @property
    def label(self) -> str:
        return label_of((self.season, self.episode)) if self.kind == "episode" else self.title

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["sources"] = [s.to_dict() for s in self.sources]
        d["source_hosts"] = sorted({urlparse(s.url).hostname or "" for s in self.sources})
        d["label"] = self.label
        return d


def page_title(html: str, fallback: str) -> str:
    """Title for a film / main video, keeping the year: 'Железный человек (2008)'."""
    soup = BeautifulSoup(html or "", "html.parser")
    raw = " ".join(x for x in [(soup.find("meta", attrs={"property": "og:title"}) or {}).get("content", ""),
                               soup.title.get_text(" ", strip=True) if soup.title else ""] if x)
    year = re.search(r"\((19\d{2}|20\d{2})\)", raw) or re.search(r"\b(19[3-9]\d|20[0-4]\d)\b", raw)
    title = series_title(html, fallback)
    return safe_name(f"{title} ({year.group(1)})" if year and year.group(1) not in title else title, fallback)


def _name_from_url(url: str) -> str:
    path = unquote(urlparse(url).path).rstrip("/")
    name = path.rsplit("/", 1)[-1] or urlparse(url).hostname or "media"
    return re.sub(r"\.(mp4|m4v|webm|mkv|mov|m3u8|mpd|mp3|m4a|flac|wav|ogg|opus)$", "", name, flags=re.I)[:80]


def build_catalog(pages, candidates: list[VideoCandidate], deferred_pages: list[str], host: str,
                  ytdlp_supported=None) -> tuple[list[MediaItem], str]:
    """Return (items, series title or '')."""
    main_html = (pages[0].rendered_html or pages[0].raw_html) if pages else ""
    items: list[MediaItem] = []
    episodes, rest = group_episodes(candidates)
    # episode pages that were not opened: resolved when the user downloads them
    deferred: dict[tuple[int | None, int], str] = {}
    for link in deferred_pages:
        k = episode_key(VideoCandidate(link, "embed", "episode-page", page=link))
        if k and k not in episodes and k not in deferred:
            deferred[k] = link
    series = series_title(main_html, host) if (episodes or deferred) else ""
    for k in sorted(set(episodes) | set(deferred), key=lambda x: ((x[0] or 0), x[1])):
        it = MediaItem(id=f"ep-{k[0] or 0}-{k[1]}", kind="episode", group=series, title=label_of(k),
                       season=k[0], episode=k[1], sources=episodes.get(k, []), page=deferred.get(k, ""),
                       outtmpl=episode_path(series, k))
        items.append(it)

    # streams of embedded players / the page's own player (not tied to an episode)
    player_frames = {c.referer for c in candidates if c.source == "network"}
    streams = [c for c in rest if c.kind in ("stream", "direct") and c.source in ("network", "inline-script")
               and not c.label]
    others = [c for c in rest if c not in streams]
    if streams and not items:
        title = page_title(main_html, host)
        streams.sort(key=lambda c: (c.protected, 0 if c.source == "network" else 1, _KIND_RANK.get(c.kind, 9),
                                    0 if ".m3u8" in c.url or ".mpd" in c.url else 1))
        items.append(MediaItem(id="movie", kind="movie", group="Film / main video", title=title, sources=streams,
                               outtmpl=f"{title.replace('%', '%%')}/{title.replace('%', '%%')}.%(ext)s"))
    elif streams:
        # next to a series, only streams of the start page itself are offered individually; unattributed player
        # streams from crawled pages are "whatever episode the player had loaded" (kept in the report only)
        start = {pages[0].url, pages[0].final_url} if pages else set()
        others = [c for c in streams if c.page in start] + others

    n = 0
    seen_paths: set[str] = set()
    player_hosts = {urlparse(r).hostname for r in player_frames if r}
    main_found = bool(items)
    for c in sorted(others, key=lambda c: _KIND_RANK.get(c.kind, 9)):
        if c.kind == "embed":
            # an iframe is only offered if something can download it (a dedicated extractor, or a known video
            # host when nothing else was found) and its streams were not captured already
            if c.url in player_frames or urlparse(c.url).hostname in player_hosts or NON_MEDIA_FRAME_RE.search(c.url):
                continue
            supported = bool(ytdlp_supported and ytdlp_supported(c.url))
            if not (supported or (not main_found and PIRATE_HOST_RE.search(urlparse(c.url).netloc))):
                continue
        key = (urlparse(c.url).hostname or "") + urlparse(c.url).path
        if c.source == "network" and urlparse(c.url).path in seen_paths:
            continue  # mirror of a stream already listed
        seen_paths.add(urlparse(c.url).path)
        n += 1
        kind = "audio" if c.kind == "audio" else "video"
        label = c.label or ("YouTube video" if "youtu" in key else _name_from_url(c.url))
        items.append(MediaItem(id=f"{kind}-{n}", kind=kind, group="Audio" if kind == "audio" else "Other videos",
                               title=label, sources=[c]))
    return items, series
