"""End-to-end pipeline: discover -> scan site -> download -> scan media -> assess -> report."""
from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from . import __version__
from .av import engines_status, virustotal_hash, virustotal_url
from .discovery import BrowserDiscoverer, dedupe, fetch_static, now_stamp, write_cookie_jar
from .downloader import Downloader
from .media_scan import MEDIA_EXTS, scan_media
from .models import PageResult, VideoCandidate, sev_rank
from .piracy import assess_site, assess_video
from .prompts import FormatChoice
from .site_scan import scan_site

KIND_ORDER = {"platform-link": 0, "embed": 1, "stream": 2, "direct": 3}


@dataclass
class Options:
    out: Path = Path("output")
    browser: bool = True
    download: bool = True
    max_pages: int = 25
    depth: int = 1
    max_combinations: int = 300
    timeout: int = 30
    cookies: str | None = None
    cookies_from_browser: str | None = None
    choice: FormatChoice | None = None
    deep: bool = False
    virustotal: bool = False
    headful: bool = False
    subtitles: bool = False
    av: bool = True
    max_scripts: int = 60
    max_playlist: int = 50


def _save_evidence(pages: list[PageResult], evidence: Path) -> None:
    for i, pg in enumerate(pages, 1):
        d = evidence / f"page_{i:02d}"
        d.mkdir(parents=True, exist_ok=True)
        (d / "url.txt").write_text(f"{pg.url}\nfinal: {pg.final_url}\n")
        if pg.raw_html:
            (d / "raw.html.txt").write_text(pg.raw_html)
        if pg.rendered_html:
            (d / "rendered.html.txt").write_text(pg.rendered_html)
        (d / "network.json").write_text(json.dumps(pg.network, indent=1))
        (d / "headers.json").write_text(json.dumps(pg.headers, indent=1))
        for j, fr in enumerate(pg.frames, 1):
            (d / f"frame_{j:02d}.html.txt").write_text(f"<!-- {fr['url']} -->\n{fr['html']}")


def discover(url: str, opts: Options, evidence: Path, log: Callable[[str], None],
             cancel=None) -> tuple[list[PageResult], str | None]:
    pages: list[PageResult] = []
    queue: list[tuple[str, int]] = [(url, 0)]
    visited: set[str] = set()
    cookie_jar = None

    def crawl(analyze: Callable[[str, bool], PageResult]) -> None:
        while queue and len(pages) < opts.max_pages and not (cancel is not None and cancel.is_set()):
            u, depth = queue.pop(0)
            if u in visited:
                continue
            visited.add(u)
            log(f"[discover] {u}")
            pg = analyze(u, True)
            pages.append(pg)
            log(f"           {len(pg.candidates)} candidate(s), {len(pg.selections)} selection(s), "
                f"{len(pg.crawl_links)} episode link(s)")
            if depth < opts.depth:
                queue.extend((link, depth + 1) for link in pg.crawl_links if link not in visited)

    if opts.browser:
        try:
            with BrowserDiscoverer(evidence, opts.timeout, opts.headful, max_combinations=opts.max_combinations,
                                   log=log) as bd:
                crawl(lambda u, e: bd.analyze(u, e))
                from .tools import session_temp

                # session cookies are only needed by the downloader: keep them in the session temp folder
                cookie_jar = str(write_cookie_jar(bd.cookies(), session_temp() / f"cookies-{now_stamp()}.txt"))
        except Exception as exc:  # noqa: BLE001
            log(f"[discover] browser unavailable ({exc}); falling back to static mode")
            if not pages:
                crawl(lambda u, e: fetch_static(u, opts.timeout))
    else:
        crawl(lambda u, e: fetch_static(u, opts.timeout))
    return pages, cookie_jar


