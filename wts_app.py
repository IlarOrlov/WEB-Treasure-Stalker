"""PyInstaller entry script."""
import multiprocessing
import sys

from wts.__main__ import entry

if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(entry())
