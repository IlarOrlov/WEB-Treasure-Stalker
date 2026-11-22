"""Command-line interface."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .platforms import classify, run_platform
from .prompts import FORMAT_PRESETS, Prompter


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="wts",
        description="WEB-Treasure-Stalker: discover and download every video on a web site (best quality), scan the "
                    "site and the videos for malware/attacks, and flag likely piracy. YouTube and Spotify links are "
                    "handled directly (single item or playlist). For local blue-team analysis.",
    )
    p.add_argument("url", nargs="?", help="page, YouTube or Spotify URL to analyse")
    t = p.add_argument_group("setup")
    t.add_argument("--install", choices=["ffmpeg", "deno", "browser", "all"],
                   help="download a helper tool into the app folder and exit")
    t.add_argument("--check-tools", action="store_true", help="show which helper tools are available and exit")
    g = p.add_argument_group("download choices (asked interactively when omitted)")
    g.add_argument("-p", "--path", "-o", "--out", dest="path",
                   help="download folder; playlists/albums/series get their own sub-folder")
    g.add_argument("-f", "--format", dest="preset", choices=list(FORMAT_PRESETS),
                   help="output format: video (best, mp4, mkv, webm) or audio (audio, mp3, m4a, opus, flac, wav)")
    g.add_argument("--max-height", type=int, help="max video resolution, e.g. 1080 (0 = best)")
    g.add_argument("--items", help="playlist items: 'all' or e.g. 1,3,5-8")
    g.add_argument("-y", "--yes", action="store_true", help="never prompt; use flags or defaults")
    g.add_argument("--ytdl-format", help="advanced: raw yt-dlp format selector (overrides --format)")

    s = p.add_argument_group("web-site analysis")
    s.add_argument("--no-browser", action="store_true", help="static HTML only (no headless Chromium)")
    s.add_argument("--no-download", action="store_true", help="discover + scan site only, do not download videos")
    s.add_argument("--max-pages", type=int, default=25, help="max pages to visit (episode/season links) [25]")
    s.add_argument("--depth", type=int, default=1, help="link depth to follow for episode pages [1]")
    s.add_argument("--max-combinations", type=int, default=300,
                   help="max season/episode selector combinations per page [300]")
    s.add_argument("--max-playlist", type=int, default=50, help="max items per playlist embedded in a site [50]")
    s.add_argument("--subs", action="store_true", help="also download subtitles (scanned for payloads)")
    s.add_argument("--virustotal", action="store_true",
                   help="look up site URL and file hashes on VirusTotal (needs VT_API_KEY; sends URL+hashes, never files)")
    s.add_argument("--headful", action="store_true", help="show the browser window")

    c = p.add_argument_group("common")
    c.add_argument("--timeout", type=int, default=30, help="network timeout in seconds [30]")
    c.add_argument("--cookies", help="Netscape cookies.txt for content that needs a login (e.g. Instagram)")
    c.add_argument("--cookies-from-browser", metavar="BROWSER", help="reuse cookies from a local browser (chrome, firefox, ...)")
    c.add_argument("--deep", action="store_true", help="fully decode each video with ffmpeg to find malformed streams (slow)")
    c.add_argument("--no-av", action="store_true", help="skip ClamAV/YARA even if installed")
    c.add_argument("--save-report", action="store_true",
                   help="YouTube/Spotify: also save a JSON report in <folder>/.wts/ (off by default)")
    c.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    a = parser.parse_args(argv)
    from .tools import setup_environment

    setup_environment()
    if a.check_tools or a.install:
        return _tools_cmd(a)
    if not a.url:
        parser.error("a URL is required (run without arguments to open the GUI)")
    url = a.url.strip()
    if not url.startswith(("http://", "https://", "spotify:")):
        url = "https://" + url
    prompter = Prompter.auto(assume_yes=a.yes, preset_path=a.path, preset_format=a.preset,
                             preset_height=a.max_height, preset_items=a.items, raw_format=a.ytdl_format)
    try:
        target = classify(url)
        if target is not None:
            report = run_platform(target, prompter, cookies=a.cookies, cookies_from_browser=a.cookies_from_browser,
                                  use_av=not a.no_av, save_report=a.save_report)
            n = report["downloaded_files"]
            if n:
                print(f"\nDone: {n} file(s) in {report['destination']}  (highest media severity: "
                      f"{report['highest_severity'].upper()})")
            return 0 if n or not report["downloads"] else 1
        return _run_site(url, a, prompter)
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130


def _tools_cmd(a: argparse.Namespace) -> int:
    from . import tools

    def bar(done: int, total: int) -> None:
        if total:
            print(f"\r  {done / 1048576:6.1f} / {total / 1048576:.1f} MB", end="", flush=True)

    targets = ["ffmpeg", "deno", "browser"] if a.install == "all" else ([a.install] if a.install else [])
    for t in targets:
        print(f"Installing {t}…")
        try:
            if t == "ffmpeg":
                tools.install_ffmpeg(bar)
            elif t == "deno":
                tools.install_deno(bar)
            else:
                tools.install_browser(print)
            print(f"\n  {t}: done")
        except Exception as exc:  # noqa: BLE001
            print(f"\n  {t}: FAILED: {exc}")
            return 1
    for st in tools.status():
        print(f"  [{'ok' if st.found else '--'}] {st.name:28s} {st.detail}")
    return 0


def _run_site(url: str, a: argparse.Namespace, prompter: Prompter) -> int:
    from .pipeline import Options, run
    from .report import print_summary

    out = prompter.path("./output", "Output folder (a sub-folder per run is created)")
    choice = prompter.format("best") if not a.no_download else None
    opts = Options(out=Path(out), browser=not a.no_browser, download=not a.no_download, max_pages=a.max_pages,
                   depth=a.depth, max_combinations=a.max_combinations, timeout=a.timeout, cookies=a.cookies,
                   cookies_from_browser=a.cookies_from_browser, choice=choice, deep=a.deep, virustotal=a.virustotal,
                   headful=a.headful, subtitles=a.subs, av=not a.no_av, max_playlist=a.max_playlist)
    report, _ = run(url, opts)
    print(print_summary(report))
    return 2 if report["summary"]["highest_severity"] in ("critical", "high") else 0


if __name__ == "__main__":
    sys.exit(main())
