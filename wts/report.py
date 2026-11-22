"""JSON + self-contained HTML report."""
from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

SEV_COLORS = {"critical": "#b91c1c", "high": "#dc2626", "medium": "#d97706", "low": "#2563eb", "info": "#6b7280"}
VERDICT_COLORS = {"LIKELY PIRATED": "#b91c1c", "SUSPICIOUS": "#d97706", "NO STRONG INDICATORS": "#15803d"}


def _e(x: Any) -> str:
    return html.escape(str(x if x is not None else ""))


def _findings_table(findings: list[dict[str, Any]]) -> str:
    if not findings:
        return "<p class='muted'>No findings.</p>"
    order = ["critical", "high", "medium", "low", "info"]
    rows = sorted(findings, key=lambda f: order.index(f["severity"]) if f["severity"] in order else 9)
    out = ["<table><tr><th>Severity</th><th>Category</th><th>Finding</th><th>Evidence</th></tr>"]
    for f in rows:
        c = SEV_COLORS.get(f["severity"], "#6b7280")
        out.append(f"<tr><td><span class='sev' style='background:{c}'>{_e(f['severity'])}</span></td>"
                   f"<td>{_e(f['category'])}</td><td>{_e(f['message'])}</td><td><pre>{_e(f.get('evidence', ''))}</pre></td></tr>")
    out.append("</table>")
    return "".join(out)


def _verdict(p: dict[str, Any]) -> str:
    c = VERDICT_COLORS.get(p["verdict"], "#6b7280")
    reasons = "".join(f"<li>{_e(r)}</li>" for r in p["reasons"]) or "<li class='muted'>no indicators</li>"
    return f"<div class='verdict' style='border-color:{c}'><b style='color:{c}'>{_e(p['verdict'])}</b> — score {p['score']}/100<ul>{reasons}</ul></div>"


def _series_html(s: dict[str, Any] | None) -> str:
    if not s:
        return ""
    seasons = ", ".join(f"{_e(k)}: {v}" for k, v in s.get("seasons", {}).items())
    missing = s.get("missing") or []
    return (f"<h2>Series: {_e(s['title'])}</h2><p>{s['episodes_found']} episodes found ({seasons}) · "
            f"{s.get('episodes_selected', s['episodes_found'])} selected · <b>{s['episodes_downloaded']} downloaded</b> "
            f"to <code>videos/{_e(s['title'])}/Season N/Episode MM</code></p>"
            + (f"<details><summary>{len(missing)} selected but not downloaded</summary><pre>{_e(chr(10).join(missing))}</pre></details>"
               if missing else ""))


