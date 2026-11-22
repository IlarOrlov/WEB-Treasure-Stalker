"""Entry point: no arguments -> desktop GUI, otherwise the command-line interface."""
import os
import sys


def _attach_windows_console() -> None:
    """A windowed (GUI) exe has no console; reuse the parent terminal when run with CLI arguments."""
    if sys.platform != "win32" or sys.stdout is not None and getattr(sys.stdout, "isatty", lambda: False)():
        return
    try:
        import ctypes

        if ctypes.windll.kernel32.AttachConsole(-1):
            sys.stdout = open("CONOUT$", "w", encoding="utf-8", errors="replace", buffering=1)
            sys.stderr = open("CONOUT$", "w", encoding="utf-8", errors="replace", buffering=1)
            sys.stdin = open("CONIN$", "r", encoding="utf-8", errors="replace")
            print()
    except Exception:  # noqa: BLE001
        pass


def entry() -> int:
    args = sys.argv[1:]
    if not args or args == ["--gui"] or (len(args) == 1 and args[0].startswith("-psn_")):
        from .gui.app import main as gui_main

        return gui_main()
    _attach_windows_console()
    if sys.stdout is None:  # no console available at all
        sys.stdout = sys.stderr = open(os.devnull, "w")
    from .cli import main

    return main(args)


if __name__ == "__main__":
    sys.exit(entry())
