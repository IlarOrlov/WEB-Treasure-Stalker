"""Sitemap discovery, season pages, player-tab switching, protected-playlist detection, parallel-download helpers."""
import functools
import gzip
import http.server
import threading
from pathlib import Path

import pytest

from wts import discovery
from wts.discovery import (_manifest_hosts_resolve, quality_variants, related_links, series_tokens, sitemap_urls)
from wts.models import VideoCandidate


class _Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


@pytest.fixture()
def serve(tmp_path):
    def start(files: dict[str, bytes]) -> str:
        for name, data in files.items():
            p = tmp_path / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(_Quiet, directory=str(tmp_path)))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        servers.append(srv)
        return f"http://127.0.0.1:{srv.server_address[1]}"

    servers: list = []
    yield start
    for s in servers:
        s.shutdown()


def test_sitemap_index_robots_and_gzip(serve, tmp_path):
    url = serve({})  # the folder is served live: files are written once the address is known
    child = f"<urlset><url><loc>{url}/show-4-sezon-1-serija/</loc></url><url><loc>https://evil.example/x</loc></url>"\
            f"<url><loc>{url}/show-4-sezon-2-serija/?a=1&amp;b=2</loc></url></urlset>".encode()
    (tmp_path / "robots.txt").write_text(f"User-agent: *\nSitemap: {url}/sitemap_index.xml\n")
    (tmp_path / "sitemap_index.xml").write_text(
        f"<sitemapindex><sitemap><loc>{url}/post-sitemap.xml.gz</loc></sitemap>"
        f"<sitemap><loc>{url}/author-sitemap.xml</loc></sitemap></sitemapindex>")
    (tmp_path / "post-sitemap.xml.gz").write_bytes(gzip.compress(child))
    (tmp_path / "author-sitemap.xml").write_text(f"<urlset><url><loc>{url}/author/admin/</loc></url></urlset>")
    urls = sitemap_urls(url + "/")
    assert f"{url}/show-4-sezon-1-serija/" in urls
    assert f"{url}/show-4-sezon-2-serija/?a=1&b=2" in urls  # entities unescaped
    assert not any("evil.example" in u for u in urls)  # other sites are never followed
    assert not any("/author/" in u for u in urls)  # author/user sitemaps are skipped


def test_sitemap_size_cap(serve):
    big = b"<urlset>" + b"<url><loc>http://127.0.0.1/a-1-sezon-1-serija/</loc></url>" * 20000 + b"</urlset>"
    url = serve({"sitemap.xml": big})
    assert sitemap_urls(url + "/", max_bytes=100_000) == []


def test_season_page_links_judged_by_title_words():
    start = "https://site.su/"
    html = """<a href="/berserk-4-sezon-1-serija/">1</a><a href="/berserk-4-sezon-2-serija/">2</a>
              <a href="/category/4-sezon/">s4</a><a href="/other-title-1-sezon-1-serija/">other</a>
              <a href="/about/">about</a>"""
    tokens = series_tokens(["https://site.su/berserk-1-sezon-1-serija/", "https://site.su/berserk-2-sezon-3-serija/"])
    assert tokens == {"berserk"}
    links = related_links(html, "https://site.su/category/4-sezon/", start, tokens)
    assert "https://site.su/berserk-4-sezon-1-serija/" in links
    assert "https://site.su/category/4-sezon/" in links
    assert "https://site.su/other-title-1-sezon-1-serija/" not in links
    assert "https://site.su/about/" not in links


def test_quality_variants_respect_max_height():
    c = VideoCandidate("https://cdn.x/abc:123/360.mp4:hls:manifest.m3u8", "stream", "network")
    assert [v.url.rsplit("/", 1)[1][:4] for v in quality_variants(c)] == ["1080", "720.", "480.", "360."]
    assert [v.url.rsplit("/", 1)[1][:4] for v in quality_variants(c, 720)] == ["720.", "480.", "360."]
    plain = VideoCandidate("https://cdn.x/master.m3u8", "stream", "network")
    assert quality_variants(plain) == [plain]


