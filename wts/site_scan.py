"""Security analysis of the web site hosting the videos (blue-team view)."""
from __future__ import annotations

import ipaddress
import re
import socket
import ssl
import time
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from .av import antivirus_scan
from .discovery import DEFAULT_UA, site_key
from .models import Finding, PageResult
from .signatures import (AGGRESSIVE_AD_NETWORKS, CRYPTOMINER_INDICATORS, EXECUTABLE_EXT_RE,
                         SUSPICIOUS_TLDS)

JS_RULES: list[tuple[re.Pattern, str, str, str]] = [
    (re.compile(r"eval\s*\(\s*(atob|unescape|decodeURIComponent|String\.fromCharCode)\s*\(", re.I), "high",
     "obfuscation", "eval() of decoded string"),
    (re.compile(r"eval\s*\(\s*function\s*\(\s*p\s*,\s*a\s*,\s*c\s*,\s*k\s*,\s*e\s*,\s*[dr]\s*\)"), "medium",
     "obfuscation", "Dean-Edwards packed JavaScript (p,a,c,k,e,d)"),
    (re.compile(r"(String\.fromCharCode\(\s*\d+\s*(,\s*\d+\s*){40,}\))"), "medium", "obfuscation",
     "long String.fromCharCode() chain"),
    (re.compile(r"document\.write\s*\(\s*(unescape|atob)\s*\(", re.I), "high", "obfuscation",
     "document.write() of decoded content"),
    (re.compile(r"(\\x[0-9a-f]{2}){60,}", re.I), "medium", "obfuscation", "long hex-escaped string"),
    (re.compile(r"var\s+_0x[0-9a-f]{4,6}\s*=\s*\[", re.I), "medium", "obfuscation",
     "javascript-obfuscator style _0x variable table"),
    (re.compile(r"[A-Za-z0-9+/]{3000,}={0,2}"), "low", "obfuscation", "very large base64 blob"),
    (re.compile(r"(top|window|document)\.location(\.href)?\s*=\s*[^=]", re.I), "low", "redirect",
     "script-driven redirect"),
    (re.compile(r"setTimeout\s*\([^)]{0,200}location", re.I), "medium", "redirect", "timed redirect"),
    (re.compile(r"window\.open\s*\(", re.I), "low", "popup", "window.open() (pop-up / pop-under)"),
    (re.compile(r"addEventListener\s*\(\s*['\"](click|mousedown|touchstart)['\"][^)]{0,300}window\.open", re.I | re.S),
     "high", "popup", "click-jacking pop-under (window.open in click handler)"),
    (re.compile(r"navigator\.clipboard\.writeText|execCommand\s*\(\s*['\"]copy", re.I), "medium", "clipboard",
     "writes to clipboard (clipboard-hijack / ClickFix lure)"),
    (re.compile(r"(powershell|cmd\s*/c|mshta|Win\s*\+\s*R|Windows\s*\+\s*R)", re.I), "high", "social-engineering",
     "instructs/copies OS commands (ClickFix-style attack)"),
    (re.compile(r"Notification\.requestPermission|pushManager\.subscribe", re.I), "medium", "push-spam",
     "requests push-notification permission"),
    (re.compile(r"serviceWorker\.register", re.I), "low", "persistence", "registers a service worker"),
    (re.compile(r"setInterval\s*\(\s*function\s*\(\s*\)\s*\{\s*debugger", re.I), "medium", "anti-analysis",
     "debugger trap (anti-devtools)"),
    (re.compile(r"devtools-?detect|disable-devtool|devtoolsDetector", re.I), "medium", "anti-analysis",
     "devtools detection library"),
    (re.compile(r"contextmenu['\"]?\s*,\s*[^)]*preventDefault", re.I), "info", "anti-analysis",
     "right-click disabled"),
    (re.compile(r"new\s+WebSocket\s*\(", re.I), "info", "network", "opens WebSocket connections"),
    (re.compile(r"WebAssembly\.(instantiate|compile)", re.I), "low", "wasm", "loads WebAssembly"),
    (re.compile(r"document\.cookie\s*=", re.I), "info", "tracking", "sets cookies from script"),
    (re.compile(r"navigator\.(sendBeacon)|fingerprintjs|canvas\.toDataURL", re.I), "info", "tracking",
     "browser fingerprinting / beacons"),
]

