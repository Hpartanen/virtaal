import os

import pytest

from virtaal_review.session import APPROVED, OPEN, REJECTED, RefusedFile, ReviewSession, state_names

HEAD = '<?xml version="1.0" encoding="UTF-8"?>\n<xliff version="1.2" xmlns="urn:oasis:names:tc:xliff:document:1.2">\n'
FILE = '<file original="t" source-language="sv" target-language="fi" datatype="plaintext"><body>\n'
TAIL = "</body></file></xliff>\n"


def write(tmp_path, units, name="t.xliff"):
    path = tmp_path / name
    path.write_text(HEAD + FILE + "".join(units) + TAIL, encoding="utf-8")
    return path


def unit(uid, source, target=None, state="translated", space=' xml:space="preserve"', notes=()):
    tgt = "" if target is None else f'<target state="{state}">{target}</target>'
    note = "".join(f"<note>{n}</note>" for n in notes)
    return f'<trans-unit id="{uid}"{space}><source>{source}</source>{tgt}{note}</trans-unit>\n'


@pytest.fixture
def basic(tmp_path):
    return write(tmp_path, [
        unit("a", "Ventil", "Venttiili", notes=("first note", "second note")),
        unit("b", "Kanal"),
    ])


def reopen(session):
    return ReviewSession(session.path)


def test_approve_writes_signed_off_and_survives_reload(basic):
    s = ReviewSession(basic)
    assert s.kind(0) == OPEN and s.notes(0) == "first note\nsecond note"
    assert s.approve(0)
    s.save()
    assert 'state="signed-off"' in basic.read_text(encoding="utf-8")
    assert reopen(s).kind(0) == APPROVED


def test_reject_keeps_target(basic):
    s = ReviewSession(basic)
    s.reject(0)
    s.save()
    again = reopen(s)
    assert again.kind(0) == REJECTED and again.target(0) == "Venttiili"
    assert 'state="needs-translation"' in basic.read_text(encoding="utf-8")


def test_edit_after_approve_drops_to_translated(basic):
    s = ReviewSession(basic)
    s.approve(0)
    assert not s.set_target(0, "Venttiili")  # unchanged text is not an edit
    assert s.kind(0) == APPROVED
    assert s.set_target(0, "Venttiilit")
    s.save()
    assert reopen(s).kind(0) == OPEN
    assert 'state="translated"' in basic.read_text(encoding="utf-8")


def test_empty_target_is_not_approved(basic):
    s = ReviewSession(basic)
    assert not s.approve(1)
    assert s.kind(1) == OPEN and not s.dirty


@pytest.mark.parametrize("brk, xml", [("\r", "&#13;"), ("\r\n", "&#13;\n"), ("\n", "\n")])
def test_target_breaks_take_the_source_form(tmp_path, brk, xml):
    path = write(tmp_path, [unit("a", f"Rad 1{xml}rad 2")])
    s = ReviewSession(path)
    assert s.source(0) == "Rad 1\nrad 2"
    s.set_target(0, "Rivi 1\nrivi 2")
    s.save()
    again = reopen(s)
    assert again.units[0].source == f"Rad 1{brk}rad 2"
    assert again.units[0].target == f"Rivi 1{brk}rivi 2"


def test_break_added_to_one_line_source_is_cr(basic):
    s = ReviewSession(basic)
    s.set_target(1, "Kanava\nkanava")
    assert s.units[1].target == "Kanava\rkanava"


def test_cr_without_preserve_is_refused(tmp_path):
    path = write(tmp_path, [unit("a", "a&#13;b", space=""), unit("b", "c&#xd;d", space="")])
    with pytest.raises(RefusedFile, match="2 merkkijonossa"):
        ReviewSession(path)
    assert not [n for n in os.listdir(tmp_path) if ".backup-" in n]


def test_preserve_on_an_ancestor_counts(tmp_path):
    path = tmp_path / "t.xliff"
    path.write_text(HEAD + FILE.replace("<body>", '<body xml:space="preserve">')
                    + unit("a", "a&#13;b", space="") + TAIL, encoding="utf-8")
    assert ReviewSession(path).source(0) == "a\nb"


def test_backup_on_open_and_save_is_atomic(basic, monkeypatch):
    original = basic.read_bytes()
    s = ReviewSession(basic)
    assert open(s.backup_path, "rb").read() == original
    s.approve(0)

    def fail(out):
        out.write(b"<?xml half")
        raise OSError("disk full")

    monkeypatch.setattr(s.model._trans_store, "serialize", fail)
    with pytest.raises(OSError):
        s.save()
    assert basic.read_bytes() == original and s.dirty
    assert sorted(os.listdir(basic.parent)) == sorted([basic.name, os.path.basename(s.backup_path)])

    monkeypatch.undo()
    s.save()
    assert not s.dirty and s.saved_at
    assert 'state="signed-off"' in basic.read_text(encoding="utf-8")


def test_untouched_units_round_trip(basic):
    before = basic.read_text(encoding="utf-8")
    s = ReviewSession(basic)
    s.save()
    after = basic.read_text(encoding="utf-8")
    assert after.count("<note>") == before.count("<note>")
    assert [u.xmlelement.get("id") for u in reopen(s).units] == ["a", "b"]


def test_virtaal_state_names_are_finnish():
    names = state_names()
    assert names[120] == "Tarkastettu" and names[30] == "Keskeneräinen"
