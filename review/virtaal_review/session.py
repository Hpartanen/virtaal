"""One review of one XLIFF 1.2 file, without any GUI.

Loading and serializing go through Virtaal's StoreModel and translate-toolkit;
this module adds what a reviewer's tool needs on top: explicit states (no
200 ms state timer), line breaks kept in the form the source uses, a backup
on open, and saves that never leave a half-written file behind.

States, as XLIFF writes them:
  approved  state="signed-off" (Virtaal's FINAL, "Tarkastettu"); nothing else
            counts as a review, not even translated + approved="yes".
  rejected  state="needs-translation" (NEEDS_WORK, "Keskeneräinen"), target kept.
  open      anything else; an edited target becomes state="translated".
"""
import html
import os
import re
import shutil
import tempfile
import time
import types
from difflib import SequenceMatcher

from lxml import etree
from translate.storage.workflow import StateEnum

# The package's __init__ has already put the GTK stand-in and this checkout's
# virtaal in place.
from virtaal.models.storemodel import StoreModel

OPEN, APPROVED, REJECTED = "open", "approved", "rejected"

_XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"


class RefusedFile(Exception):
    """The file cannot be reviewed safely; the message is for the reviewer, in Finnish."""


def _install_catalog():
    """Compile the wheel's fi.po where Virtaal looks for its catalog.

    In a checkout there is no packaged fi.po: Virtaal compiles po/fi.po itself.
    """
    from translate.tools.pocompile import convertmo
    from virtaal.common.platform import platform

    po = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fi.po")
    mo = os.path.join(platform.locale_dir, "fi", "LC_MESSAGES", "virtaal.mo")
    if not os.path.isfile(po) or os.path.isfile(mo):
        return
    os.makedirs(os.path.dirname(mo), exist_ok=True)
    with open(mo + ".tmp", "w") as out, open(po, "rb") as src:
        convertmo(src, out, None)
    os.replace(mo + ".tmp", mo)


def state_names():
    """Virtaal's own Finnish names for the unit states, keyed by StateEnum."""
    from virtaal.common import pan_app

    _install_catalog()

    pan_app.set_ui_language("fi")
    from virtaal.controllers.unitcontroller import UnitController

    return UnitController.get_unit_state_names(types.SimpleNamespace())


def check_line_breaks(path):
    """Refuse a file whose CRs translate-toolkit would turn into spaces.

    The toolkit keeps a CR only inside xml:space="preserve"; elsewhere it
    normalizes whitespace on load and rewrites the source on save.
    """
    lost = 0
    for _, unit in etree.iterparse(path, tag="{*}trans-unit"):
        if any("\r" in text for text in unit.itertext()):
            space = next((e.get(_XML_SPACE) for e in unit.iterancestors() if e.get(_XML_SPACE)), None)
            if (unit.get(_XML_SPACE) or space) != "preserve":
                lost += 1
    if lost:
        raise RefusedFile(
            f"Tiedostoa ei avattu: {lost} merkkijonossa on rivinvaihto (CR), mutta "
            'niiltä puuttuu määrite xml:space="preserve", joten rivinvaihdot katoaisivat. '
            "Vie tiedosto uudelleen niin, että määrite on mukana."
        )


def _break_form(source):
    if "\r\n" in source:
        return "\r\n"
    if "\n" in source:
        return "\n"
    # A one-line source gives no form to follow; a break added to it is
    # written as a bare CR.
    return "\r"


def to_display(text):
    """Text with every line break as \\n, as Tk's Text widget uses."""
    return (text or "").replace("\r\n", "\n").replace("\r", "\n")


def plain(text):
    """The text a reader sees, on one line: rich-text markup dropped."""
    text = to_display(text)
    if text.lstrip().startswith("<") or "<?xml" in text:
        text = html.unescape(re.sub(r"<[^>]*>", "", text))
    return " ".join(text.split())


