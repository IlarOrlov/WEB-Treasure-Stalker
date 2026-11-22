"""Discover video candidates on a web page.

Two layers:
  * static extraction from HTML (works on raw or browser-rendered markup)
  * a headless Chromium session (Playwright) that renders the page, records all
    network traffic, presses play, and walks season/episode selectors and
    episode buttons so that per-episode players get loaded.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Callable
from urllib.parse import urldefrag, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from .models import PageResult, VideoCandidate
from .signatures import PIRATE_HOST_RE, PLATFORM_LINK_RE

DEFAULT_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

MEDIA_EXT = r"mp4|m4v|webm|mov|mkv|avi|flv|wmv|mpg|mpeg|3gp|ogv|m3u8|mpd|f4m"
MEDIA_URL_RE = re.compile(rf"\.({MEDIA_EXT})(\?|#|$)", re.I)
MANIFEST_RE = re.compile(r"\.(m3u8|mpd|f4m)(\?|#|$)", re.I)
SEGMENT_RE = re.compile(
    r"\.(ts|m4s|aac|m4a|vtt|srt|key|jpg|png)(\?|#|$)|/(seg|chunk|frag|fragment)[-_]?\d+|[?&](range|bytes)=|/init[-_.]|[-_.]init\.",
    re.I,
)
QUOTED_MEDIA_RE = re.compile(rf"""["']([^"'\s<>]+?\.(?:{MEDIA_EXT})(?:\?[^"'\s<>]*)?)["']""", re.I)
MEDIA_CONTENT_TYPES = (
    "video/", "application/vnd.apple.mpegurl", "application/x-mpegurl", "audio/mpegurl",
    "application/dash+xml", "application/f4m",
)
IGNORE_IFRAME_RE = re.compile(
    r"googlesyndication|doubleclick|google\.com/recaptcha|recaptcha|hcaptcha|disqus|"
    r"facebook\.com/plugins/(like|page|comments|share)|platform\.twitter\.com/widgets|accounts\.google|"
    r"googletagmanager|addthis|sharethis|challenges\.cloudflare|about:blank|^javascript:|^data:|"
    r"google\.com/maps|/ads?/|adserver|chat-widget|intercom|livechat|tawk\.to",
    re.I,
)
# Media served by platforms that yt-dlp handles better through the page/embed URL.
PLATFORM_CDN_RE = re.compile(r"googlevideo\.com|cdninstagram|fbcdn\.net|vimeocdn|ttwstatic|tiktokcdn|"
                             r"twimg\.com|dmcdn\.net|jtvnw\.net|ytimg\.com", re.I)
EPISODE_LINK_RE = re.compile(
    r"(season|episode|/series/|/tv/|/watch|/s\d{1,2}e\d{1,3}|[-_/]s\d{1,2}[-_]?e\d{1,3}|[-_/]ep(isode)?[-_]?\d+|"
    r"עונה|פרק|temporada|episodio|saison|[ée]pisode|staffel|folge|сезон|серия|موسم|حلقة)",
    re.I,
)
SELECT_KW = (r"(season|episode|series|serie|\bep\b|\bep\.|part|chapter|volume|temporada|episodio|saison|"
             r"épisode|staffel|folge|сезон|серия|עונה|פרק|موسم|حلقة)")
PLACEHOLDER_RE = re.compile(r"^\s*(select|choose|pick|--|—|all\b|בחר|elige|choisir|wählen|выбер)", re.I)


def _norm(url: str) -> str:
    return urldefrag(url.strip())[0]


def _http_url(url: str) -> bool:
    return urlparse(url).scheme in ("http", "https")


