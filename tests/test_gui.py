"""GUI smoke tests (offscreen Qt)."""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")


@pytest.fixture(scope="module")
def window(tmp_path_factory):
    os.environ["XDG_DATA_HOME"] = str(tmp_path_factory.mktemp("xdg"))
    from PySide6.QtWidgets import QApplication

    from wts.gui.app import MainWindow

    app = QApplication.instance() or QApplication([])
    w = MainWindow(app)
    w.show()
    yield w
    w.close()


def test_pages_and_theme(window):
    for i in range(4):
        window.go(i)
        assert window.stack.currentIndex() == i
    before = window.theme
    window.toggle_theme()
    assert window.theme != before
    window.toggle_theme()


def test_youtube_channel_message_without_network(window):
    from wts.platforms import Inspection, classify

    g = window.grab_page
    t = classify("https://www.youtube.com/@SomeChannel")
    g._inspected(("platform", Inspection(t, ok=False, message="This YouTube page has no main video")))
    assert g.stack.currentWidget() is g.pages["message"]
    assert not g.options.isVisible()


def test_playlist_selection(window):
    from wts.platforms import Inspection, classify

    g = window.grab_page
    t = classify("https://www.youtube.com/playlist?list=PL123")
    ins = Inspection(t, ok=True, title="PL", playlist_title="PL",
                     entries=[{"title": f"Video {i}", "duration": 60} for i in range(1, 6)])
    g._inspected(("platform", ins))
    assert g.stack.currentWidget() is g.pages["preview"]
    assert g._checked() == [1, 2, 3, 4, 5]
    g._set_checks(False)
    assert g._checked() == [] and not g.options.btn.isEnabled()
    g.filter.setText("Video 3")
    g._set_checks(True)
    assert g._checked() == [3]
