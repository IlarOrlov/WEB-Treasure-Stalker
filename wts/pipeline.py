"""End-to-end pipeline: discover -> scan site -> download -> scan media -> assess -> report."""
from __future__ import annotations

import json
import shutil
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from . import __version__
from .av import engines_status, virustotal_hash, virustotal_url
from .discovery import BrowserDiscoverer, dedupe, fetch_static, now_stamp, quality_variants, write_cookie_jar
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
    parallel: int = 3  # catalogue items downloaded at the same time
    # called with the Analysis; returns the catalogue item ids to download (None = nothing)
    choose_items: Any = None


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
             cancel=None, stage: Callable[[str, float], None] | None = None
             ) -> tuple[list[PageResult], str | None, list[str]]:
    """Visit the page (and related season pages). Numbered episode pages are only listed ("deferred"):
    they are opened at download time, except the first one, which is analysed as a sample."""
    from concurrent.futures import ThreadPoolExecutor

    from .discovery import fetch_many_static, related_links, series_tokens, sitemap_urls
    from .episodes import episode_key

    deferred: list[str] = []
    hubs: list[str] = []  # same-site season/category pages: read with plain HTTP (fast, parallel)
    sample_done = False
    stage = stage or (lambda name, frac: None)
    state = {"page": 0}
    is_ep = lambda link: episode_key(VideoCandidate(link, "embed", "episode-page", page=link)) is not None  # noqa: E731
    # the sitemap is read in the background while the browser works on the start page
    sm_pool = ThreadPoolExecutor(max_workers=1)
    sm_future = sm_pool.submit(sitemap_urls, url, min(opts.timeout, 20), cancel=cancel)

    def on_event(msg: str) -> None:
        total = max(1, min(opts.max_pages, state["page"] + len(queue)))
        frac = 0.05 + 0.28 * min(1.0, (state["page"] - 1) / total)
        stage(f"Page {state['page']}/{total}: {msg}", frac)

    pages: list[PageResult] = []
    queue: list[tuple[str, int]] = [(url, 0)]
    visited: set[str] = set()
    cookie_jar = None

    def crawl(analyze: Callable[[str, bool], PageResult]) -> None:
        nonlocal sample_done
        while queue and len(pages) < opts.max_pages and not (cancel is not None and cancel.is_set()):
            u, depth = queue.pop(0)
            if u in visited:
                continue
            visited.add(u)
            state["page"] += 1
            log(f"[discover] {u}")
            # walk player menus on the start page; on crawled pages only if no episode pages are listed
            # (then those pages already cover every episode and walking would just repeat work)
            pg = analyze(u, depth == 0 or (u not in deferred and not deferred))
            pages.append(pg)
            log(f"           {len(pg.candidates)} candidate(s), {len(pg.selections)} selection(s), "
                f"{len(pg.crawl_links)} episode link(s)")
            if depth < opts.depth:
                links = [x for x in pg.crawl_links if x not in visited and x not in deferred]
                for link in [x for x in links if is_ep(x)]:
                    deferred.append(link)  # opened only if the user downloads this episode
                    if not sample_done:  # analyse one episode page as a sample (players, security)
                        sample_done = True
                        queue.append((link, depth + 1))
                for link in [x for x in links if not is_ep(x)]:
                    if deferred:  # a season list page: its episode links are all that is needed from it
                        if link not in hubs:
                            hubs.append(link)
                    else:
                        queue.append((link, depth + 1))

    def expand_hubs() -> None:
        """Read season/category pages over plain HTTP, in parallel, and follow them two levels deep."""
        done: set[str] = set(visited)
        level, budget = 0, 40
        todo = [h for h in hubs if h not in done]
        while todo and level < 3 and budget > 0 and not (cancel is not None and cancel.is_set()):
            todo = todo[:budget]
            budget -= len(todo)
            done.update(todo)
            stage(f"Reading {len(todo)} season page(s)", 0.33)
            log(f"[discover] reading {len(todo)} season/category page(s) in parallel")
            tokens = series_tokens(deferred)
            nxt: list[str] = []
            for pg in fetch_many_static(todo, min(opts.timeout, 20), cancel=cancel):
                if pg.error or not pg.raw_html:
                    queue.append((pg.url, opts.depth))  # needs a real browser (scripts / protection)
                    continue
                pages.append(pg)
                new = 0
                for link in related_links(pg.raw_html, pg.final_url or pg.url, url, tokens):
                    if is_ep(link):
                        if link not in deferred:
                            deferred.append(link)
                            new += 1
                    elif link not in done and link not in nxt:
                        nxt.append(link)
                log(f"           {pg.url}: {new} new episode page(s)")
            todo, level = nxt, level + 1

    def merge_sitemap() -> None:
        """Episode pages of this title listed in the site's sitemap but not linked from the pages read
        (e.g. a season only reachable through another section)."""
        try:
            listed = sm_future.result(timeout=30)
        except Exception:  # noqa: BLE001  (timeout / network): the sitemap is only a supplement
            return
        if not deferred or not listed:
            return
        tokens = series_tokens(deferred)
        from .discovery import _slug_tokens, related_episode_link

        known = {episode_key(VideoCandidate(x, "embed", "episode-page", page=x)) for x in deferred}
        added = 0
        for link in listed:
            if not is_ep(link) or link in deferred or len(deferred) >= 3000:
                continue
            if not (related_episode_link(url, link) and (not tokens or tokens & _slug_tokens(link))):
                continue
            k = episode_key(VideoCandidate(link, "embed", "episode-page", page=link))
            if k in known:
                continue
            known.add(k)
            deferred.append(link)
            added += 1
        if added:
            log(f"[discover] sitemap: {added} more episode page(s) of this title")

    def finish_listing(analyze) -> None:
        expand_hubs()
        if queue:
            crawl(analyze)
        merge_sitemap()

    if opts.browser:
        try:
            with BrowserDiscoverer(evidence, opts.timeout, opts.headful, max_combinations=opts.max_combinations,
                                   log=log, on_event=on_event, cancel=cancel) as bd:
                crawl(lambda u, e: bd.analyze(u, e))
                finish_listing(lambda u, e: bd.analyze(u, e))
                from .tools import session_temp

                # session cookies are only needed by the downloader: keep them in the session temp folder
                cookie_jar = str(write_cookie_jar(bd.cookies(), session_temp() / f"cookies-{now_stamp()}.txt"))
        except Exception as exc:  # noqa: BLE001
            log(f"[discover] browser unavailable ({exc}); falling back to static mode")
            if not pages:
                crawl(lambda u, e: fetch_static(u, opts.timeout))
                finish_listing(lambda u, e: fetch_static(u, opts.timeout))
    else:
        crawl(lambda u, e: fetch_static(u, opts.timeout))
        finish_listing(lambda u, e: fetch_static(u, opts.timeout))
    sm_pool.shutdown(wait=False, cancel_futures=True)
    return pages, cookie_jar, deferred


