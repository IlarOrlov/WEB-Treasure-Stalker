# WEB-Treasure-Stalker

![icon](wts/gui/assets/icon.png)

A desktop app (GUI + CLI, Linux and Windows) for blue-team / OSINT work. Give it a link and it:

1. **Finds every video** on the page: `<video>` tags, iframes and embeds (Instagram reels, YouTube,
   Vimeo, TikTok, X, Facebook, and so on), og:video and JSON-LD data, JS player configs, and the HLS/DASH/MP4
   streams it sees in the network traffic of a headless Chromium. It also **walks season/episode selectors** (combo boxes,
   season tabs and episode buttons) so every episode's player gets loaded.
2. **Downloads them in the best quality the site offers**, using yt-dlp to merge the best video and audio tracks.
3. **Scans the site** for attacks: malvertising and pop-under networks, crypto-miners, drive-by downloads,
   links to executables, hidden iframes, obfuscated or packed JavaScript, ClickFix and clipboard hijacking,
   fake "update your player" lures, push-notification spam, anti-analysis tricks, suspicious redirects,
   TLS problems and missing security headers.
4. **Scans every downloaded file** for malware:
   - hashes (MD5, SHA-1, SHA-256)
   - checking the real content type against the extension (an `.exe` disguised as `.mp4`)
   - data appended after the end of the MP4, MKV or AVI container
   - executables, archives and scripts embedded in the file, and the EICAR test signature
   - suspicious ffprobe metadata and attachments, and malformed streams
   - ClamAV and YARA (bundled rules in `wts/rules/`) when installed
   - optional VirusTotal hash lookup
5. **Flags piracy** with an explainable 0–100 score per site and per video. Signals include piracy-portal
   domains, known pirate video hosts, "we do not host any files" disclaimers, scene release names,
   CAM rips and pop-under ads.

YouTube and Spotify links are handled directly, without crawling the page:

| Link | Behaviour |
|---|---|
| YouTube video (`watch`, `youtu.be`, `shorts`, `live`, `embed`) | Downloads **only the main video**. If the page has none (a channel, the home page, a search page, or a private/removed video), nothing is downloaded and the reason is shown. |
| YouTube playlist | Lists the items and asks for **all** or a selection (`1,3,5-8`). Saves into a folder named after the playlist. |
| YouTube video inside a playlist (`watch?v=…&list=…`) | Asks: only this video, the whole playlist, or chosen items. |
| Spotify track / album / playlist | Reads the track list from Spotify's public embed page and asks for all or a selection. Each track is **matched to its YouTube upload**, which is what gets downloaded. Albums and playlists get their own folder. |

> Spotify audio is DRM-protected. This tool never circumvents DRM (Spotify, Netflix, Widevine, PlayReady,
> FairPlay). DRM-protected streams found on sites are reported as `drm` and skipped.

Every download asks for the **destination folder** and the **format**:

- video: best (auto MP4/MKV), MP4, MKV or WebM, plus a maximum resolution
- audio: best original codec, MP3, M4A, Opus, FLAC or WAV

Series, playlists and site runs always get their own sub-folder.

## Download (no install)

