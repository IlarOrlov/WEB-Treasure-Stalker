import functools
import http.server
import shutil
import subprocess
import threading
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "site"

COLORS = {"clip1": "red", "clip2": "blue", "s1e1": "green", "s1e2": "yellow", "s2e1": "purple"}


def make_clip(path: Path, color: str) -> None:
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c={color}:s=320x240:d=1:r=10",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=1", "-shortest",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-movflags", "+faststart", str(path)],
        check=True,
    )


@pytest.fixture(scope="session")
def clip(tmp_path_factory) -> Path:
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not installed")
    p = tmp_path_factory.mktemp("clip") / "clean.mp4"
    make_clip(p, "red")
    return p


@pytest.fixture(scope="session")
def site_url(tmp_path_factory):
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not installed")
    root = tmp_path_factory.mktemp("site")
    shutil.copytree(FIXTURES, root, dirs_exist_ok=True)
    (root / "media").mkdir(exist_ok=True)
    for name, color in COLORS.items():
        make_clip(root / "media" / f"{name}.mp4", color)
    handler = functools.partial(_QuietHandler, directory=str(root))
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass
