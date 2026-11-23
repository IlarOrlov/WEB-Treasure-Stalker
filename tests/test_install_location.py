"""User-chosen install folders for the Tools page (no binaries are downloaded here)."""
import os
import stat
import sys

import pytest

from wts import tools

posix = pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission checks")


@pytest.fixture()
def appdata(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "data"))
    return tmp_path


@posix
def test_private_folder_accepted_and_shared_folder_refused(appdata):
    good = appdata / "mytools"
    assert tools.check_install_dir(good, create=True) is None
    assert stat.S_IMODE(good.stat().st_mode) & 0o077 == 0
    shared = appdata / "shared"
    shared.mkdir()
    shared.chmod(0o777)
    assert "other users can write" in tools.check_install_dir(shared)
    assert tools.check_install_dir(appdata / "missing") is not None  # not created unless asked
    assert "absolute" in tools.check_install_dir(tools.Path("relative/dir"))


def test_session_temp_refused(appdata):
    assert "session temporary folder" in tools.check_install_dir(tools.session_temp())


@posix
def test_location_saved_privately_and_put_on_path(appdata, monkeypatch):
    folder = appdata / "bin2"
    tools.check_install_dir(folder, create=True)
    assert not tools.data_dir().exists()  # nothing written before the user installs somewhere
    tools.set_install_location("ffmpeg", folder)
    f = tools.data_dir() / "tool-locations.json"
    assert stat.S_IMODE(f.stat().st_mode) == 0o600
    assert tools.install_dir("ffmpeg") == folder.resolve()
    monkeypatch.setenv("PATH", "/usr/bin")
    tools.setup_environment()
    assert os.environ["PATH"].split(os.pathsep)[0] == str(folder.resolve())
    # back to the default: the file is removed again
    tools.set_install_location("ffmpeg", None)
    assert not f.exists()


@posix
def test_location_that_became_unsafe_is_ignored(appdata):
    folder = appdata / "bin3"
    tools.check_install_dir(folder, create=True)
    tools.set_install_location("js", folder)
    folder.chmod(0o777)  # someone opened it up later
    assert "js" not in tools.install_locations()
    folder.chmod(0o700)


@posix
def test_place_refuses_symlink(appdata):
    folder = appdata / "bin4"
    tools.check_install_dir(folder, create=True)
    (folder / "ffmpeg").symlink_to(appdata / "elsewhere")
    with pytest.raises(RuntimeError, match="symbolic link"):
        tools._place("ffmpeg", b"x", folder)