def render_html(r: dict[str, Any]) -> str:
    s = r["summary"]
    parts = [f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>WTS Report</title><style>
body{{font-family:system-ui,sans-serif;margin:0 auto;max-width:1200px;padding:16px;color:#111;background:#fafafa}}
h1{{margin-bottom:0}} h2{{margin-top:32px;border-bottom:1px solid #ddd}} table{{border-collapse:collapse;width:100%;font-size:14px}}
td,th{{border:1px solid #ddd;padding:6px;vertical-align:top;text-align:left}} th{{background:#f0f0f0}}
pre{{white-space:pre-wrap;word-break:break-all;margin:0;font-size:12px;max-height:160px;overflow:auto}}
.sev{{color:#fff;padding:2px 6px;border-radius:4px;font-size:12px;text-transform:uppercase}}
.verdict{{border-left:6px solid;padding:8px 12px;background:#fff;margin:8px 0}} .muted{{color:#6b7280}}
.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:8px}}
.card{{background:#fff;border:1px solid #ddd;padding:10px;border-radius:6px}} .card b{{font-size:20px;display:block}}
code{{word-break:break-all}}
</style></head><body>
<h1>WEB-Treasure-Stalker report</h1><p class="muted">Target: <code>{_e(r['target'])}</code> · v{_e(r['version'])}</p>
<div class="cards">
<div class="card"><b>{s['pages_analyzed']}</b>pages analysed</div>
<div class="card"><b>{s['candidates']}</b>video candidates</div>
<div class="card"><b>{s['downloaded_files']}</b>files downloaded</div>
<div class="card"><b>{s['drm_protected']}</b>DRM-protected (skipped)</div>
<div class="card"><b style="color:{SEV_COLORS[s['highest_severity']]}">{_e(s['highest_severity']).upper()}</b>highest severity</div>
<div class="card"><b style="color:{VERDICT_COLORS.get(s['piracy_verdict'])}">{s['piracy_score']}</b>{_e(s['piracy_verdict'])}</div>
</div>
{_series_html(r.get('series'))}
<h2>Piracy assessment (site)</h2>{_verdict(r['piracy'])}
<p class="muted">Heuristic analyst aid, not a legal determination.</p>
<h2>Site security</h2>
<p>Final URL: <code>{_e(r['site'].get('final_url'))}</code> · server: {_e(r['site'].get('server'))} ·
requests: {_e(r['site'].get('request_count'))} · scripts analysed: {_e(r['site'].get('scripts_analyzed'))}</p>
{_findings_table(r['site']['findings'])}
<details><summary>Third-party hosts ({len(r['site'].get('third_party_hosts', []))})</summary><pre>{_e(chr(10).join(r['site'].get('third_party_hosts', [])))}</pre></details>
<h2>Videos</h2>"""]
    if not r["downloads"]:
        parts.append("<p class='muted'>No downloads performed.</p>")
    for d in r["downloads"]:
        c = d["candidate"]
        title = d.get("info", {}).get("title") or c["url"]
        parts.append(f"<h3>{_e(c.get('label') + ' — ' if c.get('label') else '')}{_e(title)}</h3>"
                     f"<p>Status: <b>{_e(d['status'])}</b> · {_e(c['kind'])} via {_e(c['source'])} · <code>{_e(c['url'])}</code></p>")
        if d.get("error") and d["status"] != "downloaded":
            parts.append(f"<pre>{_e(d['error'])}</pre>")
        info = d.get("info", {})
        if info:
            q = " · ".join(f"{k}: {_e(info[k])}" for k in ("extractor", "width", "height", "fps", "vcodec", "acodec", "duration")
                           if k in info)
            parts.append(f"<p class='muted'>{q}</p>")
        if d.get("piracy"):
            parts.append(_verdict(d["piracy"]))
        for m in d.get("media", []):
            parts.append(f"<p><code>{_e(m['file'])}</code><br>sha256 {_e(m['hashes']['sha256'])} · {m['size']:,} bytes · "
                         f"detected {_e(m['detected_type'])}{' · duplicate of ' + _e(m['duplicate_of']) if m.get('duplicate_of') else ''}</p>")
            parts.append(_findings_table(m["findings"]))
    parts.append("<h2>Candidates</h2><table><tr><th>Kind</th><th>Source</th><th>Label</th><th>URL</th></tr>")
    for c in r["candidates"]:
        parts.append(f"<tr><td>{_e(c['kind'])}</td><td>{_e(c['source'])}</td><td>{_e(c['label'])}</td><td><code>{_e(c['url'])}</code></td></tr>")
    parts.append("</table>")
    parts.append(f"<h2>Engines</h2><pre>{_e(json.dumps(r['engines'], indent=1))}</pre></body></html>")
    return "".join(parts)


def write_reports(report: dict[str, Any], run_dir: Path) -> None:
    (run_dir / "report.json").write_text(json.dumps(report, indent=2, default=str))
    (run_dir / "report.html").write_text(render_html(report))


def print_summary(r: dict[str, Any]) -> str:
    s = r["summary"]
    lines = [
        "",
        "=" * 70,
        f"Target            : {r['target']}",
        f"Pages analysed    : {s['pages_analyzed']}",
        f"Video candidates  : {s['candidates']}",
        f"Files downloaded  : {s['downloaded_files']}   (DRM-protected skipped: {s['drm_protected']})",
        f"Highest severity  : {s['highest_severity'].upper()}  ({s['security_findings']} findings)",
        f"PIRACY VERDICT    : {s['piracy_verdict']}  (score {s['piracy_score']}/100)",
    ]
    for reason in r["piracy"]["reasons"]:
        lines.append(f"    {reason}")
    serious = [f for f in r["site"]["findings"] if f["severity"] in ("critical", "high")]
    serious += [f for d in r["downloads"] for m in d.get("media", []) for f in m["findings"]
                if f["severity"] in ("critical", "high")]
    if serious:
        lines.append("Serious findings  :")
        for f in serious[:25]:
            lines.append(f"  [{f['severity'].upper()}] {f['category']}: {f['message']}")
    lines.append(f"Report            : {r['run_dir']}/report.html")
    lines.append("=" * 70)
    return "\n".join(lines)