def run(url: str, opts: Options, log: Callable[[str], None] = print, cancel=None,
        on_progress: Callable[[dict], None] | None = None,
        on_stage: Callable[[str, float], None] | None = None) -> tuple[dict[str, Any], Path]:
    stage = on_stage or (lambda name, frac: None)
    cancelled = lambda: cancel is not None and cancel.is_set()  # noqa: E731
    host = (urlparse(url).hostname or "site").replace(":", "_")
    run_dir = opts.out / f"{host}_{now_stamp()}"
    evidence = run_dir / "evidence"
    qdir = run_dir / "videos"
    evidence.mkdir(parents=True, exist_ok=True)

    stage("Discovering videos", 0.05)
    pages, cookie_jar = discover(url, opts, evidence, log, cancel)
    _save_evidence(pages, evidence)
    candidates = dedupe([c for pg in pages for c in pg.candidates])
    crawled = {pg.url for pg in pages} | {pg.final_url for pg in pages}
    candidates = [c for c in candidates if c.url not in crawled]
    candidates.sort(key=lambda c: KIND_ORDER.get(c.kind, 9))
    log(f"[discover] {len(candidates)} unique video candidate(s) across {len(pages)} page(s)")

    stage("Scanning site security", 0.35)
    log("[site-scan] analysing site security")
    site = scan_site(pages, evidence, timeout=opts.timeout, max_scripts=opts.max_scripts, use_av=opts.av)
    if opts.virustotal:
        try:
            site["virustotal"] = virustotal_url(pages[0].final_url or url)
        except Exception as exc:  # noqa: BLE001
            site["virustotal"] = {"error": str(exc)}

    downloads: list[dict[str, Any]] = []
    if opts.download and candidates:
        stage("Downloading videos", 0.45)
        dl = Downloader(qdir, cookiefile=opts.cookies or cookie_jar, cookies_from_browser=opts.cookies_from_browser,
                        choice=opts.choice, max_playlist=opts.max_playlist, subtitles=opts.subtitles, log=log,
                        on_progress=on_progress, cancel=cancel, write_info=False)
        covered: set[str] = set()
        for i, cand in enumerate(candidates, 1):
            if cancelled():
                log("[download] cancelled")
                break
            stage(f"Downloading {i}/{len(candidates)}", 0.45 + 0.4 * (i - 1) / len(candidates))
            if cand.source == "network" and cand.referer in covered:
                downloads.append({"candidate": cand.to_dict(), "status": "skipped", "files": [], "info": {},
                                  "error": "stream belongs to an embed that was already downloaded"})
                continue
            log(f"[download {i}/{len(candidates)}] {cand.kind}: {cand.url[:110]}" + (f"  [{cand.label}]" if cand.label else ""))
            r = dl.download(cand)
            log(f"           -> {r.status}" + (f" ({len(r.files)} file(s))" if r.files else "") +
                (f": {r.error[:150]}" if r.error and r.status != "downloaded" else ""))
            if r.status in ("downloaded", "duplicate"):
                covered.add(cand.url)
            downloads.append(r.to_dict())

    # media analysis -----------------------------------------------------------
    stage("Scanning downloaded media", 0.88)
    seen_hashes: dict[str, str] = {}
    for d in downloads:
        d["media"] = []
        for f in d["files"]:
            p = Path(f)
            if p.suffix.lower() not in MEDIA_EXTS:
                continue
            log(f"[media-scan] {p.name}")
            m = scan_media(p, deep=opts.deep, use_av=opts.av)
            sha = m["hashes"]["sha256"]
            if sha in seen_hashes:
                m["duplicate_of"] = seen_hashes[sha]
            seen_hashes.setdefault(sha, str(p))
            if opts.virustotal:
                try:
                    m["virustotal"] = virustotal_hash(sha)
                except Exception as exc:  # noqa: BLE001
                    m["virustotal"] = {"error": str(exc)}
            d["media"].append(m)

    site_piracy = assess_site(pages, candidates, site)
    for d in downloads:
        if d["status"] == "downloaded":
            d["piracy"] = assess_video(d, d["media"], site_piracy["score"])

    all_findings = site["findings"] + [f for d in downloads for m in d.get("media", []) for f in m["findings"]]
    worst = max((sev_rank(f["severity"]) for f in all_findings), default=0)
    report = {
        "tool": "WEB-Treasure-Stalker", "version": __version__, "target": url, "run_dir": str(run_dir),
        "engines": {**engines_status(), "ffprobe": shutil.which("ffprobe") or "not installed"},
        "summary": {
            "pages_analyzed": len(pages), "candidates": len(candidates),
            "downloaded_files": sum(len(d["files"]) for d in downloads),
            "drm_protected": sum(d["status"] == "drm" for d in downloads),
            "piracy_verdict": site_piracy["verdict"], "piracy_score": site_piracy["score"],
            "highest_severity": ["info", "low", "medium", "high", "critical"][worst],
            "security_findings": len(all_findings),
        },
        "piracy": site_piracy, "site": site,
        "pages": [{"url": p.url, "final_url": p.final_url, "status": p.status, "error": p.error,
                   "selections": p.selections, "popups": p.popups, "screenshot": p.screenshot} for p in pages],
        "candidates": [c.to_dict() for c in candidates], "downloads": downloads,
    }
    from .report import write_reports

    write_reports(report, run_dir)
    stage("Done", 1.0)
    return report, run_dir
