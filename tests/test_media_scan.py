import shutil
import zipfile
from pathlib import Path

from wts.media_scan import mp4_end, scan_media, sniff

EICAR = ("X5O!P%@AP[4\\PZX54(P^)7CC)7}$" + "EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*").encode()


def sev(report, category):
    return {f["severity"] for f in report["findings"] if f["category"] == category}


def test_clean_clip_has_no_serious_findings(clip):
    r = scan_media(clip, use_av=False)
    assert r["detected_type"] == "mp4"
    assert r["container_end"] == r["size"]
    assert not [f for f in r["findings"] if f["severity"] in ("high", "critical")], r["findings"]
    assert any(s["codec_type"] == "video" for s in r["streams"])


def test_appended_zip_is_critical(clip, tmp_path):
    payload = tmp_path / "p.zip"
    with zipfile.ZipFile(payload, "w") as z:
        z.writestr("run.bat", "echo pwned")
    bad = tmp_path / "movie.mp4"
    bad.write_bytes(clip.read_bytes() + payload.read_bytes())
    r = scan_media(bad, use_av=False)
    assert "critical" in sev(r, "appended-data")
    end, _ = mp4_end(bad, bad.stat().st_size)
    assert end == clip.stat().st_size


def test_eicar_inside_media_is_flagged(clip, tmp_path):
    bad = tmp_path / "eicar.mp4"
    data = clip.read_bytes()
    bad.write_bytes(data + EICAR)
    r = scan_media(bad, use_av=False)
    assert any("EICAR" in f["message"] for f in r["findings"])


def test_executable_disguised_as_video(tmp_path):
    fake = tmp_path / "Episode.1.1080p.mp4"
    fake.write_bytes(b"MZ\x90\x00" + b"\x00" * 60 + b"This program cannot be run in DOS mode" + b"\x00" * 500)
    r = scan_media(fake, use_av=False)
    assert "critical" in sev(r, "fake-video")
    assert "high" in sev(r, "embedded-payload")


def test_extension_mismatch(clip, tmp_path):
    p = tmp_path / "video.mkv"
    shutil.copy(clip, p)
    r = scan_media(p, use_av=False)
    assert "medium" in sev(r, "type-mismatch")


def test_sniff_types():
    assert sniff(b"\x1a\x45\xdf\xa3" + b"\x00" * 20) == "matroska"
    assert sniff(b"\x7fELF" + b"\x00" * 20) == "elf-executable"
    assert sniff(b"<!DOCTYPE html><html>") == "html"
    assert sniff(b"#EXTM3U\n") == "m3u8-playlist"
