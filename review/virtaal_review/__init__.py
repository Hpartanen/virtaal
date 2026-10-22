"""A review window on Virtaal's core, without GTK.

Virtaal's model and state code subclasses GObject only for its signals, so
when PyGObject is not installed a small stand-in (`_gi`) takes its place.
In a checkout, Virtaal is imported from the directory above `review/`;
an installed wheel carries its own copy (see pyproject.toml). Either way
Virtaal's own dependencies are not needed here.
"""
import os
import sys

try:
    import gi  # noqa: F401  - a real PyGObject, when present, is used as is
except ImportError:
    from . import _gi

    _gi.install()

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if os.path.isdir(os.path.join(_REPO, "virtaal")) and _REPO not in sys.path:
    sys.path.insert(0, _REPO)
