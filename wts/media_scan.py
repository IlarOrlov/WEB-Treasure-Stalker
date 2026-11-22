"""Static security analysis of downloaded media files.

Nothing here executes or plays the file. ffprobe parses the container, so run
the tool inside a VM/container when analysing hostile material (see README).
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import struct
import subprocess
from pathlib import Path
from typing import Any

from .av import antivirus_scan
from .models import Finding

CHUNK = 8 * 1024 * 1024
OVERLAP = 256

VIDEO_EXTS = {".mp4", ".m4v", ".mov", ".mkv", ".webm", ".avi", ".flv", ".ts", ".mpg", ".mpeg", ".3gp", ".ogv", ".wmv"}
AUDIO_EXTS = {".mp3", ".m4a", ".opus", ".ogg", ".flac", ".wav", ".aac", ".weba", ".mka"}
MEDIA_EXTS = VIDEO_EXTS | AUDIO_EXTS
EXPECTED = {
    ".mp4": {"mp4"}, ".m4v": {"mp4"}, ".mov": {"mp4"}, ".3gp": {"mp4"}, ".mkv": {"matroska"},
    ".webm": {"matroska"}, ".avi": {"avi"}, ".flv": {"flv"}, ".ts": {"mpegts"}, ".ogv": {"ogg"},
    ".wmv": {"asf"}, ".mpg": {"mpeg-ps", "mpegts"}, ".mpeg": {"mpeg-ps", "mpegts"},
    ".mp3": {"mp3"}, ".m4a": {"mp4"}, ".opus": {"ogg"}, ".ogg": {"ogg"}, ".flac": {"flac"}, ".wav": {"wav"},
    ".aac": {"aac"}, ".weba": {"matroska"}, ".mka": {"matroska"},
}

# Built at runtime so this source file itself does not match AV signatures.
_EICAR = ("X5O!P%@AP[4\\PZX54(P^)7CC)7}$" + "EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*").encode()

STRONG_PATTERNS = re.compile(
    rb"(This program cannot be run in DOS mode|<script[\s>]|javascript:|powershell(\.exe)?\s+-|"
    rb"WScript\.Shell|cmd\.exe\s*/c|mshta(\.exe)?\s|certutil(\.exe)?\s+-|Invoke-Expression|"
    rb"FromBase64String|/bin/(ba)?sh\s+-c|curl\s+-[sSfLo]+\s+http|wget\s+http|"
    + re.escape(_EICAR) + rb")",
    re.I,
)


def sniff(head: bytes) -> str:
    """Identify a blob by magic bytes."""
    if len(head) >= 12 and head[4:8] == b"ftyp":
        return "mp4"
    if head.startswith(b"\x1a\x45\xdf\xa3"):
        return "matroska"
    if head.startswith(b"RIFF") and head[8:12] == b"AVI ":
        return "avi"
    if head.startswith(b"RIFF") and head[8:12] == b"WAVE":
        return "wav"
    if head.startswith(b"fLaC"):
        return "flac"
    if head.startswith(b"ID3") or (len(head) > 1 and head[0] == 0xFF and head[1] & 0xE0 == 0xE0 and head[1] & 0x06):
        return "mp3"
    if len(head) > 1 and head[0] == 0xFF and head[1] & 0xF6 == 0xF0:
        return "aac"
    if head.startswith(b"FLV"):
        return "flv"
    if head.startswith(b"OggS"):
        return "ogg"
    if head.startswith(b"\x30\x26\xb2\x75\x8e\x66\xcf\x11"):
        return "asf"
    if head.startswith(b"\x00\x00\x01\xba"):
        return "mpeg-ps"
    if len(head) > 376 and head[0] == 0x47 and head[188] == 0x47 and head[376] == 0x47:
        return "mpegts"
    if head[4:8] in (b"moov", b"mdat", b"free", b"wide", b"skip"):
        return "mp4"
    if head.startswith(b"MZ"):
        return "pe-executable"
    if head.startswith(b"\x7fELF"):
        return "elf-executable"
    if head[:4] in (b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe", b"\xca\xfe\xba\xbe"):
        return "macho-executable"
    if head.startswith(b"PK\x03\x04"):
        return "zip-archive"
    if head.startswith(b"Rar!"):
        return "rar-archive"
    if head.startswith(b"7z\xbc\xaf\x27\x1c"):
        return "7z-archive"
    if head.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        return "ole-document/msi"
    if head.startswith(b"%PDF"):
        return "pdf"
    if head.startswith(b"#!"):
        return "script"
    if head.lstrip()[:15].lower().startswith((b"<!doctype html", b"<html", b"<script")):
        return "html"
    if head.startswith(b"#EXTM3U"):
        return "m3u8-playlist"
    return "unknown"


DANGEROUS_TYPES = {"pe-executable", "elf-executable", "macho-executable", "zip-archive", "rar-archive",
                   "7z-archive", "ole-document/msi", "pdf", "script", "html"}


def file_hashes(path: Path) -> dict[str, str]:
    h = {"md5": hashlib.md5(), "sha1": hashlib.sha1(), "sha256": hashlib.sha256()}
    with path.open("rb") as f:
        while chunk := f.read(CHUNK):
            for x in h.values():
                x.update(chunk)
    return {k: v.hexdigest() for k, v in h.items()}


# --------------------------------------------------------------------------- container walking


def mp4_end(path: Path, size: int) -> tuple[int | None, bool]:
    """Return (offset where the last valid top-level box ends, truncated?)."""
    pos = 0
    with path.open("rb") as f:
        while pos + 8 <= size:
            f.seek(pos)
            hdr = f.read(8)
            box_size, typ = struct.unpack(">I4s", hdr)
            if not re.fullmatch(rb"[A-Za-z0-9 \xa9_\-]{4}", typ):
                break
            if box_size == 1:
                box_size = struct.unpack(">Q", f.read(8))[0]
            elif box_size == 0:
                return size, False
            if box_size < 8:
                break
            if pos + box_size > size:
                return size, True
            pos += box_size
    return pos, False


def _vint(f, keep_marker: bool) -> tuple[int | None, int]:
    first = f.read(1)
    if not first:
        return None, 0
    b = first[0]
    length = 1
    mask = 0x80
    while length <= 8 and not (b & mask):
        mask >>= 1
        length += 1
    if length > 8:
        return None, 0
    rest = f.read(length - 1)
    value = b if keep_marker else (b & (mask - 1))
    for x in rest:
        value = (value << 8) | x
    if not keep_marker and value == (1 << (7 * length)) - 1:
        return -1, length  # unknown size
    return value, length


def mkv_end(path: Path, size: int) -> tuple[int | None, bool]:
    top_ids = {0x1A45DFA3, 0x18538067, 0xEC, 0xBF}
    pos = 0
    with path.open("rb") as f:
        while pos < size:
            f.seek(pos)
            eid, il = _vint(f, True)
            if eid not in top_ids:
                break
            esize, sl = _vint(f, False)
            if esize is None:
                break
            if esize == -1:
                return None, False  # live-written segment with unknown size
            nxt = pos + il + sl + esize
            if nxt > size:
                return size, True
            pos = nxt
    return pos, False


def avi_end(path: Path, size: int) -> tuple[int | None, bool]:
    pos = 0
    with path.open("rb") as f:
        while pos + 12 <= size:
            f.seek(pos)
            hdr = f.read(12)
            if hdr[:4] != b"RIFF" or hdr[8:12] not in (b"AVI ", b"AVIX"):
                break
            chunk = struct.unpack("<I", hdr[4:8])[0] + 8
            if pos + chunk > size:
                return size, True
            pos += chunk + (chunk & 1)
    return min(pos, size), False


WALKERS = {"mp4": mp4_end, "matroska": mkv_end, "avi": avi_end}


# --------------------------------------------------------------------------- content scanning


def _valid_elf(buf: bytes, i: int) -> bool:
    h = buf[i:i + 20]
    return (len(h) == 20 and h[4] in (1, 2) and h[5] in (1, 2) and h[6] == 1
            and int.from_bytes(h[16:18], "little" if h[5] == 1 else "big") in (2, 3)
            and int.from_bytes(h[18:20], "little" if h[5] == 1 else "big") in (3, 8, 0x28, 0x3E, 0xB7, 0xF3))


def _valid_zip(buf: bytes, i: int) -> bool:
    h = buf[i:i + 30]
    if len(h) < 30:
        return False
    ver, = struct.unpack("<H", h[4:6])
    method, = struct.unpack("<H", h[8:10])
    nlen, = struct.unpack("<H", h[26:28])
    if ver > 63 or method not in (0, 8, 12, 14, 93, 95) or not 1 <= nlen <= 255:
        return False
    name = buf[i + 30:i + 30 + nlen]
    return len(name) == nlen and re.fullmatch(rb"[\x20-\x7e]+", name) is not None


def scan_content(path: Path, limit: int = 25) -> list[dict[str, Any]]:
    """Look for embedded executables, archives and script payloads."""
    hits: list[dict[str, Any]] = []
    seen: set[int] = set()
    with path.open("rb") as f:
        base = 0
        prev = b""
        while len(hits) < limit:
            chunk = f.read(CHUNK)
            if not chunk:
                break
            buf = prev + chunk
            off0 = base - len(prev)
            for m in STRONG_PATTERNS.finditer(buf):
                off = off0 + m.start()
                if off not in seen:
                    seen.add(off)
                    hits.append({"offset": off, "match": m.group(0)[:60].decode("latin-1")})
            for sig, check, name in ((b"\x7fELF", _valid_elf, "embedded ELF header"),
                                     (b"PK\x03\x04", _valid_zip, "embedded ZIP local header")):
                i = buf.find(sig)
                while i != -1 and len(hits) < limit:
                    off = off0 + i
                    if off not in seen and check(buf, i):
                        seen.add(off)
                        hits.append({"offset": off, "match": name})
                    i = buf.find(sig, i + 1)
            prev = buf[-OVERLAP:]
            base += len(chunk)
    return hits


def ffprobe(path: Path) -> tuple[dict[str, Any] | None, str]:
    if not shutil.which("ffprobe"):
        return None, "ffprobe not installed"
    try:
        p = subprocess.run(
            ["ffprobe", "-v", "error", "-show_format", "-show_streams", "-show_chapters", "-of", "json", str(path)],
            capture_output=True, text=True, timeout=120,
        )
    except subprocess.TimeoutExpired:
        return None, "ffprobe timed out (possible parser trap)"
    try:
        data = json.loads(p.stdout or "{}")
    except ValueError:
        data = None
    return data, p.stderr.strip()[:2000]


def deep_decode(path: Path, timeout: int = 1800) -> str:
    try:
        p = subprocess.run(["ffmpeg", "-v", "error", "-xerror", "-i", str(path), "-f", "null", "-"],
                           capture_output=True, text=True, timeout=timeout)
        return p.stderr.strip()[:2000]
    except subprocess.TimeoutExpired:
        return "decode timed out"


URL_RE = re.compile(r"https?://[^\s\"'<>]+", re.I)


def scan_media(path: Path, deep: bool = False, use_av: bool = True, expect_video: bool | None = None) -> dict[str, Any]:
    findings: list[Finding] = []
    size = path.stat().st_size
    report: dict[str, Any] = {"file": str(path), "size": size, "hashes": file_hashes(path)}

    with path.open("rb") as f:
        head = f.read(4096)
    detected = sniff(head)
    report["detected_type"] = detected
    ext = path.suffix.lower()
    if expect_video is None:
        expect_video = ext not in AUDIO_EXTS
    if detected in DANGEROUS_TYPES:
        findings.append(Finding("critical", "fake-video",
                                f"File content is '{detected}', not a video (disguised payload)", head[:32].hex()))
    elif ext in EXPECTED and detected not in EXPECTED[ext]:
        findings.append(Finding("medium", "type-mismatch",
                                f"Extension {ext} but content looks like '{detected}'"))

    walker = WALKERS.get(detected)
    if walker:
        end, truncated = walker(path, size)
        report["container_end"] = end
        if truncated:
            findings.append(Finding("low", "container", "Container structure is truncated (incomplete download?)"))
        if end is not None and end < size:
            extra = size - end
            with path.open("rb") as f:
                f.seek(end)
                tail = f.read(4096)
            tail_type = sniff(tail)
            sev = "critical" if tail_type in DANGEROUS_TYPES else ("medium" if extra > 1024 else "low")
            findings.append(Finding(sev, "appended-data",
                                    f"{extra} bytes appended after end of {detected} container (looks like '{tail_type}')",
                                    f"offset={end} head={tail[:32].hex()}"))

    for hit in scan_content(path):
        m = hit["match"]
        sev = "high" if ("DOS mode" in m or "ELF" in m or "EICAR" in m.upper()) else "medium"
        if "EICAR" in m.upper():
            m = "EICAR antivirus test signature"
        findings.append(Finding(sev, "embedded-payload", f"Suspicious content inside media: {m}",
                                f"offset={hit['offset']}"))

    probe, perr = ffprobe(path)
    if probe is not None:
        fmt = probe.get("format", {})
        streams = probe.get("streams", [])
        report["format"] = {k: fmt.get(k) for k in ("format_name", "format_long_name", "duration", "bit_rate",
                                                     "nb_streams") if fmt.get(k) is not None}
        report["tags"] = fmt.get("tags", {})
        report["streams"] = [{k: s.get(k) for k in ("index", "codec_type", "codec_name", "width", "height",
                                                    "r_frame_rate", "bit_rate", "channels", "tags")
                              if s.get(k) is not None} for s in streams]
        if expect_video and not any(s.get("codec_type") == "video" for s in streams):
            findings.append(Finding("medium", "container", "No video stream found in file"))
        for s in streams:
            ctype = s.get("codec_type")
            tags = s.get("tags", {}) or {}
            if ctype == "attachment":
                mime = tags.get("mimetype", "")
                fname = tags.get("filename", "")
                font = mime.startswith(("font/", "application/x-truetype", "application/vnd.ms-opentype",
                                        "application/x-font")) or fname.lower().endswith((".ttf", ".otf"))
                findings.append(Finding("info" if font else "high", "attachment",
                                        f"Embedded attachment '{fname}' ({mime or 'unknown type'})"))
            elif ctype == "data":
                findings.append(Finding("low", "data-stream", f"Data stream present ({s.get('codec_tag_string', '')})"))
        all_tags = dict(fmt.get("tags", {}) or {})
        for s in streams:
            all_tags.update({f"stream{s.get('index')}.{k}": v for k, v in (s.get("tags") or {}).items()})
        for k, v in all_tags.items():
            v = str(v)
            if re.search(r"<script|javascript:|powershell|cmd\.exe|\$\(|eval\(", v, re.I):
                findings.append(Finding("high", "metadata", f"Script-like content in metadata tag '{k}'", v[:200]))
            elif len(v) > 4096:
                findings.append(Finding("medium", "metadata", f"Unusually long metadata tag '{k}' ({len(v)} chars)"))
            for u in URL_RE.findall(v):
                findings.append(Finding("info", "metadata-url", f"URL in metadata tag '{k}'", u[:200]))
    if perr:
        findings.append(Finding("medium" if probe is None else "low", "parser",
                                "ffprobe reported errors parsing the file (malformed media can target parsers)",
                                perr[:500]))
    if deep:
        derr = deep_decode(path)
        report["decode_errors"] = derr
        if derr:
            findings.append(Finding("medium", "decoder", "Full decode produced errors", derr[:500]))

    if use_av:
        findings.extend(antivirus_scan(path))
    report["findings"] = [x.to_dict() for x in findings]
    return report
