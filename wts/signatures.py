"""Indicator lists shared by discovery, site scanning and piracy scoring.

All lists are heuristics. Extend them as you learn new hosts or indicators.
"""
from __future__ import annotations

import re

# Video file hosts and "embed aggregators" that are overwhelmingly used to
# stream unlicensed films/series.
PIRATE_VIDEO_HOSTS = [
    "streamtape", "strtape", "stape", "doodstream", "dood.", "ds2play", "d0000d", "dooood",
    "mixdrop", "mixdrp", "filemoon", "voe.sx", "voe-", "vidmoly", "upstream.to", "streamwish",
    "swdyu", "wishembed", "vidcloud", "rabbitstream", "megacloud", "dokicloud", "mp4upload",
    "uqload", "streamlare", "vidoza", "fembed", "hqq.", "netu.", "waaw.", "streamsb", "sbembed",
    "sbplay", "vidsrc", "2embed", "embedsu", "multiembed", "autoembed", "superembed", "vidplay",
    "mcloud", "filelions", "vidhide", "streamhide", "lulustream", "vtube", "vtbe", "supervideo",
    "dropload", "vidguard", "listeamed", "bembed", "vembed", "gogocdn", "gogo-stream",
    "goload", "playtaku", "anihdplay", "streamvid", "voe.", "mxdrop", "turboviplay", "emturbovid",
]

# Domain keywords typical of piracy portals.
PIRATE_DOMAIN_RE = re.compile(
    r"(\d*movies?\d*(hd|free|online|watch)|putlocker|fmovies|123movie|gomovies|solarmovie|yesmovies|"
    r"soap2day|watchseries|primewire|lookmovie|hdtoday|sflix|myflixer|flixtor|kimcartoon|gogoanime|"
    r"9anime|aniwatch|kissanime|kissasian|dramacool|torrent|yts\.|rarbg|thepiratebay|piratebay|"
    r"freestream|streamfree|bflix|cuevana|pelisplus|seriesflix|watchfree|freemovie|moviesjoy|"
    r"movie4k|losmovies|vumoo|popcornflix-free|tinyzone|zoechip|braflix|hianime|animepahe|"
    r"series9|watchcartoon|wcostream|projectfreetv|tubi-free|vidsrc|nunflix|flixhq|showbox|"
    r"sratim|watchsomuch)",
    re.I,
)

# Ad networks known for pop-unders, malvertising and forced redirects.
AGGRESSIVE_AD_NETWORKS = [
    "popads.net", "popcash.net", "propellerads", "propeller-tracking", "onclickads", "onclkds",
    "adsterra", "exoclick", "exosrv", "juicyads", "hilltopads", "adcash", "clickadu",
    "trafficjunky", "admaven", "ad-maven", "monetag", "galaksion", "richpush", "pushhouse",
    "evadav", "a-ads.com", "popmyads", "plugrush", "trafficstars", "adspyglass", "clickaine",
    "notifpush", "pushame", "zeroredirect", "betterads", "realsrv.com", "tsyndicate",
    "hpyrdr", "tumtzfl", "bngpt", "dolohen", "acint.net",
]

CRYPTOMINER_INDICATORS = [
    "coinhive", "coin-hive", "authedmine", "cryptoloot", "crypto-loot", "coinimp", "webminepool",
    "jsecoin", "cryptonight", "minero.cc", "monerominer", "webmine", "deepminer", "coinhave",
    "ppoi.org", "projectpoi", "miner.start(", "new client.anonymous(",
]

OFFICIAL_PLATFORMS = [
    "youtube.com", "youtu.be", "vimeo.com", "instagram.com", "facebook.com", "fb.watch",
    "tiktok.com", "twitter.com", "x.com", "dailymotion.com", "twitch.tv", "netflix.com",
    "hulu.com", "disneyplus.com", "primevideo.com", "amazon.com", "max.com", "hbomax.com",
    "paramountplus.com", "peacocktv.com", "apple.com", "tv.apple.com", "bbc.co.uk", "crunchyroll.com",
    "pluto.tv", "tubitv.com", "roku.com", "streamable.com", "rumble.com", "reddit.com",
    "linkedin.com", "ted.com", "kan.org.il", "mako.co.il", "13tv.co.il", "yes.co.il", "hot.net.il",
]

