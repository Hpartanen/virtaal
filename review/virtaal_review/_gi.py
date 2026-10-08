"""Just enough of PyGObject for Virtaal's models and controllers to import.

They use GObject for signals, GLib for timers and Gtk once, for a default
font name at import time. Nothing here draws anything.
"""
import sys
import types


class _GObject:
    def __init__(self, *args, **kwargs):
        self._handlers = {}

    def connect(self, name, func, *extra):
        self._handlers.setdefault(name, []).append((func, extra))
        return len(self._handlers[name])

    def emit(self, name, *args):
        for func, extra in getattr(self, "_handlers", {}).get(name, []):
            func(self, *args, *extra)


class _SignalFlags:
    RUN_FIRST = 1
    RUN_LAST = 2


class _Label:
    def get_pango_context(self):
        return self

    def get_font_description(self):
        return self

    def to_string(self):
        return "Sans 10"


def _module(name, **attrs):
    module = types.ModuleType(name)
    module.__dict__.update(attrs)
    return module


def install():
    repository = _module(
        "gi.repository",
        GObject=_module(
            "gi.repository.GObject",
            GObject=_GObject,
            SignalFlags=_SignalFlags,
            TYPE_PYOBJECT=object,
            signal_list_names=lambda type_: list(type_.__dict__.get("__gsignals__", {})),
        ),
        # The review window saves and moves on itself; Virtaal's timers are not run.
        GLib=_module("gi.repository.GLib", timeout_add=lambda *a: 0, idle_add=lambda *a: 0),
        Gtk=_module("gi.repository.Gtk", Label=_Label),
    )
    gi = _module("gi", repository=repository, require_version=lambda *a: None)
    sys.modules["gi"] = gi
    sys.modules["gi.repository"] = repository
    for name in ("GObject", "GLib", "Gtk"):
        sys.modules[f"gi.repository.{name}"] = getattr(repository, name)
