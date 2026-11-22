"""Full pipeline against a local fixture site (no internet needed)."""
from pathlib import Path

import pytest

from wts.pipeline import Options, run

pytestmark = pytest.mark.browser


def _have_browser() -> bool:
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            p.chromium.launch().close()
        return True
    except Exception:  # noqa: BLE001
        return False


@pytest.fixture(scope="module")
def result(site_url, tmp_path_factory):
    if not _have_browser():
        pytest.skip("Playwright Chromium not installed (python -m playwright install chromium)")
    out = tmp_path_factory.mktemp("out")
    report, run_dir = run(f"{site_url}/index.html", Options(out=out, max_pages=3, timeout=10, max_scripts=5, av=False),
                          log=lambda m: None)
    return report, run_dir


def test_all_episodes_and_embeds_downloaded(result):
    report, run_dir = result
    labels = {d["candidate"]["label"] for d in report["downloads"] if d["status"] == "downloaded"}
    assert {"Season 1 / Episode 1", "Season 1 / Episode 2", "Season 2 / Episode 1"} <= labels, labels
    sizes = {}
    for d in report["downloads"]:
        for m in d.get("media", []):
            sizes[m["hashes"]["sha256"]] = m["file"]
    # clip1, clip2, s1e1, s1e2, s2e1 are five distinct videos
    assert len(sizes) == 5, sizes
    for f in sizes.values():
        assert not Path(f).stat().st_mode & 0o111, "quarantined files must not be executable"


def test_site_security_findings(result):
    report, _ = result
    cats = {f["category"] for f in report["site"]["findings"]}
    assert {"malvertising", "hidden-iframe", "malicious-link", "obfuscation", "social-engineering"} <= cats, cats


def test_piracy_verdict(result):
    report, run_dir = result
    assert report["piracy"]["verdict"] == "LIKELY PIRATED", report["piracy"]
    assert (run_dir / "report.html").exists() and (run_dir / "report.json").exists()
