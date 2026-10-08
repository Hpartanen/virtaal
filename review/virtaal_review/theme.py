"""Light, dark or the system's choice, on the Sun Valley ttk theme (sv-ttk).

sv-ttk styles the ttk widgets and gives plain Tk widgets one palette; the
text boxes get their own colours here, and on Windows the title bar follows.
The choice is kept beside Virtaal's own settings.
"""
import json
import os
import sys
from tkinter import ttk

import sv_ttk

# Shown in the window, in this order; "system" is the default.
CHOICES = {"Järjestelmä": "system", "Vaalea": "light", "Tumma": "dark"}

# Text boxes: (editable background, read-only background, border).
TEXT_COLOURS = {"light": ("#ffffff", "#f3f3f3", "#e0e0e0"), "dark": ("#2b2b2b", "#232323", "#3a3a3a")}
# The selected list row, (background, text): sv-ttk's accent colour, as on its
# check and radio buttons, so the row stands out with or without focus.
SELECTED_ROW = {"light": ("#005fb8", "#ffffff"), "dark": ("#57c8ff", "#000000")}
# Rows a decision just changed, for a moment.
FLASH = {"light": "#fff1b8", "dark": "#5c4a00"}


def system_mode():
    """Windows' app mode ("light" or "dark"); light elsewhere or when unknown."""
    if sys.platform != "win32":
        return "light"
    try:
        import winreg

        key = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
            return "light" if winreg.QueryValueEx(k, "AppsUseLightTheme")[0] else "dark"
    except OSError:
        return "light"


def _settings_path():
    from virtaal.common import pan_app

    return os.path.join(pan_app.get_config_dir(), "virtaal-review.json")


def load_choice():
    try:
        with open(_settings_path(), encoding="utf-8") as f:
            choice = json.load(f).get("theme")
    except (OSError, ValueError, AttributeError):
        return "system"
    return choice if choice in CHOICES.values() else "system"


def save_choice(choice):
    """Remember the choice; a failure only means it is not remembered."""
    try:
        with open(_settings_path(), "w", encoding="utf-8") as f:
            json.dump({"theme": choice}, f)
    except OSError:
        pass


def apply(root, choice, editable, readonly):
    """Switch the window to the chosen mode; returns the mode used."""
    mode = system_mode() if choice == "system" else choice
    sv_ttk.set_theme(mode, root)
    # sv-ttk recolours plain Tk widgets when the theme change is handled;
    # let that happen first, then give the text boxes their own colours.
    root.update_idletasks()
    background, foreground = SELECTED_ROW[mode]
    ttk.Style(root).map("Treeview", background=[("selected", background)], foreground=[("selected", foreground)])
    field, quiet, border = TEXT_COLOURS[mode]
    for text, background in [(t, field) for t in editable] + [(t, quiet) for t in readonly]:
        text.configure(background=background, highlightthickness=1, highlightbackground=border,
                       highlightcolor=border, relief="flat", borderwidth=4)
    _title_bar(root, mode == "dark")
    return mode


def _title_bar(root, dark):
    """Ask Windows (10 20H1 and later) for a dark or light title bar."""
    if sys.platform != "win32":
        return
    import ctypes

    root.update_idletasks()
    hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
    value = ctypes.c_int(1 if dark else 0)
    ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(value), ctypes.sizeof(value))
    # The frame is repainted only when it changes; a one-pixel nudge does that.
    width, height = root.winfo_width(), root.winfo_height()
    if width > 1:
        root.geometry(f"{width + 1}x{height}")
        root.update_idletasks()
        root.geometry(f"{width}x{height}")
