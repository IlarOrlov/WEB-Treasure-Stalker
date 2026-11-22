"""Optional antivirus engines: ClamAV, YARA, VirusTotal (hash lookup only, opt-in)."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from .models import Finding

RULES_DIR = Path(__file__).parent / "rules"
_status: dict[str, str] = {}


def engines_status() -> dict[str, str]:
    if not _status:
        _status["clamav"] = shutil.which("clamdscan") or shutil.which("clamscan") or "not installed"
        try:
            import yara  # noqa: F401
            _status["yara"] = "yara-python"
        except ImportError:
            _status["yara"] = shutil.which("yara") or "not installed"
    return _status


def clamav_scan(path: Path) -> list[Finding]:
    exe = shutil.which("clamdscan") or shutil.which("clamscan")
    if not exe:
        return []
    args = [exe, "--no-summary", "--infected"]
    if exe.endswith("clamdscan"):
        args.append("--fdpass")
    try:
        p = subprocess.run(args + [str(path)], capture_output=True, text=True, timeout=1800)
    except subprocess.TimeoutExpired:
        return [Finding("low", "antivirus", "ClamAV scan timed out")]
    if p.returncode == 1:
        sigs = [ln.split(":", 1)[-1].replace("FOUND", "").strip() for ln in p.stdout.splitlines() if "FOUND" in ln]
        return [Finding("critical", "antivirus", f"ClamAV detection: {', '.join(sigs) or 'infected'}")]
    if p.returncode not in (0, 1):
        return [Finding("info", "antivirus", "ClamAV could not scan the file", p.stderr.strip()[:300])]
    return []


def yara_scan(path: Path) -> list[Finding]:
    rule_files = sorted(RULES_DIR.glob("*.yar"))
    if not rule_files:
        return []
    matches: list[str] = []
    try:
        import yara

        rules = yara.compile(filepaths={f.stem: str(f) for f in rule_files})
        matches = [m.rule for m in rules.match(str(path), timeout=600)]
    except ImportError:
        exe = shutil.which("yara")
        if not exe:
            return []
        for rf in rule_files:
            p = subprocess.run([exe, "-w", str(rf), str(path)], capture_output=True, text=True, timeout=1800)
            matches += [ln.split()[0] for ln in p.stdout.splitlines() if ln.strip()]
    except Exception as exc:  # noqa: BLE001
        return [Finding("info", "yara", f"YARA scan failed: {exc}")]
    return [Finding("high", "yara", f"YARA rule matched: {m}") for m in matches]


def antivirus_scan(path: Path) -> list[Finding]:
    return clamav_scan(path) + yara_scan(path)


def virustotal_hash(sha256: str) -> dict | None:
    """Look up a file hash on VirusTotal (no upload). Needs VT_API_KEY; only called with --virustotal."""
    key = os.environ.get("VT_API_KEY")
    if not key:
        return None
    import requests

    r = requests.get(f"https://www.virustotal.com/api/v3/files/{sha256}", headers={"x-apikey": key}, timeout=30)
    if r.status_code == 404:
        return {"known": False}
    r.raise_for_status()
    stats = r.json()["data"]["attributes"].get("last_analysis_stats", {})
    return {"known": True, "stats": stats}


def virustotal_url(url: str) -> dict | None:
    key = os.environ.get("VT_API_KEY")
    if not key:
        return None
    import base64

    import requests

    uid = base64.urlsafe_b64encode(url.encode()).decode().strip("=")
    r = requests.get(f"https://www.virustotal.com/api/v3/urls/{uid}", headers={"x-apikey": key}, timeout=30)
    if r.status_code == 404:
        return {"known": False}
    r.raise_for_status()
    return {"known": True, "stats": r.json()["data"]["attributes"].get("last_analysis_stats", {})}
