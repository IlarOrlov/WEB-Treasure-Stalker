"""Heuristic piracy assessment.

Produces an explainable score (0-100). This is an analyst aid, not a legal
determination: always review the listed reasons.
"""
from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from .discovery import site_key
from .models import PageResult, VideoCandidate
from .signatures import (CAM_RE, OFFICIAL_PLATFORMS, PIRACY_PAGE_PHRASES, PIRATE_DOMAIN_RE, PIRATE_HOST_RE,
                         RELEASE_NAME_RE)


def _is_official(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return any(host == d or host.endswith("." + d) for d in OFFICIAL_PLATFORMS)


def verdict(score: int) -> str:
    if score >= 60:
        return "LIKELY PIRATED"
    if score >= 30:
        return "SUSPICIOUS"
    return "NO STRONG INDICATORS"


def assess_site(pages: list[PageResult], candidates: list[VideoCandidate], site_report: dict[str, Any]) -> dict[str, Any]:
    reasons: list[tuple[int, str]] = []
    main = pages[0]
    url = main.final_url or main.url
    host = urlparse(url).hostname or ""

    if _is_official(url):
        reasons.append((-40, f"{host} is an official/licensed platform"))
    if PIRATE_DOMAIN_RE.search(host):
        reasons.append((30, f"domain name matches piracy-portal pattern ({host})"))

    text = ""
    title = ""
    for pg in pages:
        soup = BeautifulSoup(pg.rendered_html or pg.raw_html or "", "html.parser")
        if not title and soup.title:
            title = soup.title.get_text(strip=True)
        text += " " + soup.get_text(" ", strip=True)[:500_000]
    for rx, weight, why in PIRACY_PAGE_PHRASES:
        m = rx.search(text)
        if m:
            reasons.append((weight, f"{why}: \"{m.group(0)[:60]}\""))
    if RELEASE_NAME_RE.search(title):
        reasons.append((15, f"page title uses scene/P2P release naming: \"{title[:80]}\""))

    hosts = sorted({urlparse(c.url).hostname or "" for c in candidates if PIRATE_HOST_RE.search(urlparse(c.url).hostname or "")})
    if hosts:
        reasons.append((min(25 + 5 * (len(hosts) - 1), 40), "embeds known piracy video hosts: " + ", ".join(hosts[:8])))

    if site_report.get("aggressive_ad_networks"):
        reasons.append((15, "monetised with pop-under/malvertising ad networks (typical of piracy portals)"))
    if site_report.get("popups"):
        reasons.append((5, "forces pop-ups/pop-unders"))

    from .episodes import episode_key

    eps = {k for k in (episode_key(c) for c in candidates) if k}
    labels = {c.label for c in candidates if c.label}
    if not _is_official(url):
        if len(eps) >= 10:
            reasons.append((35, f"free complete TV-series catalogue on an unofficial site ({len(eps)} episodes)"))
        elif len(eps) >= 3 or len(labels) >= 3:
            reasons.append((15, f"free season/episode catalogue on an unofficial site ({max(len(eps), len(labels))} episodes)"))
        own = [c for c in candidates if episode_key(c) and site_key(c.url) == site_key(url)]
        if len(eps) >= 3 and own:
            reasons.append((10, "episode files are served from the site's own servers"))
    if candidates and site_key(url) and all(site_key(c.url) != site_key(url) for c in candidates) and not _is_official(url):
        if any(c.kind in ("embed", "stream") for c in candidates):
            reasons.append((5, "all video is served from third-party hosts (site only aggregates embeds)"))

    score = max(0, min(100, sum(w for w, _ in reasons)))
    return {"score": score, "verdict": verdict(score), "title": title,
            "reasons": [f"{'+' if w >= 0 else ''}{w}: {r}" for w, r in reasons]}


def assess_video(dl: dict[str, Any], media: list[dict[str, Any]], site_score: int) -> dict[str, Any]:
    reasons: list[tuple[int, str]] = []
    cand = dl["candidate"]
    info = dl.get("info", {})
    src = info.get("webpage_url") or cand["url"]
    official = _is_official(src)
    if official:
        reasons.append((-30, f"served from official platform ({urlparse(src).hostname})"))
    if PIRATE_HOST_RE.search(urlparse(cand["url"]).hostname or ""):
        reasons.append((35, f"hosted on piracy video host ({urlparse(cand['url']).hostname})"))
    names = " ".join(filter(None, [info.get("title", ""), *[m["file"] for m in media]]))
    if RELEASE_NAME_RE.search(names):
        reasons.append((25, "scene/P2P release naming in title/filename"))
    if CAM_RE.search(info.get("title", "")):
        reasons.append((15, "title indicates a camcorder (CAM/TS) recording"))
    duration = info.get("duration") or next((float(m.get("format", {}).get("duration", 0) or 0) for m in media), 0)
    if duration and float(duration) > 40 * 60 and not official:
        reasons.append((10, f"feature/episode-length content ({int(float(duration) // 60)} min) from unofficial source"))
    if cand.get("label") and not official:
        reasons.append((10, f"selected from site episode/season menu ({cand['label']})"))
    for m in media:
        for k, v in (m.get("tags") or {}).items():
            vs = str(v)
            if PIRATE_DOMAIN_RE.search(vs) or RELEASE_NAME_RE.search(vs) or PIRATE_HOST_RE.search(vs):
                reasons.append((20, f"metadata tag '{k}' references piracy source: {vs[:80]}"))
                break
    inherited = round(site_score * 0.4)
    if inherited:
        reasons.append((inherited, f"inherited from site score ({site_score})"))
    score = max(0, min(100, sum(w for w, _ in reasons)))
    return {"score": score, "verdict": verdict(score),
            "reasons": [f"{'+' if w >= 0 else ''}{w}: {r}" for w, r in reasons]}