def site_key(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    parts = host.split(".")
    if len(parts) >= 3 and len(parts[-1]) == 2 and parts[-2] in ("co", "com", "org", "net", "ac", "gov"):
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def classify_url(url: str) -> str:
    if MANIFEST_RE.search(url):
        return "stream"
    if MEDIA_URL_RE.search(url):
        return "direct"
    if PLATFORM_LINK_RE.search(url):
        return "platform-link"
    return "embed"


def extract_from_html(html: str, base_url: str, label: str = "") -> tuple[list[VideoCandidate], list[str]]:
    """Return (video candidates, same-site episode/season links to crawl)."""
    soup = BeautifulSoup(html or "", "html.parser")
    cands: list[VideoCandidate] = []
    links: list[str] = []

    def add(url: str | None, source: str, kind: str | None = None) -> None:
        if not url:
            return
        url = url.strip()
        if url.startswith("//"):
            url = "https:" + url
        full = _norm(urljoin(base_url, url))
        if not _http_url(full):
            return
        cands.append(VideoCandidate(url=full, kind=kind or classify_url(full), source=source,
                                    referer=base_url, label=label, page=base_url))

    for v in soup.find_all(["video", "audio"]):
        for attr in ("src", "data-src", "data-video", "data-url"):
            add(v.get(attr), f"{v.name}-tag")
        for s in v.find_all("source"):
            add(s.get("src") or s.get("data-src"), f"{v.name}-source")
    for tag in soup.find_all(["iframe", "embed", "frame"]):
        src = tag.get("src") or tag.get("data-src") or tag.get("data-lazy-src")
        if src and not IGNORE_IFRAME_RE.search(src):
            add(src, tag.name)
    for obj in soup.find_all("object"):
        add(obj.get("data"), "object")
    for meta in soup.find_all("meta"):
        prop = (meta.get("property") or meta.get("name") or "").lower()
        if prop in ("og:video", "og:video:url", "og:video:secure_url", "twitter:player", "twitter:player:stream"):
            add(meta.get("content"), prop)
    for link in soup.find_all("link", rel=True):
        if "video_src" in link.get("rel", []):
            add(link.get("href"), "link-video_src")
    # Social embeds rendered by JS (Instagram, TikTok, Twitter blockquotes)
    for bq in soup.find_all("blockquote"):
        add(bq.get("data-instgrm-permalink"), "instagram-embed", "platform-link")
        if "tiktok-embed" in (bq.get("class") or []):
            add(bq.get("cite"), "tiktok-embed", "platform-link")
        if "twitter-tweet" in (bq.get("class") or []) or "twitter-video" in (bq.get("class") or []):
            for a in bq.find_all("a", href=True):
                if re.search(r"(twitter|x)\.com/[^/]+/status/", a["href"]):
                    add(a["href"], "twitter-embed", "platform-link")
    # JSON-LD VideoObject
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except (ValueError, TypeError):
            continue
        stack = [data]
        while stack:
            node = stack.pop()
            if isinstance(node, list):
                stack.extend(node)
            elif isinstance(node, dict):
                if "VideoObject" in str(node.get("@type", "")):
                    add(node.get("contentUrl"), "json-ld")
                    add(node.get("embedUrl"), "json-ld")
                stack.extend(v for v in node.values() if isinstance(v, (dict, list)))
    # Data attributes commonly used by lazy players / server switchers
    for el in soup.find_all(attrs={"data-video": True}):
        add(el.get("data-video"), "data-video")
    for el in soup.find_all(attrs={"data-embed": True}):
        add(el.get("data-embed"), "data-embed")
    # Anchors: media files, recognised platforms, pirate hosts, crawlable episode links
    base_site = site_key(base_url)
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith(("javascript:", "mailto:", "tel:", "#")):
            continue
        full = _norm(urljoin(base_url, href))
        if not _http_url(full):
            continue
        if MEDIA_URL_RE.search(full) or PLATFORM_LINK_RE.search(full) or PIRATE_HOST_RE.search(urlparse(full).netloc):
            add(full, "link")
        elif site_key(full) == base_site and (EPISODE_LINK_RE.search(full) or EPISODE_LINK_RE.search(a.get_text(" ", strip=True))):
            links.append(full)
    # Media URLs inside inline scripts / attributes (jwplayer `file:`, JSON configs, ...)
    text = (html or "").replace("\\/", "/")
    for m in QUOTED_MEDIA_RE.finditer(text):
        u = m.group(1)
        if not SEGMENT_RE.search(u):
            add(u, "inline-script")
    return dedupe(cands), list(dict.fromkeys(links))


def dedupe(cands: list[VideoCandidate]) -> list[VideoCandidate]:
    seen: dict[str, VideoCandidate] = {}
    for c in cands:
        key = _norm(c.url)
        if key not in seen:
            seen[key] = c
    return list(seen.values())


def is_media_response(url: str, resource_type: str, content_type: str) -> bool:
    if SEGMENT_RE.search(url) and not MANIFEST_RE.search(url):
        return False
    ct = (content_type or "").lower()
    if any(ct.startswith(t) for t in MEDIA_CONTENT_TYPES):
        return not ct.startswith("video/mp2t")
    return resource_type == "media" or bool(MANIFEST_RE.search(url)) or (
        bool(MEDIA_URL_RE.search(url)) and resource_type in ("xhr", "fetch", "other"))


# --------------------------------------------------------------------------- static


def fetch_static(url: str, timeout: int = 30, user_agent: str = DEFAULT_UA) -> PageResult:
    res = PageResult(url=url)
    try:
        r = requests.get(url, headers={"User-Agent": user_agent}, timeout=timeout, allow_redirects=True)
        res.status = r.status_code
        res.final_url = r.url
        res.headers = {k.lower(): v for k, v in r.headers.items()}
        res.redirects = [h.url for h in r.history]
        res.raw_html = r.text
        res.rendered_html = r.text
        res.candidates, res.crawl_links = extract_from_html(r.text, r.url)
    except requests.RequestException as exc:
        res.error = str(exc)
    return res


# --------------------------------------------------------------------------- browser

_RELEVANT_SELECTS_JS = r"""
(kw) => {
  const KW = new RegExp(kw, 'i');
  const out = [];
  document.querySelectorAll('select').forEach((s, i) => {
    let lab = '';
    if (s.id) { const l = document.querySelector(`label[for="${CSS.escape(s.id)}"]`); if (l) lab = l.innerText; }
    const meta = [s.name, s.id, s.className, s.getAttribute('aria-label') || '', lab,
                  s.getAttribute('data-type') || '', s.getAttribute('title') || ''].join(' ');
    const opts = Array.from(s.options).map(o => ({value: o.value, text: (o.text || '').trim(), disabled: o.disabled}));
    const optText = opts.map(o => o.text).join(' ');
    if (opts.length && (KW.test(meta) || KW.test(optText) || /\b[se]\d{1,3}\b/i.test(optText))) {
      const key = s.id ? '#' + CSS.escape(s.id) : (s.name ? `select[name="${s.name}"]` : `select >> nth=${i}`);
      const word = (meta.match(KW) || optText.match(KW) || [''])[0];
      out.push({key, word, options: opts});
    }
  });
  return out;
}
"""

_EPISODE_ITEMS_JS = r"""
(kind) => {
  const sel = kind === 'season'
    ? '[data-season],[data-season-id],.season-item,.seasons li,.seasons a,.seasons button,[class*="season"] li,[class*="season"] button'
    : '[data-episode],[data-ep],[data-episode-id],[data-epid],.episode,.episode-item,.ep-item,.episodes li,.episodes a,.episodes button,[class*="episode"] li,[class*="episode"] button,[class*="episode"] a';
  const els = Array.from(document.querySelectorAll(sel)).filter(e => {
    const r = e.getBoundingClientRect();
    if (r.width === 0 && r.height === 0) return false;
    // anchors with real navigation targets are crawled instead of clicked
    if (e.tagName === 'A') { const h = e.getAttribute('href') || ''; if (h && !h.startsWith('#') && !h.startsWith('javascript')) return false; }
    // skip containers of other matched items
    return !e.querySelector(sel);
  });
  els.forEach((e, i) => e.setAttribute('data-wts-' + kind, String(i)));
  return els.map(e => (e.innerText || e.getAttribute('title') || e.getAttribute('data-episode') || e.getAttribute('data-season') || '').trim().slice(0, 60));
}
"""

_PLAY_SELECTORS = [
    ".vjs-big-play-button", ".jw-icon-playback", ".jw-display-icon-container", ".plyr__control--overlaid",
    ".ytp-large-play-button", "button[aria-label*='play' i]", ".play-button", ".play-btn", "[class*='play-icon']",
    "#play", ".fp-play", ".mejs__overlay-play", "video",
]


class BrowserDiscoverer:
    """Headless Chromium session. Use as a context manager."""

    def __init__(self, evidence_dir: Path, timeout: int = 30, headful: bool = False,
                 user_agent: str = DEFAULT_UA, max_combinations: int = 300, max_clicks: int = 200,
                 log: Callable[[str], None] = print):
        self.evidence_dir = evidence_dir
        self.timeout_ms = timeout * 1000
        self.headful = headful
        self.user_agent = user_agent
        self.max_combinations = max_combinations
        self.max_clicks = max_clicks
        self.log = log
        self._pw = self._browser = self._ctx = None
        self._label = ""
        self._res: PageResult | None = None
        self._main_page = None
        self._popup_pages: list = []
        self._combos = 0
        self._page_no = 0

    # lifecycle -------------------------------------------------------------
    def __enter__(self) -> "BrowserDiscoverer":
        from playwright.sync_api import sync_playwright

        self._pw = sync_playwright().start()
        from .tools import launch_chromium

        try:
            self._browser = launch_chromium(
                self._pw, headless=not self.headful,
                args=["--autoplay-policy=no-user-gesture-required", "--mute-audio", "--disable-notifications"],
            )
        except Exception:
            self._pw.stop()
            raise
        self._ctx = self._browser.new_context(
            accept_downloads=False, user_agent=self.user_agent, ignore_https_errors=True,
            service_workers="block", viewport={"width": 1366, "height": 900},
        )
        self._ctx.on("request", self._on_request)
        self._ctx.on("response", self._on_response)
        self._ctx.on("page", self._on_page)
        return self

    def __exit__(self, *exc) -> None:
        for obj in (self._ctx, self._browser):
            try:
                obj and obj.close()
            except Exception:  # noqa: BLE001
                pass
        if self._pw:
            self._pw.stop()

    def cookies(self) -> list[dict]:
        return self._ctx.cookies() if self._ctx else []

    # event handlers ----------------------------------------------------------
    def _on_request(self, req) -> None:
        if not self._res:
            return
        try:
            frame_url = req.frame.url
        except Exception:  # noqa: BLE001
            frame_url = ""
        self._res.network.append({
            "url": req.url, "method": req.method, "type": req.resource_type, "frame": frame_url,
            "nav": req.is_navigation_request(), "referer": req.headers.get("referer", ""), "label": self._label,
        })

    def _on_response(self, resp) -> None:
        if not self._res:
            return
        try:
            headers = resp.headers
        except Exception:  # noqa: BLE001
            headers = {}
        ct = headers.get("content-type", "")
        for entry in reversed(self._res.network[-400:]):
            if entry["url"] == resp.url and "status" not in entry:
                entry["status"] = resp.status
                entry["content_type"] = ct
                entry["length"] = headers.get("content-length", "")
                if is_media_response(resp.url, entry["type"], ct) and not PLATFORM_CDN_RE.search(resp.url):
                    referer = entry.get("referer") or entry.get("frame") or self._res.url
                    self._res.candidates.append(VideoCandidate(
                        url=resp.url, kind=classify_url(resp.url) if MEDIA_URL_RE.search(resp.url) else "direct",
                        source="network", referer=referer, label=entry.get("label", ""), page=self._res.url))
                break

    def _on_page(self, page) -> None:
        if self._main_page is not None and page is not self._main_page:
            self._popup_pages.append(page)

    def _reap_popups(self) -> None:
        while self._popup_pages:
            pg = self._popup_pages.pop()
            try:
                pg.wait_for_load_state("commit", timeout=3000)
            except Exception:  # noqa: BLE001
                pass
            if self._res is not None:
                self._res.popups.append(pg.url)
            try:
                pg.close()
            except Exception:  # noqa: BLE001
                pass

    # helpers -----------------------------------------------------------------
    def _settle(self, page, idle_ms: int = 8000) -> None:
        try:
            page.wait_for_load_state("networkidle", timeout=idle_ms)
        except Exception:  # noqa: BLE001
            pass
        page.wait_for_timeout(700)
        self._reap_popups()
        if page.is_closed():
            return
        try:
            if page.url != self._main_url and urlparse(page.url).netloc != urlparse(self._main_url).netloc:
                # a click hijacked the tab (pop-under / redirect); record and go back
                self._res.popups.append(page.url)
                page.go_back(timeout=self.timeout_ms)
        except Exception:  # noqa: BLE001
            pass

    def _try_play(self, page) -> None:
        for frame in page.frames:
            for sel in _PLAY_SELECTORS:
                try:
                    loc = frame.locator(sel).first
                    if loc.count() and loc.is_visible(timeout=300):
                        loc.click(timeout=1500, no_wait_after=True)
                        page.wait_for_timeout(1200)
                        self._reap_popups()
                        break
                except Exception:  # noqa: BLE001
                    continue
            try:
                frame.evaluate("() => document.querySelectorAll('video').forEach(v => { v.muted = true; v.play && v.play().catch(()=>{}); })")
            except Exception:  # noqa: BLE001
                pass
        self._settle(page, 4000)

    def _harvest(self, page, label: str) -> None:
        res = self._res
        for frame in list(page.frames):
            try:
                html = frame.content()
            except Exception:  # noqa: BLE001
                continue
            furl = frame.url
            if frame is not page.main_frame and _http_url(furl):
                if not IGNORE_IFRAME_RE.search(furl):
                    res.candidates.append(VideoCandidate(furl, classify_url(furl), "frame", page.url, label, res.url))
                if not any(f["url"] == furl for f in res.frames):
                    res.frames.append({"url": furl, "html": html[:2_000_000]})
            cands, links = extract_from_html(html, furl if _http_url(furl) else page.url, label)
            res.candidates.extend(cands)
            if frame is page.main_frame:
                res.crawl_links.extend(links)
            try:
                srcs = frame.evaluate("() => Array.from(document.querySelectorAll('video,audio,source'))"
                                      ".map(v => v.currentSrc || v.src).filter(Boolean)")
            except Exception:  # noqa: BLE001
                srcs = []
            for s in srcs:
                if _http_url(s):
                    res.candidates.append(VideoCandidate(s, classify_url(s), "video-element", furl, label, res.url))

    # season / episode enumeration -------------------------------------------
    def _walk_selects(self, page, labels: list[str], used: set[str]) -> None:
        if self._combos >= self.max_combinations:
            return
        try:
            selects = [s for s in page.main_frame.evaluate(_RELEVANT_SELECTS_JS, SELECT_KW) if s["key"] not in used]
        except Exception:  # noqa: BLE001
            selects = []
        if not selects:
            if labels:
                self._combos += 1
                label = " / ".join(labels)
                self._res.selections.append(label)
                self._try_play(page)
                self._harvest(page, label)
            return
        sel = selects[0]
        options = [o for o in sel["options"] if not o["disabled"] and o["value"] != "" and not PLACEHOLDER_RE.match(o["text"])]
        if not options:
            self._walk_selects(page, labels, used | {sel["key"]})
            return
        word = sel["word"].strip().title()
        for opt in options:
            if self._combos >= self.max_combinations:
                break
            text = opt["text"] or opt["value"]
            part = f"{word} {text}" if word and text.isdigit() else text
            self._label = " / ".join(labels + [part])
            try:
                page.locator(sel["key"]).first.select_option(value=opt["value"], timeout=5000)
            except Exception:  # noqa: BLE001
                continue
            self._settle(page, 5000)
            self._walk_selects(page, labels + [part], used | {sel["key"]})

    def _click_items(self, page, kind: str, prefix: str) -> int:
        try:
            names = page.main_frame.evaluate(_EPISODE_ITEMS_JS, kind)
        except Exception:  # noqa: BLE001
            return 0
        clicked = 0
        for i, name in enumerate(names):
            if self._combos >= self.max_clicks:
                break
            label = (prefix + " / " if prefix else "") + (name or f"{kind} {i + 1}")
            self._label = label
            try:
                page.locator(f"[data-wts-{kind}='{i}']").first.click(timeout=3000, no_wait_after=True)
            except Exception:  # noqa: BLE001
                continue
            clicked += 1
            self._settle(page, 5000)
            if kind == "season":
                self._click_items(page, "episode", label)
            else:
                self._combos += 1
                self._res.selections.append(label)
                self._try_play(page)
                self._harvest(page, label)
        return clicked

    # main entry ----------------------------------------------------------------
    def analyze(self, url: str, enumerate_episodes: bool = True) -> PageResult:
        res = self._res = PageResult(url=url)
        self._label = ""
        self._combos = 0
        self._page_no += 1
        page = self._main_page = self._ctx.new_page()
        self._main_url = url
        page.on("download", lambda d: (res.downloads.append({"url": d.url, "filename": d.suggested_filename}),
                                       d.cancel()))
        page.on("dialog", lambda d: (res.dialogs.append(f"{d.type}: {d.message[:200]}"), d.dismiss()))
        page.on("console", lambda m: m.type == "error" and len(res.console_errors) < 100
                and res.console_errors.append(m.text[:300]))
        try:
            resp = page.goto(url, wait_until="domcontentloaded", timeout=self.timeout_ms)
            if resp is not None:
                res.status = resp.status
                res.headers = {k.lower(): v for k, v in resp.headers.items()}
                chain, r = [], resp.request.redirected_from
                while r is not None:
                    chain.append(r.url)
                    r = r.redirected_from
                res.redirects = list(reversed(chain))
                try:
                    res.raw_html = resp.text()
                except Exception:  # noqa: BLE001
                    pass
            self._main_url = page.url
            self._settle(page)
            res.final_url = page.url
            self._try_play(page)
            self._harvest(page, "")
            if enumerate_episodes:
                self._walk_selects(page, [], set())
                self._click_items(page, "season", "")
                if not any(s for s in res.selections):
                    self._click_items(page, "episode", "")
            try:
                res.rendered_html = page.content()
            except Exception:  # noqa: BLE001
                pass
            shot = self.evidence_dir / f"screenshot_{self._page_no}.png"
            try:
                page.screenshot(path=str(shot), full_page=False)
                res.screenshot = str(shot)
            except Exception:  # noqa: BLE001
                pass
        except Exception as exc:  # noqa: BLE001
            res.error = f"{type(exc).__name__}: {exc}"
            self.log(f"  ! browser error: {res.error}")
        finally:
            self._reap_popups()
            try:
                page.close()
            except Exception:  # noqa: BLE001
                pass
            self._main_page = None
        res.candidates = dedupe(res.candidates)
        res.crawl_links = list(dict.fromkeys(res.crawl_links))
        self._res = None
        return res


def write_cookie_jar(cookies: list[dict], path: Path) -> Path:
    """Write Playwright cookies in Netscape format so yt-dlp can reuse the session."""
    lines = ["# Netscape HTTP Cookie File"]
    for c in cookies:
        domain = c.get("domain", "")
        lines.append("\t".join([
            domain, "TRUE" if domain.startswith(".") else "FALSE", c.get("path", "/"),
            "TRUE" if c.get("secure") else "FALSE", str(int(c.get("expires", 0)) if c.get("expires", -1) > 0 else 0),
            c.get("name", ""), c.get("value", ""),
        ]))
    path.write_text("\n".join(lines) + "\n")
    path.chmod(0o600)
    return path


def now_stamp() -> str:
    return time.strftime("%Y%m%d-%H%M%S")
