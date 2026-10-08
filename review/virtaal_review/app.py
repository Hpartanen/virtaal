"""The review window: a string list on the left, the selected string on the right.

All file and state logic is in `session`; this module only shows it and
turns keys into calls. Saving happens shortly after each change, on Ctrl+S
and on close.

The list can be filtered (state, search text, strings like the selected one)
and sorted; several selected strings can be approved or rejected at once.
With "Niputa samat tekstit" on, strings that read the same are bundled under
one expandable row and share one translation and one decision.

Every decision can be undone (Ctrl+Z, Kumoa); rows a decision changed flash
briefly, and the status line keeps the last action apart from the save state.
"""
import argparse
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from . import theme
from .session import APPROVED, OPEN, REJECTED, RefusedFile, ReviewSession, plain, state_names

AUTOSAVE_MS = 1500
FLASH_MS = 1500
TOOLTIP_DELAY_MS = 500
HELP = """Näppäimet
Ctrl+Enter\tHyväksy ja siirry seuraavaan avoimeen
Ctrl+Shift+Enter\tHylkää ja siirry seuraavaan
Ctrl+Z\tKumoa viimeisin hyväksyntä tai hylkäys
Ctrl+↑ / Ctrl+↓\tEdellinen / seuraava rivi
Ctrl+A (listassa)\tValitse kaikki listan rivit
Ctrl+S\tTallenna heti (tallennus tapahtuu myös itsestään)
F1\tTämä ohje

Lista
Sarakkeen otsikko järjestää listan; uusi napsautus kääntää järjestyksen.
× kertoo, montako samaa tekstiä on. ≠ tarkoittaa, että niiden käännökset
eroavat nyt toisistaan.

Niputa samat tekstit
Samat tekstit näkyvät yhtenä rivinä, jonka alla ovat sen jäsenet.
Muokkaus, hyväksyntä ja hylkäys koskevat koko nippua: kaikki saavat
näytetyn käännöksen. Muotoillun tekstin muotoilu säilyy."""
MIN_SIZE = (1000, 560)
SEARCH_DELAY_MS = 250
FILTERS = [("Kaikki", None), ("Avoimet", OPEN), ("Hyväksytyt", APPROVED), ("Hylätyt", REJECTED)]
COLUMNS = [("n", "Nro", 45, False), ("state", "Tila", 100, False), ("repeats", "×", 30, False),
           ("source", "Lähde", 100, True), ("target", "Käännös", 100, True)]
# Swedish and Finnish put å, ä and ö after z, in that order.
# "{", "|" and "}" are the characters right after "z".
_ALPHABET = str.maketrans({"å": "{", "ä": "|", "æ": "|", "ö": "}", "ø": "}"})


def alphabetical(text):
    return text.casefold().translate(_ALPHABET)


class Tooltip:
    """A short explanation that appears when the pointer rests on a widget."""

    def __init__(self, widget, text):
        self.widget, self.text, self.job, self.tip = widget, text, None, None
        widget.bind("<Enter>", self.schedule, add="+")
        widget.bind("<Leave>", self.hide, add="+")
        widget.bind("<ButtonPress>", self.hide, add="+")

    def schedule(self, _event=None):
        self.hide()
        self.job = self.widget.after(TOOLTIP_DELAY_MS, self.show)

    def show(self):
        self.tip = tk.Toplevel(self.widget)
        self.tip.wm_overrideredirect(True)
        self.tip.wm_geometry(f"+{self.widget.winfo_rootx() + 8}+{self.widget.winfo_rooty() + self.widget.winfo_height() + 4}")
        ttk.Label(self.tip, text=self.text, padding=(8, 4), relief="solid", borderwidth=1).pack()

    def hide(self, _event=None):
        if self.job:
            self.widget.after_cancel(self.job)
            self.job = None
        if self.tip:
            self.tip.destroy()
            self.tip = None