def pattern(text):
    """plain() in lower case with every number as #, so '12 x 20' and '14 x 20' match."""
    return re.sub(r"\d+(?:[.,]\d+)?", "#", plain(text).casefold())


SIMILARITY = 0.7  # SequenceMatcher ratio of two patterns; a judgment call, not measured


def similar(a, b):
    """Whether two patterns are alike enough to review together."""
    if a == b:
        return True
    m = SequenceMatcher(None, a, b)
    return m.real_quick_ratio() >= SIMILARITY and m.quick_ratio() >= SIMILARITY and m.ratio() >= SIMILARITY


class ReviewSession:
    def __init__(self, path):
        self.path = os.path.abspath(path)
        check_line_breaks(self.path)
        self.backup_path = self._backup()
        self.model = StoreModel(self.path, None)
        self.units = self.model.get_units()
        # Sources never change, so their list forms are worked out once.
        self.source_plain = [plain(u.source) for u in self.units]
        self.source_pattern = [pattern(u.source) for u in self.units]
        # Strings with exactly the same source: each entry lists the whole group.
        groups = {}
        for i, u in enumerate(self.units):
            groups.setdefault(u.source, []).append(i)
        self.repeats = [groups[u.source] for u in self.units]
        self.dirty = False
        self.saved_at = None

    def __len__(self):
        return len(self.units)

    def _backup(self):
        root, ext = os.path.splitext(self.path)
        backup = f"{root}.backup-{time.strftime('%Y%m%d-%H%M%S')}{ext}"
        shutil.copy2(self.path, backup)
        return backup

    # Reading

    def source(self, i):
        return to_display(self.units[i].source)

    def similar_to(self, i):
        """The strings whose source is like string i's, i itself included."""
        return {j for j, p in enumerate(self.source_pattern) if similar(self.source_pattern[i], p)}

    def target(self, i):
        return to_display(self.units[i].target)

    def notes(self, i):
        return self.units[i].getnotes()

    def state_id(self, i):
        return self.units[i].get_state_id()

    def kind(self, i):
        state = self.state_id(i)
        if state == StateEnum.FINAL:
            return APPROVED
        if state == StateEnum.NEEDS_WORK:
            return REJECTED
        return OPEN

    def counts(self):
        counts = {OPEN: 0, APPROVED: 0, REJECTED: 0}
        for i in range(len(self.units)):
            counts[self.kind(i)] += 1
        return counts

    # Changing

    def set_target(self, i, text):
        """Store the reviewer's text; an edit makes an approved string open again."""
        text = to_display(text)
        if text == self.target(i):
            return False
        unit = self.units[i]
        unit.settarget(text.replace("\n", _break_form(unit.source)))
        unit.set_state_n(unit.S_TRANSLATED if text else unit.S_UNTRANSLATED)
        self.dirty = True
        return True

    def copy_to_repeats(self, i):
        """Give string i's translation to the other strings with the same source.

        Returns the strings that changed; as with any edit, they become open.
        """
        return [j for j in self.repeats[i] if j != i and self.set_target(j, self.target(i))]

    def approve(self, i):
        """Sign the string off; an empty target cannot be approved."""
        unit = self.units[i]
        if not unit.target:
            return False
        unit.set_state_n(unit.S_SIGNED_OFF)
        self.dirty = True
        return True

    def reject(self, i):
        self.units[i].set_state_n(self.units[i].S_NEEDS_TRANSLATION)
        self.dirty = True

    # Saving

    def save(self):
        """Write to a temp file beside the original, then swap it in."""
        folder, name = os.path.split(self.path)
        fd, tmp = tempfile.mkstemp(dir=folder, prefix=f".{name}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as out:
                self.model._update_header()
                self.model._trans_store.serialize(out)
                out.flush()
                os.fsync(out.fileno())
            os.replace(tmp, self.path)
        except BaseException:
            if os.path.exists(tmp):
                os.remove(tmp)
            raise
        self.dirty = False
        self.saved_at = time.localtime()