SOCIAL_ENGINEERING_RE = re.compile(
    r"(update\s+(your\s+)?(flash|video\s+)?player|install\s+(the\s+)?(codec|plugin|player)|download\s+(hd\s+)?player|"
    r"your\s+(pc|computer|device)\s+is\s+infected|virus(es)?\s+detected|verify\s+you\s+are\s+(human|not\s+a\s+robot)"
    r"[^.]{0,80}(press|win|ctrl)|click\s+allow\s+to\s+(continue|watch|verify)|register\s+free\s+to\s+watch\s+hd)",
    re.I,
)

SECURITY_HEADERS = {
    "content-security-policy": "Content-Security-Policy",
    "strict-transport-security": "Strict-Transport-Security",
    "x-content-type-options": "X-Content-Type-Options",
    "x-frame-options": "X-Frame-Options",
    "referrer-policy": "Referrer-Policy",
}


def _host_is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def tls_info(url: str, timeout: int = 10) -> tuple[dict[str, Any], list[Finding]]:
    p = urlparse(url)
    findings: list[Finding] = []
    if p.scheme != "https":
        return {}, [Finding("medium", "transport", "Site is served over plain HTTP (no TLS)")]
    host, port = p.hostname, p.port or 443
    info: dict[str, Any] = {}
    ctx = ssl.create_default_context()
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as ss:
                cert = ss.getpeercert()
                info["version"] = ss.version()
        info["subject"] = dict(x[0] for x in cert.get("subject", []))
        info["issuer"] = dict(x[0] for x in cert.get("issuer", []))
        info["not_after"] = cert.get("notAfter")
        info["san"] = [v for k, v in cert.get("subjectAltName", []) if k == "DNS"][:50]
        exp = ssl.cert_time_to_seconds(cert["notAfter"])
        start = ssl.cert_time_to_seconds(cert["notBefore"])
        if exp - time.time() < 7 * 86400:
            findings.append(Finding("low", "transport", "TLS certificate expires within 7 days"))
        if time.time() - start < 14 * 86400:
            findings.append(Finding("low", "transport", "TLS certificate issued less than 14 days ago (fresh infra)"))
        if len(info["san"]) > 30:
            findings.append(Finding("info", "transport", f"Certificate covers {len(info['san'])}+ names (shared/mirror infra)"))
    except ssl.SSLCertVerificationError as exc:
        findings.append(Finding("high", "transport", "TLS certificate failed verification", str(exc)[:200]))
    except (OSError, ssl.SSLError) as exc:
        findings.append(Finding("info", "transport", "Could not inspect TLS certificate", str(exc)[:200]))
    return info, findings


def _fetch_scripts(pages: list[PageResult], evidence: Path, max_scripts: int, timeout: int) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for pg in pages:
        for entry in pg.network:
            if entry.get("type") == "script" and entry["url"].startswith("http") and entry["url"] not in seen:
                seen.add(entry["url"])
        soup = BeautifulSoup(pg.rendered_html or pg.raw_html, "html.parser")
        for s in soup.find_all("script", src=True):
            u = urljoin(pg.final_url or pg.url, s["src"])
            if u.startswith("http"):
                seen.add(u)
    scripts_dir = evidence / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)

    def get(u: str) -> str | None:
        try:  # TLS verified; at most 3 MB per script (decompressed)
            with requests.get(u, headers={"User-Agent": DEFAULT_UA}, timeout=timeout, stream=True) as r:
                return r.raw.read(3_000_000, decode_content=True).decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            return None

    from concurrent.futures import ThreadPoolExecutor

    urls = sorted(seen)[:max_scripts]
    with ThreadPoolExecutor(max_workers=8) as ex:  # fetched in parallel, results kept in order
        bodies = list(ex.map(get, urls))
    for i, (u, body) in enumerate(zip(urls, bodies)):
        if body is None:
            continue
        (scripts_dir / f"{i:03d}.js").write_text(f"// source: {u}\n{body}")
        out.append((u, body))
    return out