@dataclass
class Analysis:
    url: str
    opts: Options
    host: str
    pages: list[PageResult]
    candidates: list[VideoCandidate]
    site: dict[str, Any]
    piracy: dict[str, Any]
    items: list[Any]  # catalog.MediaItem
    series: str
    workdir: Path  # evidence collected during analysis (session temp until saved)
    cookie_jar: str | None
    report: dict[str, Any]


def analyse(url: str, opts: Options, log: Callable[[str], None] = print, cancel=None,
            on_stage: Callable[[str, float], None] | None = None) -> Analysis:
    """Discover all media and scan the site. Downloads nothing; evidence stays in the session temp folder."""
    from .catalog import build_catalog
    from .platforms import ytdlp_extractor
    from .tools import session_temp

    stage = on_stage or (lambda name, frac: None)
    host = (urlparse(url).hostname or "site").replace(":", "_")
    workdir = session_temp() / f"analysis-{now_stamp()}-{abs(hash(url)) % 10_000}"
    evidence = workdir / "evidence"
    evidence.mkdir(parents=True, exist_ok=True)

    stage("Discovering media", 0.05)
    pages, cookie_jar, deferred = discover(url, opts, evidence, log, cancel, stage)
    _save_evidence(pages, evidence)
    candidates = dedupe([c for pg in pages for c in pg.candidates])
    crawled = {pg.url for pg in pages} | {pg.final_url for pg in pages}
    candidates = [c for c in candidates if c.url not in crawled]
    candidates.sort(key=lambda c: KIND_ORDER.get(c.kind, 9))
    log(f"[discover] {len(candidates)} media candidate(s) across {len(pages)} page(s); "
        f"{len(deferred)} more episode page(s) listed")

    stage("Scanning site security", 0.75)
    log("[site-scan] analysing site security")
    site = scan_site(pages, evidence, timeout=opts.timeout, max_scripts=opts.max_scripts, use_av=opts.av)
    if opts.virustotal:
        try:
            site["virustotal"] = virustotal_url(pages[0].final_url or url)
        except Exception as exc:  # noqa: BLE001
            site["virustotal"] = {"error": str(exc)}

    items, series = build_catalog(pages, candidates, deferred, host, ytdlp_supported=ytdlp_extractor)
    eps = [i for i in items if i.kind == "episode"]
    if eps:
        log(f"[catalog] series '{series}': {len(eps)} episode(s) in {len({i.season for i in eps})} season(s)")
    for i in items:
        if i.kind != "episode":
            log(f"[catalog] {i.kind}: {i.title} ({len(i.sources)} source(s))")
    site_piracy = assess_site(pages, candidates + [c for i in items for c in ([] if i.sources else
                              [VideoCandidate(i.page, "embed", "episode-page", page=i.page)])], site)
    a = Analysis(url, opts, host, pages, candidates, site, site_piracy, items, series, workdir, cookie_jar, {})
    a.report = make_report(a, [], None)
    stage("Analysis complete", 1.0)
    return a


