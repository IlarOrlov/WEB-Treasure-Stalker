"""Shared data structures."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

SEVERITIES = ["info", "low", "medium", "high", "critical"]


def sev_rank(sev: str) -> int:
    return SEVERITIES.index(sev) if sev in SEVERITIES else 0


@dataclass
class Finding:
    severity: str  # info | low | medium | high | critical
    category: str
    message: str
    evidence: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class VideoCandidate:
    url: str
    kind: str  # embed | direct | stream | platform-link
    source: str  # how it was found (video-tag, iframe, network, og:video, ...)
    referer: str = ""
    label: str = ""  # e.g. "Season 2 / Episode 5"
    page: str = ""  # page on which it was discovered

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DownloadResult:
    candidate: VideoCandidate
    status: str  # downloaded | duplicate | drm | no-video | error | skipped
    files: list[str] = field(default_factory=list)
    info: dict[str, Any] = field(default_factory=dict)
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["candidate"] = self.candidate.to_dict()
        return d


@dataclass
class PageResult:
    url: str
    final_url: str = ""
    status: int | None = None
    headers: dict[str, str] = field(default_factory=dict)
    redirects: list[str] = field(default_factory=list)
    raw_html: str = ""
    rendered_html: str = ""
    frames: list[dict[str, str]] = field(default_factory=list)  # {url, html}
    network: list[dict[str, Any]] = field(default_factory=list)
    popups: list[str] = field(default_factory=list)
    downloads: list[dict[str, str]] = field(default_factory=list)
    dialogs: list[str] = field(default_factory=list)
    console_errors: list[str] = field(default_factory=list)
    candidates: list[VideoCandidate] = field(default_factory=list)
    crawl_links: list[str] = field(default_factory=list)
    selections: list[str] = field(default_factory=list)
    screenshot: str = ""
    error: str = ""
