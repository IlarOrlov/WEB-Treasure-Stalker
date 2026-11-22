from wts.catalog import build_catalog
from wts.models import PageResult, VideoCandidate as V


def page(url, title):
    return PageResult(url=url, final_url=url, rendered_html=f"<html><head><title>{title}</title></head></html>")


def test_movie_page_becomes_one_film_with_fallbacks():
    p = page("https://kino.example/12788--iron-man.html", "Железный человек (2008) смотреть онлайн бесплатно")
    cands = [V("https://cdn1.example/movies/a/b:1", "stream", "network", referer="https://player.example/embed/1", page=p.url),
             V("https://cdn2.example/movies/a/b:1", "stream", "network", referer="https://player.example/embed/1", page=p.url,
               protected=True),
             V("https://player.example/embed/1", "embed", "iframe", page=p.url),
             V("https://www.youtube.com/embed/abc", "platform-link", "json-ld", page=p.url)]
    items, series = build_catalog([p], cands, [], "kino.example", ytdlp_supported=lambda u: "youtube" in u)
    assert series == ""
    movie = items[0]
    assert movie.kind == "movie" and movie.title == "Железный человек (2008)"
    assert movie.outtmpl == "Железный человек (2008)/Железный человек (2008).%(ext)s"
    assert not movie.sources[0].protected and movie.sources[-1].protected  # unprotected first
    assert [i.kind for i in items] == ["movie", "video"]  # the player iframe itself is not offered


def test_series_with_deferred_episode_pages():
    p = page("https://anime.example/", "Берсерк все сезоны и серии смотреть")
    cands = [V("https://cdn.example/x.m3u8", "stream", "network", referer="https://pl.example/?t=1",
               page="https://anime.example/berserk-1-sezon-1-serija/")]
    deferred = ["https://anime.example/berserk-1-sezon-1-serija/", "https://anime.example/berserk-2-sezon-3-serija/"]
    items, series = build_catalog([p], cands, deferred, "anime.example")
    assert series == "Берсерк"
    assert [(i.season, i.episode, bool(i.sources), bool(i.page)) for i in items] == [(1, 1, True, False), (2, 3, False, True)]
    assert items[1].outtmpl == "Берсерк/Season 2/Episode 03.%(ext)s"


def test_ytdlp_platform_detection():
    from wts.platforms import classify

    t = classify("https://soundcloud.com/kutemane/avoid-me?utm_source=clipboard")
    assert t.platform == "ytdlp" and t.id == "Soundcloud"
    assert classify("https://kinogo.example/12788--zheleznyi-chelovek.html") is None
