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


def test_episode_keys_and_titles():
    from wts.episodes import episode_key, episode_path, series_title
    from wts.models import VideoCandidate as V

    assert episode_key(V("https://x/hls/abc.m3u8", "stream", "network", label="Season 3 / Episode 12")) == (3, 12)
    assert episode_key(V("https://mp.thementalist.ru/The-Mentalist-season-1-episode-7.mp4", "direct", "inline-script")) == (1, 7)
    assert episode_key(V("https://cdn/x/Show.S02E05.1080p.mkv", "direct", "link")) == (2, 5)
    # a link listed on an episode page is NOT that page's episode
    assert episode_key(V("https://cdn/trailer.mp4", "direct", "inline-script", page="https://x/season-1/episode-1")) is None
    assert episode_key(V("https://cdn/v.mp4", "direct", "video-source", page="https://x/seasons/season-2/episode-4")) == (2, 4)
    assert series_title("<title>Менталист (1-7 сезон) смотреть онлайн бесплатно все серии</title>", "x") == "Менталист"
    assert series_title("<title>Сериал Менталист все сезоны и серии смотреть Бесплатно</title>", "x") == "Менталист"
    assert series_title("<h1>Операция: Семейка Ёдзакура</h1>", "x") == "Операция - Семейка Ёдзакура"
    assert series_title("<title>Breaking Bad: watch online</title>", "x") == "Breaking Bad"
    assert episode_path("Менталист", (1, 3)) == "Менталист/Season 1/Episode 03.%(ext)s"


def test_episode_key_repeated_season():
    from wts.episodes import episode_key
    from wts.models import VideoCandidate as V

    assert episode_key(V("https://mp.thementalist.ru/The-Mentalist-season-3-episode-3-17.mp4", "direct", "inline-script")) == (3, 17)
    assert episode_key(V("https://x/Show-season-2-episode-11-1080p.mp4", "direct", "link")) == (2, 11)


def test_continuation_part_pages_continue_episode_numbers():
    """yummyanime: '2 сезон' has episodes 1-12 and '2 сезон. Часть 2' numbers its 7 episodes from 1 again."""
    from wts.episodes import group_episodes
    from wts.models import VideoCandidate as V
    p1 = "https://y.tv/4995-show-2-sezon-f3.html"
    p2 = "https://y.tv/9025-show-2-sezon-chast-2.html"
    ref = "https://kodikplayer.com/serial/1/x/720p"
    cands = [V(f"https://cdn/a{e}.m3u8", "stream", "network", ref, f"Season 2 / Episode {e}", p1) for e in range(1, 13)]
    cands += [V(f"https://cdn/b{e}.m3u8", "stream", "network", ref, f"Season 2 / Episode {e}", p2) for e in range(1, 8)]
    eps, rest = group_episodes(cands)
    assert sorted(eps) == [(2, e) for e in range(1, 20)]
    assert eps[(2, 13)][0].url == "https://cdn/b1.m3u8"
    assert eps[(2, 13)][0].label == "Season 2 / Episode 13"


def test_continuation_part_already_continuing_is_kept():
    from wts.episodes import group_episodes
    from wts.models import VideoCandidate as V
    p1 = "https://y.tv/1-show.html"
    p2 = "https://y.tv/2-show-part-2.html"
    cands = [V(f"https://cdn/a{e}.m3u8", "stream", "network", "https://pl/x", f"Season 1 / Episode {e}", p1) for e in (1, 2)]
    cands += [V(f"https://cdn/b{e}.m3u8", "stream", "network", "https://pl/x", f"Season 1 / Episode {e}", p2) for e in (3, 4)]
    eps, _ = group_episodes(cands)
    assert sorted(eps) == [(1, 1), (1, 2), (1, 3), (1, 4)]


def test_same_episode_on_two_plain_pages_is_not_renumbered():
    from wts.episodes import group_episodes
    from wts.models import VideoCandidate as V
    cands = [V("https://cdn/a.m3u8", "stream", "network", "https://pl/x", "Season 1 / Episode 1", "https://y.tv/show"),
             V("https://cdn/b.m3u8", "stream", "network", "https://pl/x", "Season 1 / Episode 1", "https://y.tv/show/ep1")]
    eps, _ = group_episodes(cands)
    assert list(eps) == [(1, 1)] and len(eps[(1, 1)]) == 2


def test_protected_unlabelled_stream_dropped_when_series_found():
    """The site's default player (token-protected) is not a separate video once the episodes were captured."""
    from wts.episodes import group_episodes
    from wts.models import VideoCandidate as V
    page = "https://y.tv/1-show.html"
    cands = [V("https://cdn/a.m3u8", "stream", "network", "https://kodik/x", "Season 1 / Episode 1", page),
             V("https://userplay.live/0/tok", "stream", "network", "https://alloha/x", "", page, protected=True),
             V("https://www.youtube.com/embed/abc", "platform-link", "iframe", page, "", page)]
    eps, rest = group_episodes(cands)
    assert [c.url for c in rest] == ["https://www.youtube.com/embed/abc"]
