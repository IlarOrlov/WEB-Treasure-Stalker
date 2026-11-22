from wts.discovery import extract_from_html, is_media_response

HTML = """
<html><head>
<meta property="og:video" content="https://cdn.example.com/v/trailer.mp4">
<script type="application/ld+json">{"@type":"VideoObject","embedUrl":"https://player.vimeo.com/video/123"}</script>
</head><body>
<iframe src="https://www.youtube.com/embed/dQw4w9WgXcQ"></iframe>
<iframe src="https://googleads.g.doubleclick.net/pagead/ads"></iframe>
<iframe data-src="//streamtape.com/e/abc123"></iframe>
<blockquote class="instagram-media" data-instgrm-permalink="https://www.instagram.com/reel/Cxyz123/"></blockquote>
<video><source src="/media/a.webm"></video>
<a href="/series/breaking-bad/season-2/episode-3">S2E3</a>
<a href="/series/dexter/season-1">Dexter season 1</a>
<a href="/news/123">New episode of everything</a>
<a href="/about">About</a>
<a href="https://other.example.org/season-1">other site</a>
<script>jwplayer("p").setup({file: "https:\\/\\/hls.example.com\\/master.m3u8"});</script>
</body></html>
"""


def test_extracts_all_candidate_types():
    cands, links = extract_from_html(HTML, "https://site.example.com/watch/breaking-bad")
    urls = {c.url: c for c in cands}
    assert "https://cdn.example.com/v/trailer.mp4" in urls
    assert "https://player.vimeo.com/video/123" in urls
    assert "https://www.youtube.com/embed/dQw4w9WgXcQ" in urls
    assert "https://streamtape.com/e/abc123" in urls
    assert "https://www.instagram.com/reel/Cxyz123/" in urls
    assert "https://site.example.com/media/a.webm" in urls
    assert urls["https://hls.example.com/master.m3u8"].kind == "stream"
    assert not any("doubleclick" in u for u in urls)
    # only episodes of the same title are crawled, never "related" other shows
    assert links == ["https://site.example.com/series/breaking-bad/season-2/episode-3"]


def test_related_episode_link():
    from wts.discovery import related_episode_link as rel

    base = "https://kinogo.example/7941--mentalist-1.html"
    assert rel(base, "https://kinogo.example/7941--mentalist-1/season-2-episode-3.html")
    assert not rel(base, "https://kinogo.example/8499--911-sluzhba-spaseniya-serial.html")
    assert not rel(base, "https://kinogo.example/121226--dekster-voskreshenie.html")
    assert rel("https://x.example/show/the-office/", "https://x.example/watch/the-office-s02e03")


def test_media_response_detection():
    assert is_media_response("https://x/y/master.m3u8", "xhr", "")
    assert is_media_response("https://x/v", "media", "video/mp4")
    assert not is_media_response("https://x/seg-12.ts", "xhr", "video/mp2t")
    assert not is_media_response("https://x/app.js", "script", "application/javascript")