def make_report(a: Analysis, downloads: list[dict[str, Any]], run_dir: Path | None) -> dict[str, Any]:
    all_findings = a.site["findings"] + [f for d in downloads for m in d.get("media", []) for f in m["findings"]]
    worst = max((sev_rank(f["severity"]) for f in all_findings), default=0)
    eps = [i for i in a.items if i.kind == "episode"]
    series = None
    if eps:
        seasons: dict[str, int] = {}
        for i in eps:
            seasons[f"Season {i.season}" if i.season is not None else "Episodes"] = \
                seasons.get(f"Season {i.season}" if i.season is not None else "Episodes", 0) + 1
        chosen = [i for i in eps if i.status]
        series = {"title": a.series, "episodes_found": len(eps), "episodes_selected": len(chosen),
                  "episodes_downloaded": sum(i.status == "downloaded" for i in eps),
                  "missing": [i.label for i in chosen if i.status != "downloaded"], "seasons": seasons}
    return {
        "tool": "WEB-Treasure-Stalker", "version": __version__, "target": a.url,
        "run_dir": str(run_dir) if run_dir else "",
        "engines": {**engines_status(), "ffprobe": shutil.which("ffprobe") or "not installed"},
        "summary": {
            "pages_analyzed": len(a.pages), "candidates": len(a.candidates), "media_items": len(a.items),
            "downloaded_files": sum(len(d["files"]) for d in downloads),
            "drm_protected": sum(d["status"] == "drm" for d in downloads),
            "piracy_verdict": a.piracy["verdict"], "piracy_score": a.piracy["score"],
            "highest_severity": ["info", "low", "medium", "high", "critical"][worst],
            "security_findings": len(all_findings),
        },
        "series": series,
        "catalog": [i.to_dict() for i in a.items],
        "piracy": a.piracy, "site": a.site,
        "pages": [{"url": p.url, "final_url": p.final_url, "status": p.status, "error": p.error,
                   "selections": p.selections, "popups": p.popups, "screenshot": p.screenshot} for p in a.pages],
        "candidates": [c.to_dict() for c in a.candidates], "downloads": downloads,
    }


def _resolve_page(bd: BrowserDiscoverer | None, page: str, log: Callable[[str], None]) -> list[VideoCandidate]:
    """Open an episode page that was only listed during analysis and capture the stream its player loads."""
    if bd is None:
        return [VideoCandidate(page, "embed", "episode-page", referer=page, page=page)]
    bd.page_budget = 110
    res = bd.analyze(page, enumerate_episodes=False, quick=True)
    found = [c for c in res.candidates if c.kind in ("stream", "direct") and c.source in ("network", "inline-script",
                                                                                          "video-tag", "video-source")]
    # links read from the config of a player whose own streams turned out protected are protected too
    host = lambda c: urlparse(c.referer).hostname  # noqa: E731
    prot = {host(c) for c in found if c.source == "network" and c.protected}
    good = {host(c) for c in found if c.source == "network" and not c.protected}
    for c in found:
        if c.source != "network" and host(c) in prot - good:
            c.protected = True
    found.sort(key=lambda c: (c.protected, 0 if c.source == "network" else 1, 0 if c.kind == "stream" else 1))
    # one source per distinct stream path (mirrors on other hosts stay as fallbacks, duplicates are dropped)
    uniq: dict[str, VideoCandidate] = {}
    for c in found:
        uniq.setdefault(c.url, c)
    found = list(uniq.values())[:6]
    if not found:
        log(f"           no stream found on {page}")
    return found or [VideoCandidate(page, "embed", "episode-page", referer=page, page=page)]