def scan_site(pages: list[PageResult], evidence: Path, timeout: int = 20, max_scripts: int = 60,
              use_av: bool = True) -> dict[str, Any]:
    findings: list[Finding] = []
    main = pages[0]
    target = main.final_url or main.url
    tsite = site_key(target)
    report: dict[str, Any] = {"url": main.url, "final_url": target, "status": main.status}

    # transport / redirects ---------------------------------------------------
    tinfo, tf = tls_info(target)
    report["tls"] = tinfo
    findings += tf
    report["redirects"] = main.redirects
    if main.redirects:
        hosts = {urlparse(u).hostname for u in main.redirects + [target]}
        if len(hosts) > 1:
            findings.append(Finding("medium" if len(hosts) > 2 else "low", "redirect",
                                    f"Cross-domain redirect chain through {len(hosts)} hosts", " -> ".join(main.redirects + [target])))
    host = urlparse(target).hostname or ""
    if _host_is_ip(host):
        findings.append(Finding("medium", "domain", "Site is addressed by raw IP"))
    if host.startswith("xn--") or ".xn--" in host:
        findings.append(Finding("medium", "domain", "Punycode/IDN domain (possible homograph)", host))
    if host.rsplit(".", 1)[-1] in SUSPICIOUS_TLDS:
        findings.append(Finding("low", "domain", f"Domain uses a TLD frequently abused for malicious sites (.{host.rsplit('.', 1)[-1]})"))

    # headers ---------------------------------------------------------------
    missing = [name for key, name in SECURITY_HEADERS.items() if key not in main.headers]
    if missing:
        findings.append(Finding("info", "headers", "Missing security headers: " + ", ".join(missing)))
    report["server"] = main.headers.get("server", "")

    # network behaviour -------------------------------------------------------
    all_net = [e for pg in pages for e in pg.network]
    third = sorted({urlparse(e["url"]).hostname or "" for e in all_net
                    if e["url"].startswith("http") and site_key(e["url"]) != tsite})
    report["third_party_hosts"] = third
    report["request_count"] = len(all_net)
    ads = sorted({h for h in third for ad in AGGRESSIVE_AD_NETWORKS if ad in h})
    joined_net = " ".join(e["url"] for e in all_net).lower()
    for pg in pages:
        joined_net += " " + (pg.rendered_html or pg.raw_html or "").lower()[:3_000_000]
    for ad in AGGRESSIVE_AD_NETWORKS:
        if ad in joined_net and not any(ad in h for h in ads):
            ads.append(ad)
    report["aggressive_ad_networks"] = sorted(set(ads))
    if ads:
        findings.append(Finding("high", "malvertising",
                                "Loads pop-under / malvertising-prone ad networks", ", ".join(sorted(set(ads)))[:500]))
    miners = sorted({m for m in CRYPTOMINER_INDICATORS if m in joined_net})
    if miners:
        findings.append(Finding("critical", "cryptominer", "In-browser cryptominer indicators", ", ".join(miners)))
    if main.headers and target.startswith("https"):
        mixed = sorted({e["url"] for e in all_net if e["url"].startswith("http://")})
        if mixed:
            findings.append(Finding("low", "transport", f"{len(mixed)} insecure (http) sub-resources on https page",
                                    "\n".join(mixed[:10])))
    exe = sorted({e["url"] for e in all_net if EXECUTABLE_EXT_RE.search(urlparse(e["url"]).path)
                  and not urlparse(e["url"]).path.endswith((".js", ".jsx"))}
                 | {e["url"] for e in all_net if "application/x-msdownload" in e.get("content_type", "")
                    or "application/vnd.android.package-archive" in e.get("content_type", "")
                    or "application/x-msi" in e.get("content_type", "")})
    if exe:
        findings.append(Finding("critical", "drive-by", "Page requested executable/installer files", "\n".join(exe[:10])))
    downloads = [d for pg in pages for d in pg.downloads]
    if downloads:
        findings.append(Finding("critical", "drive-by", f"Page attempted {len(downloads)} automatic download(s) (blocked)",
                                "\n".join(f"{d['filename']} <- {d['url']}" for d in downloads[:10])))
    popups = sorted({p for pg in pages for p in pg.popups if p and p != "about:blank"})
    report["popups"] = popups
    if popups:
        findings.append(Finding("high", "popup", f"{len(popups)} pop-up / pop-under / tab-hijack navigations observed",
                                "\n".join(popups[:15])))
    dialogs = [d for pg in pages for d in pg.dialogs]
    if dialogs:
        findings.append(Finding("medium", "dialog", "Page opened JavaScript dialogs", "\n".join(dialogs[:5])))
    ip_reqs = sorted({urlparse(e["url"]).hostname for e in all_net
                      if e["url"].startswith("http") and _host_is_ip(urlparse(e["url"]).hostname or "")})
    if ip_reqs:
        findings.append(Finding("medium", "network", "Requests to raw IP addresses", ", ".join(ip_reqs[:10])))

    # markup -----------------------------------------------------------------
    for pg in pages:
        html = pg.rendered_html or pg.raw_html
        soup = BeautifulSoup(html or "", "html.parser")
        for fr in soup.find_all("iframe"):
            style = (fr.get("style") or "").replace(" ", "").lower()
            w, h = str(fr.get("width", "")), str(fr.get("height", ""))
            if w in ("0", "1") or h in ("0", "1") or "display:none" in style or "visibility:hidden" in style:
                findings.append(Finding("high", "hidden-iframe", "Hidden/zero-size iframe", (fr.get("src") or "")[:300]))
        for a in soup.find_all("a", href=True):
            if EXECUTABLE_EXT_RE.search(urlparse(a["href"]).path) and not a["href"].endswith(".js"):
                findings.append(Finding("high", "malicious-link", "Link to executable/installer file",
                                        f"{a.get_text(strip=True)[:80]} -> {a['href'][:300]}"))
        text = soup.get_text(" ", strip=True)
        for m in list(SOCIAL_ENGINEERING_RE.finditer(text))[:5]:
            findings.append(Finding("high", "social-engineering", "Social-engineering lure text on page", m.group(0)[:200]))
        meta = soup.find("meta", attrs={"http-equiv": re.compile("refresh", re.I)})
        if meta and "url=" in (meta.get("content") or "").lower():
            findings.append(Finding("low", "redirect", "Meta-refresh redirect", meta.get("content", "")[:200]))

    # scripts ----------------------------------------------------------------
    inline = []
    for pg in pages:
        soup = BeautifulSoup(pg.rendered_html or pg.raw_html or "", "html.parser")
        inline += [(f"{pg.url} (inline)", s.string or "") for s in soup.find_all("script") if not s.get("src")]
    external = _fetch_scripts(pages, evidence, max_scripts, timeout)
    report["scripts_analyzed"] = len(external) + len(inline)
    hits: dict[tuple[str, str], list[str]] = {}
    for src, body in inline + external:
        for rx, sev, cat, msg in JS_RULES:
            if rx.search(body):
                hits.setdefault((sev, f"{cat}|{msg}"), []).append(src)
    for (sev, key), srcs in hits.items():
        cat, msg = key.split("|", 1)
        findings.append(Finding(sev, cat, f"JavaScript: {msg} ({len(srcs)} script(s))", "\n".join(sorted(set(srcs))[:5])))

    if use_av:
        for f in sorted((evidence / "scripts").glob("*.js")):
            for fd in antivirus_scan(f):
                fd.evidence = f.read_text(errors="replace").splitlines()[0]
                findings.append(fd)

    report["findings"] = [f.to_dict() for f in findings]
    return report
