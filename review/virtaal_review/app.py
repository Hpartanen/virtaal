"""The review window: a string list on the left, the selected string on the right.

All file and state logic is in `session`; this module only shows it and
turns keys into calls. Saving happens shortly after each change, on Ctrl+S
and on close.

The list can be filtered (state, search text, strings like the selected one)
and sorted; several selected strings can be approved or rejected at once.
With "Toistot yhdessä" on, strings with exactly the same source share one
translation and one decision.
"""
import argparse
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .session import APPROVED, OPEN, REJECTED, RefusedFile, ReviewSession, plain, state_names

AUTOSAVE_MS = 1500
SEARCH_DELAY_MS = 250
FILTERS = [("Kaikki", None), ("Avoimet", OPEN), ("Hyväksytyt", APPROVED), ("Hylätyt", REJECTED)]
SORTS = ["Numero", "Lähde A–Ö", "Käännös A–Ö", "Tila", "Samankaltaiset vierekkäin"]
HEADING_SORT = {"n": "Numero", "state": "Tila", "repeats": "Samankaltaiset vierekkäin",
                "source": "Lähde A–Ö", "target": "Käännös A–Ö"}
# Swedish and Finnish put å, ä and ö after z, in that order.
# "{", "|" and "}" are the characters right after "z".
_ALPHABET = str.maketrans({"å": "{", "ä": "|", "æ": "|", "ö": "}", "ø": "}"})


