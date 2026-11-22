import json
import os

import pytest

from wts.platforms import classify, parse_spotify_embed
from wts.prompts import FormatChoice, Prompter, expand_items, parse_items, safe_name


@pytest.mark.parametrize("url,platform,kind", [
    ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "youtube", "video"),
    ("https://youtu.be/dQw4w9WgXcQ?t=10", "youtube", "video"),
    ("https://www.youtube.com/shorts/dQw4w9WgXcQ", "youtube", "video"),
    ("https://m.youtube.com/live/dQw4w9WgXcQ", "youtube", "video"),
    ("https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PL1234567890", "youtube", "video-in-playlist"),
    ("https://www.youtube.com/playlist?list=PL1234567890", "youtube", "playlist"),
    ("https://www.youtube.com/@SomeChannel", "youtube", "other"),
    ("https://www.youtube.com/", "youtube", "other"),
    ("https://www.youtube.com/results?search_query=x", "youtube", "other"),
    ("https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT?si=abc", "spotify", "track"),
    ("https://open.spotify.com/intl-he/album/4aawyAB9vmqN3uQ7FjRGTy", "spotify", "album"),
    ("https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M", "spotify", "playlist"),
    ("spotify:track:4cOdK2wGLETKBW3PvgPWqT", "spotify", "track"),
    ("https://open.spotify.com/artist/0gxyHStUsqpMadRV0Di1Qt", "spotify", "artist"),
])
def test_classify(url, platform, kind):
    t = classify(url)
    assert (t.platform, t.kind) == (platform, kind)


def test_classify_other_sites():
    assert classify("https://example.com/watch?v=dQw4w9WgXcQ") is None


def test_parse_items():
    assert parse_items("all", 10) is None
    assert parse_items("1, 3 5-8", 10) == "1,3,5-8"
    assert expand_items("1,3,5-8", 10) == [1, 3, 5, 6, 7, 8]
    with pytest.raises(ValueError):
        parse_items("0", 10)
    with pytest.raises(ValueError):
        parse_items("5-11", 10)
    with pytest.raises(ValueError):
        parse_items("abc", 10)


def test_format_choices():
    assert FormatChoice("video", "best", 1080).ytdl_opts()["format"].startswith("bv*[height<=1080]")
    mp3 = FormatChoice("audio", "mp3").ytdl_opts()
    assert mp3["postprocessors"][0]["preferredcodec"] == "mp3"
    assert FormatChoice("video", "mp4").ytdl_opts()["merge_output_format"] == "mp4"


def test_non_interactive_prompter(tmp_path):
    p = Prompter(interactive=False, preset_path=str(tmp_path / "dl"), preset_format="flac", preset_items="2-3")
    assert p.path("~/x") == (tmp_path / "dl").resolve()
    assert p.format().container == "flac"
    assert p.items(5) == "2-3"
    defaults = Prompter(interactive=False)
    assert defaults.format("mp3").kind == "audio"
    assert defaults.items(4) is None


def test_safe_name():
    assert safe_name('a/b:c*?"d') == "a_b_c_d"
    assert safe_name("   ") == "download"


def test_parse_spotify_embed():
    entity = {"type": "playlist", "name": "Mix", "subtitle": "me", "trackList": [
        {"title": "Song A", "subtitle": "Artist\xa0X,\xa0Artist Y", "duration": 200000, "entityType": "track"},
        {"title": "Ep", "subtitle": "Pod", "duration": 1, "entityType": "episode"}]}
    html = ('<script id="__NEXT_DATA__" type="application/json">'
            + json.dumps({"props": {"pageProps": {"state": {"data": {"entity": entity}}}}}) + "</script>")
    meta = parse_spotify_embed(html)
    assert meta["name"] == "Mix"
    assert meta["tracks"] == [{"title": "Song A", "artists": "Artist X, Artist Y", "duration": 200.0}]


network = pytest.mark.skipif(not os.environ.get("WTS_NETWORK_TESTS"), reason="set WTS_NETWORK_TESTS=1")


@network
def test_youtube_channel_downloads_nothing(tmp_path):
    from wts.platforms import run_platform

    r = run_platform(classify("https://www.youtube.com/@YouTube"), Prompter(interactive=False, preset_path=str(tmp_path)),
                     log=lambda m: None)
    assert r["downloaded_files"] == 0 and "no main video" in r["message"]


@network
def test_youtube_video_single_file(tmp_path):
    from wts.platforms import run_platform

    r = run_platform(classify("https://www.youtube.com/watch?v=jNQXAC9IVRw"),
                     Prompter(interactive=False, preset_path=str(tmp_path), preset_format="mp4"), log=lambda m: None)
    assert r["downloaded_files"] == 1
    assert r["downloads"][0]["files"][0].endswith(".mp4")


def test_interactive_prompts(monkeypatch, tmp_path, capsys):
    answers = iter([str(tmp_path / "my dl"), "abc", "6", "x", "2-3"])  # path, bad choice, MP3, bad items, items
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    p = Prompter(interactive=True)
    assert p.path("~/default") == (tmp_path / "my dl").resolve()
    fc = p.format("best")
    assert (fc.kind, fc.container) == ("audio", "mp3")
    assert p.items(5) == "2-3"
    out = capsys.readouterr().out
    assert "Audio - MP3" in out and "invalid item" in out