# Links to recognised video platforms (anchors that are worth handing to yt-dlp).
PLATFORM_LINK_RE = re.compile(
    r"(youtube\.com/(watch|shorts|embed|live)|youtu\.be/|youtube-nocookie\.com/embed|vimeo\.com/(\d+|video/)|"
    r"player\.vimeo\.com|instagram\.com/(p|reel|reels|tv)/|tiktok\.com/(@[^/]+/video|embed)|"
    r"dailymotion\.com/(video|embed)|dai\.ly/|facebook\.com/.+/videos/|facebook\.com/(watch|reel)|fb\.watch/|"
    r"(twitter|x)\.com/[^/]+/status/|twitch\.tv/|clips\.twitch\.tv|streamable\.com/|rumble\.com/(v|embed)|"
    r"ok\.ru/video|vk\.com/video|bilibili\.com/video|v\.redd\.it/|loom\.com/share|fast\.wistia|"
    r"players\.brightcove|cdn\.jwplayer|kaltura\.com)",
    re.I,
)

PIRATE_HOST_RE = re.compile("|".join(re.escape(h) for h in PIRATE_VIDEO_HOSTS), re.I)

# Release naming patterns (scene/P2P) found in titles and file names.
RELEASE_NAME_RE = re.compile(
    r"(\b(2160p|1080p|720p|480p|4k|uhd)\b.*\b(web[-. ]?dl|webrip|bluray|blu-ray|brrip|bdrip|hdrip|dvdrip|"
    r"hdtv|hdcam|camrip|telesync|hdts|dvdscr)\b|\b(yify|yts(\.mx|\.am|\.lt)?|rarbg|eztv|ettv|psa|"
    r"tgx|torrentgalaxy|galaxyrg|qxr|ganool|pahe\.in|mkvcage|shaanig)\b|\bx26[45][-. ][a-z0-9]{2,}\b)",
    re.I,
)

CAM_RE = re.compile(r"\b(hdcam|camrip|cam|telesync|hdts|ts-?rip)\b", re.I)

PIRACY_PAGE_PHRASES = [
    (re.compile(r"watch\s+(it\s+)?(for\s+)?free|free\s+online|stream\s+free|free\s+streaming", re.I), 10,
     "page advertises free streaming"),
    (re.compile(r"full\s+(hd\s+)?(movie|episode)s?\s+(online|free)", re.I), 10, "page advertises full movies/episodes online"),
    (re.compile(r"no\s+(sign[- ]?up|registration|account)\s+(required|needed)", re.I), 5, "no-signup streaming claim"),
    (re.compile(r"(do(es)?\s+not|don'?t)\s+(host|store)\s+any\s+(files|videos|content)|"
                r"provided\s+by\s+non[- ]affiliated\s+third\s+part", re.I), 20,
     "'we do not host any files' piracy disclaimer"),
    (re.compile(r"\b(123movies|putlocker|fmovies|soap2day|gomovies|solarmovie)\b", re.I), 15,
     "mentions well-known piracy brand"),
    (re.compile(r"magnet:\?xt=urn:btih:|\.torrent\b", re.I), 20, "torrent / magnet links present"),
    (re.compile(r"(צפייה|לצפייה)\s+ישירה|סרטים\s+(מתורגמים|לצפייה)\s+(בחינם|ישירה)|ללא\s+הרשמה", re.I), 15,
     "Hebrew free-streaming phrases"),
]

EXECUTABLE_EXT_RE = re.compile(
    r"\.(exe|msi|scr|bat|cmd|com|pif|vbs|vbe|js|jse|wsf|hta|ps1|apk|dmg|pkg|jar|lnk|iso|img|appx|msix)(\?|#|$)",
    re.I,
)

SUSPICIOUS_TLDS = {
    "xyz", "top", "click", "monster", "rest", "cyou", "cfd", "sbs", "buzz", "icu", "quest", "lol",
    "zip", "mov", "work", "bar", "loan", "win", "bid", "gq", "tk", "ml", "cf", "ga",
}
