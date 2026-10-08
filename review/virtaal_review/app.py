"""The review window: a string list on the left, the selected string on the right.

All file and state logic is in `session`; this module only shows it and
turns keys into calls. Saving happens shortly after each change, on Ctrl+S
and on close.
"""
import argparse
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .session import APPROVED, OPEN, REJECTED, RefusedFile, ReviewSession, state_names

AUTOSAVE_MS = 1500
FILTERS = [("Kaikki", None), ("Avoimet", OPEN), ("Hyväksytyt", APPROVED), ("Hylätyt", REJECTED)]


class ReviewWindow:
    def __init__(self, root, session):
        self.root = root
        self.session = session
        self.names = state_names()
        self.current = None
        self.loading = False
        self.autosave_job = None

        root.title(f"Tarkastus – {session.path}")
        root.geometry("1200x700")
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

        panes = ttk.PanedWindow(root, orient="horizontal")
        panes.pack(fill="both", expand=True, padx=8, pady=8)

        left = ttk.Frame(panes)
        self.list = ttk.Treeview(left, columns=("n", "state", "source"), show="headings", selectmode="browse")
        for col, title, width, stretch in (("n", "Nro", 50, False), ("state", "Tila", 110, False),
                                           ("source", "Lähde", 300, True)):
            self.list.heading(col, text=title, anchor="w")
            self.list.column(col, width=width, stretch=stretch, anchor="w")
        scroll = ttk.Scrollbar(left, orient="vertical", command=self.list.yview)
        self.list.configure(yscrollcommand=scroll.set)
        self.list.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.list.bind("<<TreeviewSelect>>", self.on_select)
        panes.add(left, weight=2)

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
        self.notes = self._text(right, "Huomautukset", height=6, readonly=True, expand=False)
        self.target.bind("<<Modified>>", self.on_edit)
        buttons = ttk.Frame(right)
        buttons.pack(fill="x", pady=(6, 0))
        ttk.Button(buttons, text="Hyväksy (Ctrl+Enter)", command=self.approve).pack(side="left")
        ttk.Button(buttons, text="Hylkää (Ctrl+Shift+Enter)", command=self.reject).pack(side="left", padx=6)
        ttk.Button(buttons, text="Seuraava (Ctrl+↓)", command=lambda: self.step(1)).pack(side="right")
        ttk.Button(buttons, text="Edellinen (Ctrl+↑)", command=lambda: self.step(-1)).pack(side="right", padx=6)
        panes.add(right, weight=3)

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
        text = tk.Text(parent, height=height, wrap="word", undo=not readonly, font=("Segoe UI", 11))
        text.pack(fill="both", expand=expand)
        if readonly:
            text.configure(state="disabled", background=self.root.cget("background"))
        return text

    # The list

    def row(self, i):
        first_line = self.session.source(i).split("\n", 1)[0]
        return (i + 1, self.names[self.session.state_id(i)], first_line)

    def visible(self, i):
        wanted = dict(FILTERS)[self.filter.get()]
        return wanted is None or self.session.kind(i) == wanted

    def fill_list(self):
        self.list.delete(*self.list.get_children())
        for i in range(len(self.session)):
            if self.visible(i):
                self.list.insert("", "end", iid=str(i), values=self.row(i))
        self.update_counts()
        rows = self.list.get_children()
        if self.current is not None and self.list.exists(str(self.current)):
            self.select(self.current)
        elif rows:
            self.select(int(rows[0]))
        else:
            self.show(None)

    def update_counts(self):
        c = self.session.counts()
        self.counts.configure(text=f"Yhteensä {len(self.session)}   avoimia {c[OPEN]}   "
                                   f"hyväksyttyjä {c[APPROVED]}   hylättyjä {c[REJECTED]}")

    def refresh_row(self, i):
        if self.list.exists(str(i)):
            self.list.item(str(i), values=self.row(i))
        self.update_counts()

    def select(self, i):
        self.list.selection_set(str(i))
        self.list.see(str(i))

    def on_select(self, _event):
        selected = self.list.selection()
        if selected and int(selected[0]) != self.current:
            self.show(int(selected[0]))

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
            self.refresh_row(self.current)
            self.schedule_save()

    def step(self, delta):
        rows = self.list.get_children()
        if not rows:
            return
        here = rows.index(str(self.current)) if self.list.exists(str(self.current)) else -1
        self.select(int(rows[max(0, min(len(rows) - 1, here + delta))]))

    def approve(self):
        i = self.current
        if i is None:
            return
        if not self.session.approve(i):
            self.root.bell()
            self.set_status("Tyhjää käännöstä ei voi hyväksyä.")
            return
        later = [j for j in range(i + 1, len(self.session))] + list(range(i))
        self.decided(i, next((j for j in later if self.session.kind(j) == OPEN and self.list.exists(str(j))), None))

    def reject(self):
        i = self.current
        if i is None:
            return
        self.session.reject(i)
        rows = self.list.get_children()
        k = rows.index(str(i))
        self.decided(i, int(rows[k + 1]) if k + 1 < len(rows) else None)

    def decided(self, i, move_to):
        """Show the decision on string i, then move to move_to (or stay)."""
        self.schedule_save()
        if self.visible(i):
            self.refresh_row(i)
        else:
            self.list.delete(str(i))
            self.update_counts()
        if move_to is None and not self.visible(i):
            rows = self.list.get_children()
            move_to = int(rows[-1]) if rows else None
        if move_to is not None:
            self.select(move_to)
        elif not self.visible(i):
            self.show(None)

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