class _ProgressMux:
    """Merges the progress of downloads running in parallel into one stream of events for the UI."""

    def __init__(self, cb: Callable[[dict], None] | None):
        self.cb = cb
        self.lock = threading.Lock()
        self.active: dict[str, dict] = {}

    def channel(self, key: str, label: str) -> Callable[[dict], None] | None:
        if self.cb is None:
            return None

        def update(d: dict) -> None:
            with self.lock:
                if d.get("status") in ("downloading", "finished"):
                    if d.get("status") == "finished":
                        d = {**d, "downloaded": d.get("total") or d.get("downloaded") or 0, "speed": 0, "eta": 0}
                    self.active[key] = {**d, "label": label}
                act = list(self.active.values())
            if len(act) <= 1:
                self.cb({**d, "title": label or d.get("title", "")})
                return
            totals = [x.get("total") or 0 for x in act]
            self.cb({"status": "downloading", "title": f"{len(act)} downloads in parallel: "
                     + ", ".join(x["label"] for x in act)[:120],
                     "downloaded": sum(x.get("downloaded") or 0 for x in act),
                     "total": sum(totals) if all(totals) else 0,
                     "speed": sum(x.get("speed") or 0 for x in act),
                     "eta": max((x.get("eta") or 0 for x in act), default=None)})
        return update

    def close(self, key: str) -> None:
        with self.lock:
            self.active.pop(key, None)


def _uniq_urls(cands: list[VideoCandidate]) -> list[VideoCandidate]:
    seen: set[str] = set()
    return [c for c in cands if not (c.url in seen or seen.add(c.url))]


def _remove_empty_dirs(root: Path) -> None:
    if not root.exists():
        return
    for p in sorted((p for p in root.rglob("*") if p.is_dir()), key=lambda x: len(x.parts), reverse=True):
        try:
            p.rmdir()  # only succeeds when empty
        except OSError:
            pass