Grab the single-file app from the [**Releases**](https://github.com/IlarOrlov/WEB-Treasure-Stalker/releases/latest) page:

| Platform | File |
|---|---|
| Windows 10/11 x64 | `WEB-Treasure-Stalker-windows-x64.exe` |
| Linux x64 (glibc ≥ 2.35: Ubuntu 22.04+, Debian 12+, Fedora 36+ …) | `WEB-Treasure-Stalker-linux-x64` (then run `chmod +x WEB-Treasure-Stalker-linux-x64`) |

Double-click it to open the GUI. On first run, open **Tools** and click **Install** for:

- **FFmpeg:** merges the best video and audio streams and converts formats
- **Chromium engine:** renders web sites, captures streams and walks episode menus
- **Deno:** optional; unlocks every YouTube format

Downloads are verified against their published SHA-256 checksums. Tools you already have (system FFmpeg,
Chrome, Edge, Node) are detected automatically.

The same file is also the CLI: `WEB-Treasure-Stalker <url> -y -p <folder> -f mp4` (see `--help`).
Use `--check-tools` to list the helper tools, or `--install ffmpeg|deno|browser|all` to install them.

> Windows SmartScreen may warn because the exe isn't code-signed: choose *More info → Run anyway*.
> Each release includes `SHA256SUMS.txt` for verification.

### The GUI

- **Grab & analyse:** paste a link and click Inspect. For YouTube and Spotify you get a preview with the thumbnail,
  the best available quality and a checkable track or episode list (filter, all, none, invert). For web sites you get
  the scan options. Then choose the folder, format and resolution, and click Download or Start analysis. Progress shows
  live per item, with speed, ETA and an activity log; Cancel works at any time.
- **Results:** the piracy score gauge with its reasons, stat tiles, a threats table filterable by severity,
  per-video security status, the located streams and the log. Buttons open the HTML report or the folder.
- **History:** previous runs. Double-click one to reopen its report or folder.
- **Tools:** status of FFmpeg, the JS runtime, the browser engine and ClamAV/YARA, with one-click installs.
- Dark and light themes.

## Build the executables yourself

```bash
pip install -e ".[dev]" "yt-dlp[default]" pyside6-essentials pyinstaller
pyinstaller packaging/wts.spec --noconfirm        # -> dist/WEB-Treasure-Stalker(.exe)
```

PyInstaller builds for the OS it runs on. Pushing a tag `vX.Y.Z` runs `.github/workflows/release.yml`, which
builds Linux and Windows on GitHub Actions, runs the tests, and publishes a release with both files and checksums.

## Install from source (CLI / development)


Requires Python ≥ 3.10 and `ffmpeg`/`ffprobe`.

```bash
git clone https://github.com/IlarOrlov/WEB-Treasure-Stalker.git
cd WEB-Treasure-Stalker
python3 -m venv .venv && . .venv/bin/activate      # or: uv venv && uv pip install ...
pip install -e ".[dev]" "yt-dlp[default]" pyside6-essentials
python -m playwright install chromium

# optional but recommended
sudo apt install clamav clamav-daemon yara && sudo freshclam
pip install -e ".[yara]"
```

YouTube needs a JavaScript runtime for yt-dlp. `deno` is used if it is installed, otherwise `node` or `bun` is
detected automatically.

## Usage

```bash
wts                       # no arguments: opens the GUI
# interactive CLI: asks for folder, format, items
wts "https://some-site.example/series/show"
wts "https://www.youtube.com/watch?v=jNQXAC9IVRw"
wts "https://www.youtube.com/playlist?list=PL..."
wts "https://open.spotify.com/album/..."

# non-interactive
wts "https://www.youtube.com/playlist?list=PL..." -y -p ~/Downloads/analysis --items 1-5 -f mp4 --max-height 1080
wts "https://open.spotify.com/playlist/..." -y -p ~/Music/case-42 -f flac
wts "https://news.example/article-with-instagram-reel" -y -p ./cases

# site only: scan, no downloads
wts "https://suspicious.example" --no-download -y
```

Main options (run `wts -h` for all):

| Option | Meaning |
|---|---|
| `-p/--path DIR` | download folder (asked if omitted) |
| `-f/--format` | `best mp4 mkv webm` (video) or `audio mp3 m4a opus flac wav` |
| `--max-height N` | maximum video resolution |
| `--items SPEC` | playlist items: `all` or `1,3,5-8` |
| `-y/--yes` | no prompts; use the flags or defaults |
| `--cookies FILE` / `--cookies-from-browser chrome` | for content that needs a login (Instagram, age-gated videos) |
| `--max-pages`, `--depth`, `--max-combinations` | crawl limits for episode pages and selectors |
| `--deep` | full ffmpeg decode of every file (slow, finds malformed streams) |
| `--virustotal` | VirusTotal URL and hash lookup (needs `VT_API_KEY`; sends the URL and hashes, never files) |
| `--headful` | show the browser |

Exit code (site mode): `2` if any high or critical finding, else `0`.

## Output (site mode)

```
<path>/<host>_<timestamp>/
  report.html          human-readable report (verdicts, findings, hashes)
  report.json          everything, machine-readable
  videos/              downloaded media (read-only, non-executable)
  evidence/            raw + rendered HTML, iframes, network log, scripts, screenshots
```

Platform mode saves only the media files. Add `--save-report` (CLI) for a JSON report in `<folder>/.wts/`.

## What the app writes to disk

Nothing is written without your command. In detail:

| When | What, where |
|---|---|
| You click **Download** / **Start analysis** | Only inside the folder shown in *Save to*: the media files (YouTube/Spotify; playlists and albums in a sub-folder), or one `<site>_<date-time>` sub-folder for site analysis (videos, report, evidence). Partial files are removed if you cancel. |
| You click **Install** on the Tools page (asks first, shows the path) | FFmpeg / Deno → `%LOCALAPPDATA%\WEB-Treasure-Stalker\bin` (Linux `~/.local/share/web-treasure-stalker/bin`); Chromium → `%LOCALAPPDATA%\ms-playwright` (Linux `~/.cache/ms-playwright`). |
| You tick **Remember preferences and history** (History page; off by default) | `prefs.json` and `history.json` in the app folder above. Unticking deletes them. |
| While the app runs | One private temp folder `%TEMP%\wts-session-*` (browser profile, helper caches, session cookies) plus PyInstaller's own `%TEMP%\_MEI*` unpack folder. **Both are deleted when the app closes.** If the app is force-killed (Task Manager → End task), they stay in `%TEMP%` until Windows or Disk Cleanup removes them. |

Nothing is written to the registry. yt-dlp's cache is disabled. Qt's internal settings and file-dialog state go into the session temp folder,
and the folder picker is Qt's own, so the Windows "recent folders" lists are not touched.
(Windows itself keeps general records for any program you run, such as Prefetch. That is outside the app's control.)

You can verify this with Sysinternals **Process Monitor**: filter *Process Name is WEB-Treasure-Stalker.exe*
and *Operation is WriteFile / CreateFile / RegSetValue*.

## Safety notes

- Analyse hostile sites from a **disposable VM or container**. Pages are rendered in headless Chromium, and
  ffprobe parses untrusted media, so parser exploits are possible.
- Service workers, notifications and automatic downloads are blocked in the browser. Pop-ups are recorded
  as evidence and then closed.
- Downloaded files are made read-only and non-executable. Mount the output folder `noexec` for extra safety.
- The piracy verdict is a heuristic analyst aid, not a legal determination. Read the listed reasons.

## Tests

```bash
pytest                          # offline: local fixture site, crafted malicious media, unit tests
WTS_NETWORK_TESTS=1 pytest      # adds live YouTube checks
```