def alphabetical(text):
    return text.casefold().translate(_ALPHABET)


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

        top = ttk.Frame(root, padding=(8, 8, 8, 0))
        top.pack(fill="x")
        ttk.Label(top, text="Näytä:").pack(side="left")
        self.filter = tk.StringVar(value=FILTERS[0][0])
        for label, _ in FILTERS:
            ttk.Radiobutton(top, text=label, value=label, variable=self.filter,
                            command=self.fill_list).pack(side="left", padx=4)
        self.counts = ttk.Label(top)
        self.counts.pack(side="right")

        bar = ttk.Frame(root, padding=(8, 6, 8, 0))
        bar.pack(fill="x")
        ttk.Label(bar, text="Hae:").pack(side="left")
        self.search = tk.StringVar()
        self.search.trace_add("write", lambda *_: self.schedule_search())
        search = ttk.Entry(bar, textvariable=self.search, width=30)
        search.pack(side="left", padx=(4, 12))
        self.only_similar = tk.BooleanVar()
        ttk.Checkbutton(bar, text="Vain valitun kaltaiset", variable=self.only_similar,
                        command=self.toggle_similar).pack(side="left", padx=(0, 12))
        self.together = tk.BooleanVar()
        ttk.Checkbutton(bar, text="Toistot yhdessä", variable=self.together,
                        command=self.toggle_together).pack(side="left", padx=(0, 12))
        self.show_list = tk.BooleanVar(value=True)
        self.show_notes = tk.BooleanVar(value=True)
        ttk.Checkbutton(bar, text="Näytä huomautukset", variable=self.show_notes,
                        command=self.toggle_notes).pack(side="right", padx=(12, 0))
        ttk.Checkbutton(bar, text="Näytä lista", variable=self.show_list,
                        command=self.toggle_list).pack(side="right", padx=(12, 0))
        ttk.Label(bar, text="Järjestä:").pack(side="left")
        self.sort = tk.StringVar(value=SORTS[0])
        sort = ttk.Combobox(bar, textvariable=self.sort, values=SORTS, state="readonly", width=26)
        sort.pack(side="left", padx=4)
        sort.bind("<<ComboboxSelected>>", lambda _: self.fill_list())
        self.shown = ttk.Label(bar)
        self.shown.pack(side="right")

        self.panes = panes = ttk.PanedWindow(root, orient="horizontal")
        panes.pack(fill="both", expand=True, padx=8, pady=8)

        self.left = left = ttk.Frame(panes)
        self.list = ttk.Treeview(left, columns=("n", "state", "repeats", "source", "target"), show="headings",
                                 selectmode="extended")
        for col, title, width, stretch in (("n", "Nro", 45, False), ("state", "Tila", 100, False),
                                           ("repeats", "×", 30, False),
                                           ("source", "Lähde", 150, True), ("target", "Käännös", 150, True)):
            self.list.heading(col, text=title, anchor="w",
                              command=lambda col=col: (self.sort.set(HEADING_SORT[col]), self.fill_list()))
            self.list.column(col, width=width, stretch=stretch, anchor="w")
        scroll = ttk.Scrollbar(left, orient="vertical", command=self.list.yview)
        self.list.configure(yscrollcommand=scroll.set)
        self.list.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.list.bind("<<TreeviewSelect>>", self.on_select)
        self.list.bind("<Control-a>", lambda _: (self.list.selection_set(self.list.get_children()), "break")[1])
        panes.add(left, weight=1)

        right = ttk.Frame(panes)
        # Source and translation side by side, in equal columns.
        pair = ttk.Frame(right)
        pair.pack(fill="both", expand=True)
        pair.columnconfigure((0, 1), weight=1, uniform="pair")
        pair.rowconfigure(0, weight=1)
        source_col, target_col = ttk.Frame(pair), ttk.Frame(pair)
        source_col.grid(row=0, column=0, sticky="nsew", padx=(0, 4))
        target_col.grid(row=0, column=1, sticky="nsew", padx=(4, 0))
        self.source = self._text(source_col, "Lähde", height=8, readonly=True)
        self.target = self._text(target_col, "Käännös", height=8)
        self.notes_frame = ttk.Frame(right)
        self.notes_frame.pack(fill="x")
        self.notes = self._text(self.notes_frame, "Huomautukset", height=6, readonly=True, expand=False)
        self.target.bind("<<Modified>>", self.on_edit)
        self.buttons = buttons = ttk.Frame(right)
        buttons.pack(fill="x", pady=(6, 0))
        self.approve_label = tk.StringVar()
        self.reject_label = tk.StringVar()
        ttk.Button(buttons, textvariable=self.approve_label, command=self.approve).pack(side="left")
        ttk.Button(buttons, textvariable=self.reject_label, command=self.reject).pack(side="left", padx=6)
        ttk.Button(buttons, text="Seuraava (Ctrl+↓)", command=lambda: self.step(1)).pack(side="right")
        ttk.Button(buttons, text="Edellinen (Ctrl+↑)", command=lambda: self.step(-1)).pack(side="right", padx=6)
        panes.add(right, weight=1)

        self.status = ttk.Label(root, padding=(8, 0, 8, 6), anchor="w")
        self.status.pack(fill="x")

        # Bound on the target itself too, so its own Return and Ctrl+arrow
        # bindings never run first.
        for key, action in (("<Control-Return>", self.approve), ("<Control-Shift-Return>", self.reject),
                            ("<Control-Down>", lambda: self.step(1)), ("<Control-Up>", lambda: self.step(-1)),
                            ("<Control-s>", self.save)):
            for widget in (root, self.target, self.list):
                widget.bind(key, lambda event, action=action: (action(), "break")[1])

        self.fill_list()
        self.set_status(f"Varmuuskopio: {session.backup_path}")
        self.target.focus_set()

    def _text(self, parent, title, height, readonly=False, expand=True):
        ttk.Label(parent, text=title).pack(anchor="w", pady=(6, 0))
        # width=1: the text boxes take only the room the list leaves them.
        text = tk.Text(parent, width=1, height=height, wrap="word", undo=not readonly, font=("Segoe UI", 11))
        text.pack(fill="both", expand=expand)
        if readonly:
            text.configure(state="disabled", background=self.root.cget("background"))
        return text

    # The list

    def row(self, i):
        s = self.session
        repeats = len(s.repeats[i])
        return (i + 1, self.names[s.state_id(i)], repeats if repeats > 1 else "", s.source_plain[i],
                plain(s.units[i].target))

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

    def sort_key(self):
        s = self.session
        return {
            "Numero": lambda i: i,
            "Lähde A–Ö": lambda i: (alphabetical(s.source_plain[i]), i),
            "Käännös A–Ö": lambda i: (alphabetical(plain(s.units[i].target)), i),
            "Tila": lambda i: (s.state_id(i), i),
            "Samankaltaiset vierekkäin": lambda i: (s.source_pattern[i], alphabetical(s.source_plain[i]), i),
        }[self.sort.get()]

    def fill_list(self):
        self.list.delete(*self.list.get_children())
        rows = sorted((i for i in range(len(self.session)) if self.visible(i)), key=self.sort_key())
        for i in rows:
            self.list.insert("", "end", iid=str(i), values=self.row(i))
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
        self.reject_label.set(f"Hylkää{many} (Ctrl+Shift+Enter)")

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

    def toggle_list(self):
        # A hidden list still holds the order, so Ctrl+arrows keep working.
        if self.show_list.get():
            self.panes.insert(0, self.left, weight=1)
        else:
            self.panes.forget(self.left)

    def toggle_notes(self):
        if self.show_notes.get():
            self.notes_frame.pack(fill="x", before=self.buttons)
        else:
            self.notes_frame.pack_forget()

    def toggle_together(self):
        if self.together.get():
            self.set_status("Toistot yhdessä: muokkaus, hyväksyntä ja hylkäys koskevat kaikkia saman lähteen "
                            "merkkijonoja.")

    def with_repeats(self, chosen):
        """The chosen strings, and with "Toistot yhdessä" on their repeats too,
        each given the translation of the chosen string it repeats."""
        if not self.together.get():
            return chosen, []
        out, copied = [], []
        for i in chosen:
            if i in out:
                continue
            copied += self.session.copy_to_repeats(i)
            out += self.session.repeats[i]
        return out, copied

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
        here = rows.index(str(self.current)) if self.list.exists(str(self.current)) else -1
        self.select(int(rows[max(0, min(len(rows) - 1, here + delta))]))

    def after_selection(self, chosen, wanted=lambda i: True):
        """The first row below the chosen ones, in list order, that passes wanted."""
        rows = [int(iid) for iid in self.list.get_children()]
        listed = [rows.index(i) for i in chosen if self.list.exists(str(i))]
        if not listed:
            return None
        last = max(listed)
        return next((i for i in rows[last + 1:] + rows[:last] if i not in chosen and wanted(i)), None)

    def approve(self):
        chosen = self.selected() or ([self.current] if self.current is not None else [])
        if not chosen:
            return
        group, copied = self.with_repeats(chosen)
        done = [i for i in group if self.session.approve(i)]
        skipped = len(group) - len(done)
        if not done:
            self.root.bell()
            self.set_status("Tyhjää käännöstä ei voi hyväksyä.")
            for i in copied:
                self.refresh_row(i)
            return
        move_to = self.after_selection(group, lambda i: self.session.kind(i) == OPEN)
        self.decided(sorted(set(done + copied)), move_to)
        if len(group) > 1 or skipped:
            self.set_status(f"Hyväksytty {len(done)}" + (f", ohitettu {skipped} tyhjää" if skipped else "") + ".")

    def reject(self):
        chosen = self.selected() or ([self.current] if self.current is not None else [])
        if not chosen:
            return
        group, _ = self.with_repeats(chosen)
        move_to = self.after_selection(group)
        for i in group:
            self.session.reject(i)
        self.decided(group, move_to)
        if len(group) > 1:
            self.set_status(f"Hylätty {len(group)}.")

    def decided(self, changed, move_to):
        """Show the decisions on the changed strings, then move to move_to (or stay)."""
        self.schedule_save()
        gone = [i for i in changed if not self.visible(i)]
        for i in changed:
            if i in gone:
                self.list.delete(str(i))
            else:
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

    # Saving

    def schedule_save(self):
        if self.autosave_job:
            self.root.after_cancel(self.autosave_job)
        self.autosave_job = self.root.after(AUTOSAVE_MS, self.save)
        self.set_status("Tallentamattomia muutoksia…")

    def save(self):
        if self.autosave_job:
            self.root.after_cancel(self.autosave_job)
            self.autosave_job = None
        try:
            self.session.save()
        except Exception as error:
            self.set_status(f"TALLENNUS EPÄONNISTUI: {error}")
            return False
        self.set_status(f"Tallennettu klo {time.strftime('%H.%M.%S', self.session.saved_at)}")
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
