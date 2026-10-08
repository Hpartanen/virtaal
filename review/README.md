# virtaal-review

A small Tkinter window for reviewing one XLIFF 1.2 file on Virtaal's core
(its `StoreModel` and Finnish state names), without GTK. Virtaal's own
`virtaal/` package is imported from this checkout and left untouched.

## Start

From any folder, with the environment kept outside any synced folder
(replace `<checkout>` with the path of this repository):

```powershell
$env:UV_PROJECT_ENVIRONMENT = "$env:LOCALAPPDATA\venvs\virtaal-review"; uv run --project "<checkout>\review" virtaal-review
```

It asks for the file; a path to an `.xliff` can also be given at the end.

## As a dependency

Another project can install it from git; the wheel then carries its own copy
of Virtaal's `virtaal/` package and Finnish catalog:

```toml
[tool.uv.sources]
virtaal-review = { git = "https://github.com/Hpartanen/virtaal", subdirectory = "review", rev = "<sha>" }
```

## What it does

- **Approve** (Hyväksy, Ctrl+Enter) writes `state="signed-off"` and moves to
  the next open string. An empty target cannot be approved.
- **Reject** (Hylkää, Ctrl+Shift+Enter) writes `state="needs-translation"`
  and keeps the target.
- **Editing** a target makes it `state="translated"`, also when it was
  approved: an edited string needs approving again.
- Ctrl+↑ / Ctrl+↓ move through the list. The list can be filtered by state
  and by search text (source or translation), narrowed to the strings like
  the selected one, and sorted by clicking a column title (again to reverse).
  Sorting by source ignores case and numbers first, so similar strings sit
  next to each other. Rich-text sources show as their visible text.
- Several selected strings (Shift/Ctrl-click, Ctrl+A) are approved or
  rejected at once; empty translations are skipped.
- **Toistot yhdessä** (off by default): strings with exactly the same source
  share one translation and one decision. The × column shows how many there
  are. A plain-text source and a rich-text one with the same visible text are
  not repeats of each other.
- The list and the notes can be hidden.
- **Teema**: light, dark or the system's choice (the default), on the Sun
  Valley ttk theme (sv-ttk). The choice is kept in `virtaal-review.json`
  beside Virtaal's own settings.
- **Line breaks** show as plain line breaks and are written back in the
  form the source uses (CR, CRLF or LF; CR when the source has none). A file
  with CRs outside `xml:space="preserve"` is refused, since translate-toolkit
  would turn those CRs into spaces.
- **Saving**: a timestamped backup (`name.backup-YYYYmmdd-HHMMSS.xliff`) is
  copied when the file is opened. Saves go to a temp file in the same folder
  and replace the original in one step, 1.5 s after each change, on Ctrl+S
  and on close. If a save fails, the window says so and does not close.

## Tests

```powershell
uv run pytest
```
