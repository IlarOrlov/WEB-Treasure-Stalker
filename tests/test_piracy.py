from wts.models import PageResult, VideoCandidate
from wts.piracy import assess_site, assess_video


def page(url, html):
    return PageResult(url=url, final_url=url, rendered_html=html)


def test_pirate_portal_scores_high():
    html = ("<title>Show S01 1080p WEB-DL</title><body>Watch free online. This site does not store any files."
            "</body>")
    cands = [VideoCandidate("https://streamtape.com/e/1", "embed", "iframe", label="Season 1 / Episode 1"),
             VideoCandidate("https://dood.watch/e/2", "embed", "iframe", label="Season 1 / Episode 2"),
             VideoCandidate("https://mixdrop.co/e/3", "embed", "iframe", label="Season 1 / Episode 3")]
    r = assess_site([page("https://fmovies-hd.xyz/tv/show", html)], cands, {"aggressive_ad_networks": ["popads.net"]})
    assert r["verdict"] == "LIKELY PIRATED"
    assert r["score"] >= 60


def test_official_platform_scores_low():
    r = assess_site([page("https://www.youtube.com/watch?v=x", "<title>Official trailer</title>")],
                    [VideoCandidate("https://www.youtube.com/watch?v=x", "platform-link", "link")], {})
    assert r["verdict"] == "NO STRONG INDICATORS"
    assert r["score"] == 0


def test_news_site_with_instagram_embed_not_flagged():
    r = assess_site([page("https://news.example.com/article", "<title>Story</title><body>news text</body>")],
                    [VideoCandidate("https://www.instagram.com/reel/abc/", "platform-link", "instagram-embed")], {})
    assert r["verdict"] == "NO STRONG INDICATORS"


def test_video_release_name_and_host():
    dl = {"candidate": {"url": "https://streamtape.com/e/1", "label": ""},
          "info": {"title": "Movie.2024.1080p.WEBRip.x264-YTS", "duration": 6000}}
    r = assess_video(dl, [], site_score=70)
    assert r["verdict"] == "LIKELY PIRATED"
