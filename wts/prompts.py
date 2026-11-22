"""Interactive questions (path, format, playlist items) with non-interactive fallbacks."""
from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class FormatChoice:
    kind: str = "video"  # video | audio
    container: str = "best"  # video: best|mp4|mkv|webm   audio: best|mp3|m4a|opus|flac|wav
    max_height: int | None = None
    raw: str | None = None  # raw yt-dlp format selector (advanced override)

    def describe(self) -> str:
        if self.raw:
            return f"yt-dlp format '{self.raw}'"
        if self.kind == "audio":
            return "audio, " + ("best original codec" if self.container == "best" else self.container.upper())
        res = f"up to {self.max_height}p" if self.max_height else "best resolution"
        return f"video, {'auto mp4/mkv' if self.container == 'best' else self.container.upper()}, {res}"

    def ytdl_opts(self) -> dict[str, Any]:
        if self.raw:
            return {"format": self.raw, "merge_output_format": "mp4/mkv"}
        if self.kind == "audio":
            pp = [{"key": "FFmpegExtractAudio", "preferredcodec": self.container, "preferredquality": "0"},
                  {"key": "FFmpegMetadata", "add_metadata": True}]
            return {"format": "ba/b", "postprocessors": pp}
        h = f"[height<={self.max_height}]" if self.max_height else ""
        if self.container == "mp4":
            return {"format": f"bv*{h}[ext=mp4]+ba[ext=m4a]/b{h}[ext=mp4]/bv*{h}+ba/b{h}/b",
                    "merge_output_format": "mp4",
                    "postprocessors": [{"key": "FFmpegVideoRemuxer", "preferedformat": "mp4"}]}
        if self.container == "mkv":
            return {"format": f"bv*{h}+ba/b{h}/b", "merge_output_format": "mkv",
                    "postprocessors": [{"key": "FFmpegVideoRemuxer", "preferedformat": "mkv"}]}
        if self.container == "webm":
            return {"format": f"bv*{h}[ext=webm]+ba[ext=webm]/bv*{h}+ba/b{h}/b", "merge_output_format": "webm/mkv"}
        return {"format": f"bv*{h}+ba/b{h}/bv*+ba/b", "merge_output_format": "mp4/mkv"}


FORMAT_PRESETS: dict[str, tuple[str, str, str]] = {
    "best": ("video", "best", "Video - best quality (auto MP4/MKV)"),
    "mp4": ("video", "mp4", "Video - MP4"),
    "mkv": ("video", "mkv", "Video - MKV"),
    "webm": ("video", "webm", "Video - WebM"),
    "audio": ("audio", "best", "Audio only - best original codec"),
    "mp3": ("audio", "mp3", "Audio - MP3"),
    "m4a": ("audio", "m4a", "Audio - M4A (AAC)"),
    "opus": ("audio", "opus", "Audio - Opus"),
    "flac": ("audio", "flac", "Audio - FLAC (lossless container)"),
    "wav": ("audio", "wav", "Audio - WAV"),
}
HEIGHTS = ["best", "2160", "1440", "1080", "720", "480", "360"]


def parse_items(spec: str, count: int | None = None) -> str | None:
    """'all' -> None, '1,3,5-8' -> normalised yt-dlp playlist_items string. Raises ValueError."""
    spec = spec.strip().lower()
    if spec in ("", "all", "a", "*"):
        return None
    parts = []
    for tok in re.split(r"[,\s]+", spec):
        if not tok:
            continue
        m = re.fullmatch(r"(\d+)(?:-(\d+))?", tok)
        if not m:
            raise ValueError(f"invalid item '{tok}' (use e.g. 1,3,5-8 or all)")
        a, b = int(m.group(1)), int(m.group(2) or m.group(1))
        if a < 1 or b < a or (count and b > count):
            raise ValueError(f"item range '{tok}' is out of bounds" + (f" (1-{count})" if count else ""))
        parts.append(f"{a}-{b}" if a != b else str(a))
    if not parts:
        raise ValueError("no items selected")
    return ",".join(parts)