class ReviewWindow:
    def __init__(self, root, session):
        self.root = root
        self.session = session
        self.names = state_names()
        self.current = None
        self.loading = False
        self.autosave_job = None
        self.search_job = None
        self.similar = None  # (index, set of indices) while "only similar" is on

        root.title(f"Tarkastus – {session.path}")
        root.geometry("1400x800")
        root.protocol("WM_DELETE_WINDOW", self.close)

        # Laid out to fit from MIN_SIZE up: two short toolbars, the counts in
        # the status line, and list and editor sharing the width 2:3 at any size.
        root.minsize(*MIN_SIZE)

        bottom = ttk.Frame(root, padding=(8, 0, 8, 6))
        bottom.pack(side="bottom", fill="x")  # packed first, so it stays when the window is short
        self.status = ttk.Label(bottom, anchor="w")
        self.status.pack(side="left", fill="x", expand=True)
        self.counts = ttk.Label(bottom)
        self.counts.pack(side="right")
        self.saved = ttk.Label(bottom)
        self.saved.pack(side="right", padx=(0, 16))
        self.shown = ttk.Label(bottom)
        self.shown.pack(side="right", padx=(0, 16))

        top = ttk.Frame(root, padding=(8, 8, 8, 0))
        top.pack(fill="x")
        ttk.Label(top, text="Näytä:").pack(side="left")
        self.filter = tk.StringVar(value=FILTERS[0][0])
        for label, _ in FILTERS:
            ttk.Radiobutton(top, text=label, value=label, variable=self.filter,
                            command=self.fill_list).pack(side="left", padx=4)
        self.theme_choice = theme.load_choice()
        self.theme_name = tk.StringVar(value=next(k for k, v in theme.CHOICES.items() if v == self.theme_choice))
        themes = ttk.Combobox(top, textvariable=self.theme_name, values=list(theme.CHOICES), state="readonly",
                              width=11)
        themes.pack(side="right")
        themes.bind("<<ComboboxSelected>>", lambda _: self.choose_theme())
        ttk.Label(top, text="Teema:").pack(side="right", padx=(16, 4))
        help_button = ttk.Button(top, text="?", width=3, command=self.help)
        help_button.pack(side="right")
        Tooltip(help_button, "Ohje ja näppäimet (F1)")
        self.undo_stack = []  # (what was done, snapshot of the strings before it)
        # The list is sorted by clicking a column title; again reverses it.
        self.sort_column, self.sort_reversed = "n", False

        bar = ttk.Frame(root, padding=(8, 6, 8, 0))
        bar.pack(fill="x")
        ttk.Label(bar, text="Hae:").pack(side="left")
        self.search = tk.StringVar()
        self.search.trace_add("write", lambda *_: self.schedule_search())
        search = ttk.Entry(bar, textvariable=self.search, width=24)
        search.pack(side="left", padx=(4, 12))
        Tooltip(search, "Näyttää rivit, joiden lähteessä tai käännöksessä ovat kaikki kirjoitetut sanat")
        self.only_similar = tk.BooleanVar()
        similar = ttk.Checkbutton(bar, text="Vain valitun kaltaiset", variable=self.only_similar,
                                  command=self.toggle_similar)
        similar.pack(side="left", padx=(0, 12))
        Tooltip(similar, "Näyttää vain valitun rivin kaltaiset tekstit (kirjainkoko ja numerot ohitetaan)")
        self.together = tk.BooleanVar()
        together = ttk.Checkbutton(bar, text="Niputa samat tekstit", variable=self.together,
                                   command=self.toggle_together)
        together.pack(side="left")
        Tooltip(together, "Samat tekstit yhtenä rivinä; muokkaus, hyväksyntä ja hylkäys koskevat koko nippua")
        self.show_list = tk.BooleanVar(value=True)
        self.show_notes = tk.BooleanVar(value=True)
        ttk.Checkbutton(bar, text="Huomautukset", variable=self.show_notes,
                        command=self.toggle_notes).pack(side="right")
        ttk.Checkbutton(bar, text="Lista", variable=self.show_list,
                        command=self.toggle_list).pack(side="right", padx=(12, 12))

        self.panes = panes = ttk.PanedWindow(root, orient="horizontal")
        panes.pack(fill="both", expand=True, padx=8, pady=8)

        # Fixed requests (no propagation), so the panes split the width by
        # weight instead of the list keeping its full natural width.
        self.left = left = ttk.Frame(panes, width=MIN_SIZE[0] * 2 // 5)
        left.pack_propagate(False)
        self.list = ttk.Treeview(left, columns=("n", "state", "repeats", "source", "target"), show="headings",
                                 selectmode="extended")
        for col, title, width, stretch in COLUMNS:
            self.list.heading(col, text=title, anchor="w", command=lambda col=col: self.sort_by(col))
            self.list.column(col, width=width, minwidth=30, stretch=stretch, anchor="w")
        # The expand arrow of a bundle row, shown only while bundles are on.
        self.list.column("#0", width=34, minwidth=34, stretch=False)
        scroll = ttk.Scrollbar(left, orient="vertical", command=self.list.yview)
        self.list.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.list.pack(side="left", fill="both", expand=True)
        self.list.bind("<<TreeviewSelect>>", self.on_select)
        self.list.bind("<Control-a>", lambda _: (self.list.selection_set(self.list.get_children()), "break")[1])
        panes.add(left, weight=2)

        right = ttk.Frame(panes, width=MIN_SIZE[0] * 3 // 5)
        right.pack_propagate(False)
        self.buttons = buttons = ttk.Frame(right)
        buttons.pack(side="bottom", fill="x", pady=(6, 0))  # first, so it stays when the window is short
        self.approve_label = tk.StringVar()
        self.reject_label = tk.StringVar()
        for label, action, side, pad, tip in (
                (self.approve_label, self.approve, "left", 0, "Hyväksy ja siirry seuraavaan avoimeen (Ctrl+Enter)"),
                (self.reject_label, self.reject, "left", 6, "Hylkää ja siirry seuraavaan (Ctrl+Shift+Enter)"),
                ("Kumoa", self.undo, "left", 0, "Kumoa viimeisin hyväksyntä tai hylkäys (Ctrl+Z)"),
                ("Seuraava", lambda: self.step(1), "right", 0, "Seuraava rivi (Ctrl+↓)"),
                ("Edellinen", lambda: self.step(-1), "right", 6, "Edellinen rivi (Ctrl+↑)")):
            text = {"textvariable": label} if isinstance(label, tk.StringVar) else {"text": label}
            button = ttk.Button(buttons, command=action, **text)
            button.pack(side=side, padx=pad)
            Tooltip(button, tip)
        self.notes_frame = ttk.Frame(right)
        self.notes_frame.pack(side="bottom", fill="x")
        self.notes = self._text(self.notes_frame, "Huomautukset", height=5, readonly=True, expand=False)
        # Source and translation side by side, in equal columns.
        pair = ttk.Frame(right)
        pair.pack(fill="both", expand=True)
        pair.columnconfigure((0, 1), weight=1, uniform="pair")
        pair.rowconfigure(0, weight=1)
        source_col, target_col = ttk.Frame(pair), ttk.Frame(pair)
        source_col.grid(row=0, column=0, sticky="nsew", padx=(0, 4))
        target_col.grid(row=0, column=1, sticky="nsew", padx=(4, 0))
        self.source = self._text(source_col, "Lähde", height=4, readonly=True)
        self.target = self._text(target_col, "Käännös", height=4)
        self.target.bind("<<Modified>>", self.on_edit)
        panes.add(right, weight=3)

        # Bound on the target itself too, so its own Return and Ctrl+arrow
        # bindings never run first.
        for key, action in (("<Control-Return>", self.approve), ("<Control-Shift-Return>", self.reject),
                            ("<Control-Down>", lambda: self.step(1)), ("<Control-Up>", lambda: self.step(-1)),
                            ("<Control-s>", self.save), ("<F1>", self.help)):
            for widget in (root, self.target, self.list):
                widget.bind(key, lambda event, action=action: (action(), "break")[1])
        # Ctrl+Z undoes typing first, as in any text box; with nothing typed to
        # undo there, and anywhere else, it undoes the last decision.
        root.bind("<Control-z>", lambda _: (self.undo(), "break")[1])
        self.list.bind("<Control-z>", lambda _: (self.undo(), "break")[1])
        self.target.bind("<Control-z>", self.on_target_undo)

        self.apply_theme()
        self.fill_list()
        self.set_status(f"Varmuuskopio: {session.backup_path}")
        self.target.focus_set()

    def _text(self, parent, title, height, readonly=False, expand=True):
        ttk.Label(parent, text=title).pack(anchor="w", pady=(6, 0))
        # width=1: the text boxes take only the room the list leaves them.
        text = tk.Text(parent, width=1, height=height, wrap="word", undo=not readonly, font=("Segoe UI", 11))
        text.pack(fill="both", expand=expand)
        if readonly:
            text.configure(state="disabled")
        return text

    # The list

    def row(self, i):
        s = self.session
        repeats = s.repeats[i]
        count = ""
        if len(repeats) > 1:
            # "≠": the repeats have different translations now; a decision
            # with "Toistot yhdessä" on gives them all the shown one.
            count = f"{len(repeats)} ≠" if self.differs(i) else len(repeats)
        return (i + 1, self.names[s.state_id(i)], count, s.source_plain[i], plain(s.units[i].target))

    def visible(self, i):
        wanted = dict(FILTERS)[self.filter.get()]
        if wanted is not None and self.session.kind(i) != wanted:
            return False
        if self.similar and i not in self.similar[1]:
            return False
        words = self.search.get().casefold().split()
        if words:
            text = f"{self.session.source_plain[i]} {plain(self.session.units[i].target)}".casefold()
            return all(word in text for word in words)
        return True

    def sort_by(self, column):
        """Sort by a column; the same column again reverses the order."""
        if column == self.sort_column:
            self.sort_reversed = not self.sort_reversed
        else:
            # Most repeats first is the useful start for that column.
            self.sort_column, self.sort_reversed = column, column == "repeats"
        self.fill_list()

    def sort_key(self):
        s = self.session
        return {
            "n": lambda i: i,
            "state": lambda i: (s.state_id(i), i),
            "repeats": lambda i: (len(s.repeats[i]), alphabetical(s.source_pattern[i]), i),
            # Case and numbers ignored first, so "12 x 20" and "14 x 20" sit together.
            "source": lambda i: (alphabetical(s.source_pattern[i]), alphabetical(s.source_plain[i]), i),
            "target": lambda i: (alphabetical(plain(s.units[i].target)), i),
        }[self.sort_column]

    def update_headings(self):
        for col, title, _, _ in COLUMNS:
            arrow = (" ▼" if self.sort_reversed else " ▲") if col == self.sort_column else ""
            self.list.heading(col, text=title + arrow)

    def bundled(self):
        return self.together.get()

    def bundle_visible(self, i):
        """With bundles on, a bundle is listed while any of its strings passes the filters."""
        return any(self.visible(j) for j in self.session.repeats[i])

    def top(self, i):
        """The list's top-level row for string i: its bundle row when bundled."""
        parent = self.list.parent(str(i)) if self.list.exists(str(i)) else ""
        return int(parent) if parent else i

    def fill_list(self):
        self.list.delete(*self.list.get_children())
        s = self.session
        if self.bundled():
            # One row per bundle (its first string), the others under it.
            self.list.configure(show=("tree", "headings"))
            rows = [i for i in range(len(s)) if s.repeats[i][0] == i and self.bundle_visible(i)]
        else:
            self.list.configure(show="headings")
            rows = [i for i in range(len(s)) if self.visible(i)]
        rows.sort(key=self.sort_key(), reverse=self.sort_reversed)
        self.update_headings()
        for i in rows:
            self.list.insert("", "end", iid=str(i), values=self.row(i))
            if self.bundled():
                for j in s.repeats[i][1:]:
                    self.list.insert(str(i), "end", iid=str(j), values=self.row(j))
        self.update_counts()
        if self.current is not None and self.list.exists(str(self.current)):
            self.select(self.current)
        elif rows:
            self.select(rows[0])
        else:
            self.show(None)
            self.update_buttons()

    def update_counts(self):
        c = self.session.counts()
        self.counts.configure(text=f"Yhteensä {len(self.session)}   avoimia {c[OPEN]}   "
                                   f"hyväksyttyjä {c[APPROVED]}   hylättyjä {c[REJECTED]}")
        shown = len(self.list.get_children())
        self.shown.configure(text=f"Listassa {shown}" if shown != len(self.session) else "")

    def refresh_row(self, i):
        if self.list.exists(str(i)):
            self.list.item(str(i), values=self.row(i))
        self.update_counts()

    def select(self, i):
        self.list.selection_set(str(i))
        self.list.focus(str(i))
        self.list.see(str(i))

    def selected(self):
        """The selected strings, in list order."""
        return [int(iid) for iid in self.list.selection()]

    def on_select(self, _event):
        selected = self.list.selection()
        if selected:
            focus = self.list.focus()
            i = int(focus) if focus in selected else int(selected[-1])
            if i != self.current:
                self.show(i)
        self.update_buttons()

    def update_buttons(self):
        n = len(self.list.selection())
        many = f" valitut ({n})" if n > 1 else ""
        self.approve_label.set(f"Hyväksy{many} (Ctrl+Enter)")
        self.reject_label.set(f"Hylkää{many}")

    def schedule_search(self):
        if self.search_job:
            self.root.after_cancel(self.search_job)
        self.search_job = self.root.after(SEARCH_DELAY_MS, self.fill_list)

    def toggle_similar(self):
        if self.only_similar.get() and self.current is not None:
            self.similar = (self.current, self.session.similar_to(self.current))
            self.set_status(f"Näytetään nro {self.current + 1} ja sen kaltaiset: {len(self.similar[1])}.")
        else:
            self.only_similar.set(False)
            self.similar = None
        self.fill_list()

    def apply_theme(self):
        mode = theme.apply(self.root, self.theme_choice, [self.target], [self.source, self.notes])
        self.list.tag_configure("changed", background=theme.FLASH[mode])

    def choose_theme(self):
        self.theme_choice = theme.CHOICES[self.theme_name.get()]
        theme.save_choice(self.theme_choice)
        self.apply_theme()

    def help(self):
        messagebox.showinfo("Ohje", HELP, parent=self.root)

    def toggle_list(self):
        # A hidden list still holds the order, so Ctrl+arrows keep working.
        if self.show_list.get():
            self.panes.insert(0, self.left, weight=2)
        else:
            self.panes.forget(self.left)

    def toggle_notes(self):
        if self.show_notes.get():
            self.notes_frame.pack(side="bottom", fill="x", after=self.buttons)
        else:
            self.notes_frame.pack_forget()

    def toggle_together(self):
        if self.together.get():
            self.set_status("Samat tekstit niputettu: muokkaus, hyväksyntä ja hylkäys koskevat koko nippua.")
        self.fill_list()

    def differs(self, i):
        """Whether the strings bundled with i have different translations now."""
        return len({self.session.translation(j) for j in self.session.repeats[i]}) > 1

    def members(self, chosen):
        """The chosen strings, with their bundles when bundling is on."""
        if not self.bundled():
            return list(chosen)
        out = []
        for i in chosen:
            out += [j for j in self.session.repeats[i] if j not in out]
        return out

    def with_repeats(self, chosen):
        """The chosen strings, and with bundling on their bundles too, each
        given the translation of the chosen string it belongs with."""
        if not self.bundled():
            return chosen, []
        out, copied = [], []
        for i in chosen:
            if i in out:
                continue
            copied += self.session.copy_to_repeats(i)
            out += self.session.repeats[i]
        return out, copied

    def remember(self, what, chosen):
        """Keep the strings a decision is about to change, for undo()."""
        self.undo_stack.append((what, self.session.snapshot(self.members(chosen))))

    def undo(self):
        if not self.undo_stack:
            self.root.bell()
            self.set_status("Ei kumottavaa.")
            return
        what, snapshot = self.undo_stack.pop()
        self.session.restore(snapshot)
        changed = [i for i, _ in snapshot]
        self.current = None
        self.fill_list()
        first = next((i for i in changed if self.list.exists(str(i))), None)
        if first is not None:
            self.select(first)
        self.flash(changed)
        self.schedule_save()
        self.set_status(f"Kumottu: {what}")

    def on_target_undo(self, _event):
        try:
            typed = self.target.tk.call(self.target._w, "edit", "canundo")
        except tk.TclError:
            typed = False
        if typed:
            return None  # the text box undoes the typing itself
        self.undo()
        return "break"

    def flash(self, indices):
        """Mark the rows a decision changed for a moment."""
        rows = [str(i) for i in indices if self.list.exists(str(i))]
        for iid in rows:
            self.list.item(iid, tags=("changed",))

        def clear():
            for iid in rows:
                if self.list.exists(iid):
                    self.list.item(iid, tags=())

        self.root.after(FLASH_MS, clear)

    # The selected string

    def show(self, i):
        self.current = i
        self.loading = True
        for widget, text in ((self.source, self.session.source(i) if i is not None else ""),
                             (self.notes, self.session.notes(i) if i is not None else ""),
                             (self.target, self.session.target(i) if i is not None else "")):
            widget.configure(state="normal")
            widget.delete("1.0", "end")
            widget.insert("1.0", text)
            if widget is not self.target:
                widget.configure(state="disabled")
        self.target.edit_reset()
        self.target.edit_modified(False)
        self.loading = False

    def on_edit(self, _event):
        if self.loading or not self.target.edit_modified():
            return
        self.target.edit_modified(False)
        if self.current is not None and self.session.set_target(self.current, self.target.get("1.0", "end-1c")):
            changed = [self.current] + (self.session.copy_to_repeats(self.current) if self.together.get() else [])
            for i in changed:
                self.refresh_row(i)
            self.schedule_save()

    def step(self, delta):
        rows = self.list.get_children()
        if not rows:
            return
        here = rows.index(str(self.top(self.current))) if self.current is not None and self.list.exists(
            str(self.current)) else -1
        self.select(int(rows[max(0, min(len(rows) - 1, here + delta))]))

    def after_selection(self, chosen, wanted=lambda i: True):
        """The first row below the chosen ones, in list order, that passes wanted."""
        rows = [int(iid) for iid in self.list.get_children()]
        listed = [rows.index(self.top(i)) for i in chosen if self.list.exists(str(i))]
        if not listed:
            return None
        last = max(listed)
        return next((i for i in rows[last + 1:] + rows[:last] if i not in chosen and wanted(i)), None)

    def approve(self):
        chosen = self.selected() or ([self.current] if self.current is not None else [])
        if not chosen:
            return
        if not any(self.session.units[i].target for i in chosen):
            self.root.bell()
            self.set_status("Tyhjää käännöstä ei voi hyväksyä.")
            return
        differing = self.bundled() and any(self.differs(i) for i in chosen)
        if differing and any(self.differs(i) and self.session.translation(i) is None for i in chosen):
            # The shown translation cannot be read as one run of text, so it
            # cannot be shared; signing off differing translations would mislead.
            self.root.bell()
            self.set_status("Nippua ei hyväksytty: sen käännökset eroavat, eikä näytettyä muotoiltua käännöstä "
                            "voi jakaa muille. Muokkaa käännös tai hyväksy jäsenet erikseen.")
            return
        self.remember(f"hyväksyntä ({len(self.members(chosen))})", chosen)
        group, copied = self.with_repeats(chosen)
        done = [i for i in group if self.session.approve(i)]
        skipped = len(group) - len(done)
        move_to = self.after_selection(chosen, lambda i: self.session.kind(i) == OPEN and i not in group)
        self.decided(sorted(set(done + copied)), move_to)
        message = f"Hyväksytty {len(done)}" + (f", ohitettu {skipped} tyhjää" if skipped else "") + "."
        if differing:
            message += " Nipun käännökset erosivat; kaikki saivat näytetyn käännöksen. Kumoa: Ctrl+Z."
        self.set_status(message)

    def reject(self):
        chosen = self.selected() or ([self.current] if self.current is not None else [])
        if not chosen:
            return
        self.remember(f"hylkäys ({len(self.members(chosen))})", chosen)
        group, _ = self.with_repeats(chosen)
        move_to = self.after_selection(chosen, lambda i: i not in group)
        for i in group:
            self.session.reject(i)
        self.decided(group, move_to)
        self.set_status(f"Hylätty {len(group)}.")

    def decided(self, changed, move_to):
        """Show the decisions on the changed strings, then move to move_to (or stay)."""
        self.schedule_save()
        if self.bundled():
            # A bundle row goes when none of its strings passes the filters any more.
            changed = sorted({self.session.repeats[i][0] for i in changed} | set(changed))
            gone = [i for i in changed if self.list.exists(str(i)) and self.list.parent(str(i)) == ""
                    and not self.bundle_visible(i)]
        else:
            gone = [i for i in changed if self.list.exists(str(i)) and not self.visible(i)]
        for i in changed:
            if i in gone:
                self.list.delete(str(i))
            elif self.list.exists(str(i)):
                self.refresh_row(i)
        self.update_counts()
        if move_to is None and gone:
            rows = self.list.get_children()
            move_to = int(rows[-1]) if rows else None
        if move_to is not None:
            self.select(move_to)
        elif gone:
            self.show(None)
            self.update_buttons()
        self.flash(changed)

    # Saving

    def schedule_save(self):
        if self.autosave_job:
            self.root.after_cancel(self.autosave_job)
        self.autosave_job = self.root.after(AUTOSAVE_MS, self.save)
        self.saved.configure(text="Tallentamattomia muutoksia…")

    def save(self):
        if self.autosave_job:
            self.root.after_cancel(self.autosave_job)
            self.autosave_job = None
        try:
            self.session.save()
        except Exception as error:
            self.saved.configure(text="TALLENNUS EPÄONNISTUI")
            self.set_status(f"Tallennus epäonnistui: {error}")
            return False
        self.saved.configure(text=f"Tallennettu klo {time.strftime('%H.%M.%S', self.session.saved_at)}")
        return True

    def set_status(self, text):
        self.status.configure(text=text)

    def close(self):
        if self.session.dirty and not self.save():
            messagebox.showerror("Tallennus epäonnistui",
                                 "Muutoksia ei saatu tallennettua, joten ikkunaa ei suljeta.\n\n"
                                 + self.status.cget("text"))
            return
        self.root.destroy()


def main():
    parser = argparse.ArgumentParser(prog="virtaal-review", description="Tarkasta XLIFF 1.2 -tiedoston käännökset.")
    parser.add_argument("file", nargs="?", help="tarkastettava XLIFF-tiedosto; ilman sitä ikkuna kysyy tiedoston")
    args = parser.parse_args()
    root = tk.Tk()
    path = args.file or filedialog.askopenfilename(
        title="Avaa tarkastettava XLIFF", filetypes=[("XLIFF", "*.xliff *.xlf"), ("Kaikki", "*.*")])
    if not path:
        return
    try:
        session = ReviewSession(path)
    except RefusedFile as error:
        messagebox.showerror("Tiedostoa ei avattu", str(error))
        return
    except Exception as error:
        messagebox.showerror("Tiedostoa ei avattu", f"Tiedoston lukeminen epäonnistui:\n{error}")
        return
    ReviewWindow(root, session)
    root.mainloop()


if __name__ == "__main__":
    main()
