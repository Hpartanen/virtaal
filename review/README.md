# virtaal-review

A small Tkinter window for reviewing one XLIFF 1.2 file on Virtaal's core
(its `StoreModel` and Finnish state names), without GTK. Virtaal's own
`virtaal/` package is imported from this checkout and left untouched.

## Start

From this folder, with the environment outside any synced folder:

```powershell
$env:UV_PROJECT_ENVIRONMENT = "$env:LOCALAPPDATA\venvs\virtaal-review"; uv run virtaal-review path\to\review.xliff
```

Without a path, it asks for the file.

## What it does

- **Approve** (Hyväksy, Ctrl+Enter) writes `state="signed-off"` and moves to
  the next open string. An empty target cannot be approved.
- **Reject** (Hylkää, Ctrl+Shift+Enter) writes `state="needs-translation"`
  and keeps the target.
- **Editing** a target makes it `state="translated"`, also when it was
  approved: an edited string needs approving again.
- Ctrl+↑ / Ctrl+↓ move through the list; the filter shows all, open,
  approved or rejected strings.
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