def test_manifest_with_player_only_hosts_is_protected():
    assert _manifest_hosts_resolve(b"#EXTM3U\nseg0.ts\n", "https://cdn.example/a.m3u8")
    assert not _manifest_hosts_resolve(b'<MPD><BaseURL>https://qwzxq.invalid/v/</BaseURL></MPD>',
                                       "https://cdn.example/a.mpd")


def test_partial_cleanup_only_touches_own_files(tmp_path):
    from wts.downloader import _remove_partials

    mine = tmp_path / "Episode 01.mp4"
    other = tmp_path / "Episode 02.mp4.part"
    for p in (tmp_path / "Episode 01.mp4.part", tmp_path / "Episode 01.mp4.part-Frag3", tmp_path / "Episode 01.f137.mp4",
              other, tmp_path / "Episode 010.mp4.part"):
        p.write_bytes(b"x")
    _remove_partials({str(mine)})
    left = sorted(p.name for p in tmp_path.iterdir())
    assert left == ["Episode 010.mp4.part", "Episode 02.mp4.part"]


def test_progress_mux_merges_parallel_downloads():
    from wts.pipeline import _ProgressMux

    seen = []
    mux = _ProgressMux(seen.append)
    a, b = mux.channel("a", "Episode 1"), mux.channel("b", "Episode 2")
    a({"status": "downloading", "downloaded": 10, "total": 100, "speed": 5, "eta": 3})
    b({"status": "downloading", "downloaded": 30, "total": 100, "speed": 7, "eta": 9})
    last = seen[-1]
    assert last["downloaded"] == 40 and last["total"] == 200 and last["speed"] == 12 and last["eta"] == 9
    assert "2 downloads in parallel" in last["title"]
    mux.close("a")
    b({"status": "downloading", "downloaded": 50, "total": 100, "speed": 7, "eta": 5})
    assert seen[-1]["title"] == "Episode 2"


def _have_browser() -> bool:
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            p.chromium.launch().close()
        return True
    except Exception:  # noqa: BLE001
        return False


@pytest.mark.browser
def test_player_tabs_skip_encrypted_and_unavailable(tmp_path):
    if not _have_browser():
        pytest.skip("Playwright Chromium not installed")
    root = Path(__file__).parent / "site" / "tabs"
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(_Quiet, directory=str(root)))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}/index.html"
    discovery._PLAYER_PREF.clear()
    discovery._PLAYER_MISSES.clear()
    logs: list[str] = []
    try:
        with discovery.BrowserDiscoverer(tmp_path, 10, log=logs.append, lean=True) as bd:
            bd.page_budget = 60
            res = bd.analyze(url, enumerate_episodes=False, quick=True)
    finally:
        srv.shutdown()
    streams = {c.url.rsplit("/", 1)[1]: c.protected for c in res.candidates if c.kind == "stream"}
    assert streams.get("enc.m3u8") is True, (streams, logs)  # encrypted playlist -> protected, never decoded
    assert streams.get("good.m3u8") is False, (streams, logs)  # the next working player was used
    assert discovery._PLAYER_PREF[discovery.site_key(url)] == "gamma"
    assert any("encrypted/obfuscated" in m for m in logs)


@pytest.mark.browser
def test_player_tabs_ignore_watchlist_and_trailer(tmp_path):
    """yummyanime: span tabs where only the active one has a class, a trailer tab beside them, and watch-list
    buttons ('Запланировано', 'Брошено', ...) elsewhere on the page."""
    if not _have_browser():
        pytest.skip("Playwright Chromium not installed")
    root = Path(__file__).parent / "site" / "tabs"
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(_Quiet, directory=str(root)))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}/watchlist.html"
    discovery._PLAYER_PREF.clear()
    discovery._PLAYER_MISSES.clear()
    logs: list[str] = []
    try:
        with discovery.BrowserDiscoverer(tmp_path, 10, log=logs.append, lean=True) as bd:
            bd.page_budget = 60
            res = bd.analyze(url, enumerate_episodes=False, quick=True)
    finally:
        srv.shutdown()
    tried = [m for m in logs if "trying player" in m]
    assert tried == ["  trying player 'Kodik'"], logs
    streams = {c.url.rsplit("/", 1)[1]: c.protected for c in res.candidates if c.kind == "stream"}
    assert streams.get("good.m3u8") is False, (streams, logs)
