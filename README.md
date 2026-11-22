# WEB-Treasure-Stalker (`wts`)

A blue-team / OSINT tool that takes a link and:

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

## Install

Requires Python ≥ 3.10 and `ffmpeg`/`ffprobe`.

```bash
git clone https://github.com/IlarOrlov/WEB-Treasure-Stalker.git
cd WEB-Treasure-Stalker
python3 -m venv .venv && . .venv/bin/activate      # or: uv venv && uv pip install ...
pip install -e ".[dev]" "yt-dlp[default]"
python -m playwright install chromium

# optional but recommended
sudo apt install clamav clamav-daemon yara && sudo freshclam
pip install -e ".[yara]"
```

YouTube needs a JavaScript runtime for yt-dlp. `deno` is used if it is installed, otherwise `node` or `bun` is
detected automatically.

## Usage

```bash
# interactive: asks for folder, format, items
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
  evidence/            raw + rendered HTML, iframes, network log, scripts, screenshots, session cookies
```

Platform mode writes a JSON report to `<download folder>/.wts/`.

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