def expand_items(spec: str | None, count: int) -> list[int]:
    if spec is None:
        return list(range(1, count + 1))
    out: list[int] = []
    for part in spec.split(","):
        a, _, b = part.partition("-")
        out.extend(range(int(a), int(b or a) + 1))
    return sorted(set(i for i in out if 1 <= i <= count))


@dataclass
class Prompter:
    interactive: bool = True
    preset_path: str | None = None
    preset_format: str | None = None
    preset_height: int | None = None
    preset_items: str | None = None
    raw_format: str | None = None
    log: Any = field(default=print)

    @classmethod
    def auto(cls, assume_yes: bool = False, **kw) -> "Prompter":
        return cls(interactive=sys.stdin.isatty() and not assume_yes, **kw)

    def _ask(self, question: str, default: str) -> str:
        if not self.interactive:
            return default
        try:
            ans = input(f"{question} [{default}]: ").strip()
        except EOFError:
            return default
        return ans or default

    def choice(self, question: str, options: list[tuple[str, str]], default: str) -> str:
        """options: [(key, label)]. Returns key."""
        if not self.interactive:
            return default
        print(question)
        for i, (key, label) in enumerate(options, 1):
            mark = "  (default)" if key == default else ""
            print(f"  {i:>2}) {label}{mark}")
        while True:
            ans = self._ask("Choose", str(next(i for i, (k, _) in enumerate(options, 1) if k == default)))
            if ans.isdigit() and 1 <= int(ans) <= len(options):
                return options[int(ans) - 1][0]
            if ans in dict(options):
                return ans
            print("  please enter one of the numbers above")

    def path(self, default: str, purpose: str = "Download folder") -> Path:
        raw = self.preset_path or self._ask(f"{purpose} (will be created if missing)", default)
        while True:
            p = Path(os.path.expandvars(os.path.expanduser(raw))).resolve()
            try:
                p.mkdir(parents=True, exist_ok=True)
                if os.access(p, os.W_OK):
                    return p
                msg = "not writable"
            except OSError as exc:
                msg = str(exc)
            if not self.interactive or self.preset_path:
                raise SystemExit(f"error: cannot use download folder {p}: {msg}")
            print(f"  cannot use {p}: {msg}")
            raw = self._ask(purpose, default)

    def format(self, default: str = "best") -> FormatChoice:
        if self.raw_format:
            return FormatChoice(raw=self.raw_format)
        key = self.preset_format or self.choice(
            "Download format:", [(k, v[2]) for k, v in FORMAT_PRESETS.items()], default)
        if key not in FORMAT_PRESETS:
            raise SystemExit(f"error: unknown format '{key}' (choose from {', '.join(FORMAT_PRESETS)})")
        kind, container, _ = FORMAT_PRESETS[key]
        fc = FormatChoice(kind=kind, container=container)
        if kind == "video":
            if self.preset_height is not None:
                fc.max_height = self.preset_height or None
            elif self.interactive and not self.preset_format:
                h = self.choice("Maximum resolution:", [(x, "best available" if x == "best" else f"{x}p") for x in HEIGHTS],
                                "best")
                fc.max_height = None if h == "best" else int(h)
        return fc

    def items(self, count: int, default: str = "all") -> str | None:
        spec = self.preset_items or self._ask(f"Which items? 'all' or e.g. 1,3,5-8 (1-{count})", default)
        while True:
            try:
                return parse_items(spec, count)
            except ValueError as exc:
                if not self.interactive or self.preset_items:
                    raise SystemExit(f"error: {exc}")
                print(f"  {exc}")
                spec = self._ask("Which items?", default)


def safe_name(name: str, fallback: str = "download") -> str:
    s = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', "_", name).strip(" .")
    return s[:120] or fallback
