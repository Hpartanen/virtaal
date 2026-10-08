"""A review window on Virtaal's core, without GTK.

Virtaal's model and state code subclasses GObject only for its signals, so
when PyGObject is not installed a small stand-in (`_gi`) takes its place.
Virtaal itself is imported from this checkout (the directory above
`review/`), not installed: its own dependencies are not needed here.
"""
import os
import sys

try:
    import gi  # noqa: F401  - a real PyGObject, when present, is used as is
except ImportError:
    from . import _gi

    _gi.install()

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)
