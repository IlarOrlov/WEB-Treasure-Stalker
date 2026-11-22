"""Recognise series episodes among video candidates and lay them out as <Series>/Season N/Episode MM."""
from __future__ import annotations

import re
from urllib.parse import unquote, urlparse

from bs4 import BeautifulSoup

from .models import VideoCandidate
from .prompts import safe_name

SEASON_W = r"(?:season|сезон|temporada|saison|staffel|stagione|sezon|עונה)"
EPISODE_W = r"(?:episode|серия|серии|эпизод|episodio|épisode|folge|odcinek|bölüm|epizod|serija|seriya|seriia|seria|פרק|ep)"

_PATTERNS = [
    re.compile(rf"{SEASON_W}\s*(\d{{1,2}})\s*/\s*{EPISODE_W}\s*(\d{{1,4}})", re.I),            # our own labels
    # season-1-episode-3, and season-3-episode-3-7 (season repeated before the episode number)
    re.compile(rf"{SEASON_W}[-_. ]?(\d{{1,2}})[-_. /]*{EPISODE_W}[-_. ]?(\d{{1,4}})(?:[-_.](\d{{1,3}})(?![0-9p]))?", re.I),
    re.compile(r"(?:^|[-_./ \[(])s(\d{1,2})[-_. ]?e(\d{1,4})(?=$|[-_./ \])])", re.I),            # S01E03 (delimited)
    re.compile(rf"(\d{{1,2}})[-_ ]?{SEASON_W}.{{0,20}}?(\d{{1,4}})[-_ ]?{EPISODE_W}", re.I),     # 1 сезон 3 серия
    re.compile(r"(?:^|[-_./ \[(])(\d{1,2})x(\d{1,3})(?=$|[-_./ \])])", re.I),                    # 1x03 (delimited)
]
_EPISODE_ONLY = re.compile(rf"(?:^|[/\s-]){EPISODE_W}[-_. ]?(\d{{1,4}})\b", re.I)

# candidates whose page URL describes them (the page's own main video), as opposed to links listed on it
_PAGE_SOURCES = {"video-tag", "video-source", "video-element", "network", "og:video", "og:video:url",
                 "og:video:secure_url", "json-ld", "frame", "iframe"}

TITLE_CUT = re.compile(
    r"\s*(?:[|—–:]|\s-\s|\(|\[|смотреть|онлайн|бесплатно|все\s+сезоны|все\s+серии|\d+\s*[-–]?\s*\d*\s*сезон|"
    r"сезон\s*\d|watch|online|free|all\s+seasons|season\s*\d|\d+\s*season|full\s+episodes|в\s+хорошем|hd\s*\d{3,4}).*$",
    re.I,
)
TITLE_PREFIX = re.compile(r"^\s*(?:сериал|serial|tv\s+series|the\s+series|series|фильм|мультсериал)\s+", re.I)


def episode_key(c: VideoCandidate) -> tuple[int | None, int] | None:
    texts = [c.label, unquote(urlparse(c.url).path)]
    if c.source in _PAGE_SOURCES:
        texts.append(unquote(urlparse(c.page or c.referer).path))
    for t in texts:
        if not t:
            continue
        for rx in _PATTERNS:
            m = rx.search(t)
            if m:
                s, e = int(m.group(1)), int(m.group(2))
                if rx.groups >= 3 and m.group(3) and e == s:  # "episode-<season>-<episode>"
                    e = int(m.group(3))
                return s, e
    if c.label:
        m = _EPISODE_ONLY.search(c.label)
        if m:
            return None, int(m.group(1))
    return None


def series_title(html: str, fallback: str) -> str:
    soup = BeautifulSoup(html or "", "html.parser")
    cands = []
    og = soup.find("meta", attrs={"property": "og:title"})
    if og and og.get("content"):
        cands.append(og["content"])
    h1 = soup.find("h1")
    if h1:
        cands.append(h1.get_text(" ", strip=True))
    if soup.title:
        cands.append(soup.title.get_text(" ", strip=True))
    for raw in cands:
        t = TITLE_PREFIX.sub("", TITLE_CUT.sub("", raw)).strip(" .,-–—")
        if 2 <= len(t) <= 80:
            return safe_name(t, fallback)
    return safe_name(fallback, "Series")


def label_of(key: tuple[int | None, int]) -> str:
    s, e = key
    return f"Season {s} / Episode {e}" if s is not None else f"Episode {e}"


def episode_path(series: str, key: tuple[int | None, int]) -> str:
    """yt-dlp output template (relative) for one episode."""
    s, e = key
    base = series.replace("%", "%%")
    return f"{base}/Season {s}/Episode {e:02d}.%(ext)s" if s is not None else f"{base}/Episode {e:02d}.%(ext)s"


_KIND_RANK = {"stream": 0, "direct": 1, "embed": 2, "platform-link": 3}


def group_episodes(cands: list[VideoCandidate]) -> tuple[dict[tuple[int | None, int], list[VideoCandidate]],
                                                         list[VideoCandidate]]:
    """Split into {episode: [candidates, best first]} and the remaining (non-episode) candidates.

    When episodes were captured from a player, unlabelled streams of that same player are dropped:
    they are the episode that was already playing before the menus were walked."""
    eps: dict[tuple[int | None, int], list[VideoCandidate]] = {}
    rest: list[VideoCandidate] = []
    for c in cands:
        k = episode_key(c)
        if k is None:
            rest.append(c)
        else:
            eps.setdefault(k, []).append(c)
    if eps:
        # only for players embedded in an iframe (referer differs from the page itself)
        player_refs = {c.referer for group in eps.values() for c in group
                       if c.source == "network" and c.label and c.referer and c.referer != c.page}
        rest = [c for c in rest if not (c.source == "network" and c.referer in player_refs)]
    for k in eps:
        eps[k].sort(key=lambda c: (c.protected, 0 if c.label else 1, _KIND_RANK.get(c.kind, 9)))
    return dict(sorted(eps.items(), key=lambda kv: ((kv[0][0] or 0), kv[0][1]))), rest