def _download_chosen(a: Analysis, chosen: list, qdir: Path, run_dir: Path, choice: Any, log: Callable[[str], None],
                     cancel, on_progress, stage) -> list[dict[str, Any]]:
    """Episode pages are opened one after another in a lean browser (this thread owns it), while up to
    opts.parallel items download at the same time. Pages are resolved at most one item ahead of the free
    download slots, so captured stream links are fresh when their download starts."""
    from concurrent.futures import ThreadPoolExecutor

    opts = a.opts
    cancelled = lambda: cancel is not None and cancel.is_set()  # noqa: E731
    workers = max(1, min(8, int(opts.parallel or 1)))
    mux = _ProgressMux(on_progress)
    lock = threading.Lock()
    results: dict[int, list[dict[str, Any]]] = {}
    finished = [0]
    slots = threading.Semaphore(workers + 1)
    total = len(chosen)

    def worker(n: int, it, sources: list[VideoCandidate]) -> None:
        out: list[dict[str, Any]] = []
        try:
            dl = Downloader(qdir, cookiefile=opts.cookies or a.cookie_jar,
                            cookies_from_browser=opts.cookies_from_browser, choice=choice or opts.choice,
                            max_playlist=opts.max_playlist, subtitles=opts.subtitles, log=log,
                            on_progress=mux.channel(it.id, it.label), cancel=cancel, write_info=False,
                            use_archive=False, fragments=max(4, 16 // workers))
            it.status = "error"
            max_h = getattr(choice or opts.choice, "max_height", None)
            usable = [v for c in sources if not c.protected for v in quality_variants(c, max_h)]
            for cand in _uniq_urls(usable)[:10]:  # best source (and quality) first, the rest as fallback
                if cancelled():
                    break
                cand.label = it.label
                log(f"[download {n}/{total}] {it.label}: {cand.url[:100]}")
                r = dl.download(cand, outtmpl=it.outtmpl or None)
                log(f"[download {n}/{total}] {it.label} -> {r.status}"
                    + (f": {r.error[:150]}" if r.error and r.status != "downloaded" else ""))
                d = r.to_dict()
                d["item"] = it.id
                if it.kind == "episode":
                    d["episode"] = it.label
                out.append(d)
                if r.status == "downloaded":
                    it.status, it.files = "downloaded", r.files
                    break
                it.status = r.status
            if it.status != "downloaded" and any(c.protected for c in sources):
                it.status = "protected"  # the working player is token-protected, the alternatives failed
        except Exception as exc:  # noqa: BLE001
            it.status = "error"
            out.append({"candidate": sources[0].to_dict() if sources else {}, "status": "error", "files": [],
                        "info": {}, "item": it.id, "error": f"{type(exc).__name__}: {exc}"[:500]})
        finally:
            mux.close(it.id)
            with lock:
                results[n] = out
                finished[0] += 1
                stage(f"Downloaded {finished[0]} of {total}", 0.02 + 0.85 * finished[0] / total)
            slots.release()

    need_browser = any(not i.sources and i.page for i in chosen) and opts.browser
    bd_cm = BrowserDiscoverer(run_dir / "evidence", opts.timeout, opts.headful, log=lambda m: None, cancel=cancel,
                              lean=True) if need_browser else None
    bd = None
    pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="wts-dl")
    try:
        if bd_cm is not None:
            try:
                bd = bd_cm.__enter__()
            except Exception as exc:  # noqa: BLE001
                log(f"[download] browser unavailable ({exc}); trying episode pages directly")
                bd_cm = None
        for n, it in enumerate(chosen, 1):
            while not slots.acquire(timeout=0.25):
                if cancelled():
                    break
            if cancelled():
                log("[download] cancelled")
                break
            if not it.sources and it.page:
                stage(f"Opening {it.label} ({n}/{total})", 0.02 + 0.85 * finished[0] / total)
            sources = sorted(it.sources or _resolve_page(bd, it.page, log), key=lambda c: c.protected)
            if sources and all(c.protected for c in sources):
                it.status = "protected"
                log(f"[download {n}/{total}] {it.label}: skipped - every player protects this stream (one-time "
                    "access tokens or encrypted playlists); it cannot be downloaded outside the player")
                with lock:
                    results[n] = [{"candidate": sources[0].to_dict(), "status": "protected", "files": [], "info": {},
                                   "item": it.id, "error": "stream protected (one-time tokens or encrypted playlist)",
                                   **({"episode": it.label} if it.kind == "episode" else {})}]
                    finished[0] += 1
                slots.release()
                continue
            pool.submit(worker, n, it, sources)
    finally:
        pool.shutdown(wait=True)
        if bd_cm is not None:
            bd_cm.__exit__(None, None, None)
    return [d for n in sorted(results) for d in results[n]]


def download_items(a: Analysis, ids: list[str] | None, folder: Path, choice: Any = None,
                   log: Callable[[str], None] = print, cancel=None,
                   on_progress: Callable[[dict], None] | None = None,
                   on_stage: Callable[[str, float], None] | None = None) -> tuple[dict[str, Any], Path]:
    """Download the chosen catalogue items into <folder>/<site>_<time>/, scan them, write the full report.

    ids=None downloads everything; an empty list saves only the report and evidence."""
    stage = on_stage or (lambda name, frac: None)
    cancelled = lambda: cancel is not None and cancel.is_set()  # noqa: E731
    opts = a.opts
    run_dir = Path(folder) / f"{a.host}_{now_stamp()}"
    run_dir.mkdir(parents=True, exist_ok=True)
    shutil.copytree(a.workdir / "evidence", run_dir / "evidence", dirs_exist_ok=True)
    qdir = run_dir / "videos"
    chosen = [i for i in a.items if ids is None or i.id in ids]
    for i in a.items:
        i.status, i.files = "", []
    downloads: list[dict[str, Any]] = []
    if chosen:
        downloads = _download_chosen(a, chosen, qdir, run_dir, choice, log, cancel, on_progress, stage)
        _remove_empty_dirs(qdir)

    stage("Scanning downloaded media", 0.9)
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
    for d in downloads:
        if d["status"] == "downloaded":
            d["piracy"] = assess_video(d, d["media"], a.piracy["score"])
    report = make_report(a, downloads, run_dir)
    from .report import write_reports

    write_reports(report, run_dir)
    stage("Done", 1.0)
    return report, run_dir


def run(url: str, opts: Options, log: Callable[[str], None] = print, cancel=None,
        on_progress: Callable[[dict], None] | None = None,
        on_stage: Callable[[str, float], None] | None = None) -> tuple[dict[str, Any], Path]:
    """analyse -> choose (opts.choose_items, default: everything) -> download into opts.out."""
    a = analyse(url, opts, log, cancel, on_stage)
    ids: list[str] | None = None
    if not opts.download:
        ids = []
    elif opts.choose_items is not None:
        ids = opts.choose_items(a)
        if ids is None:
            ids = []
    return download_items(a, ids, opts.out, opts.choice, log, cancel, on_progress, on_stage)
