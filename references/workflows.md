# Workflows

## Read-only triage

1. Run `check-config`.
2. Run `folders` if the target folder is unknown.
3. Run `search` with the narrowest useful filters.
4. Fetch only the selected UIDs.
5. Summarize from structured JSON fields and body excerpts.

Example:

```bash
python scripts/email_cli.py search --folder INBOX --unseen --since 2026-07-01 --limit 20
python scripts/email_cli.py fetch --folder INBOX --uid <uid> --max-chars 12000
```

## Thread summary

1. Fetch the seed message.
2. Run `thread` on the seed UID.
3. Summarize messages in date order.
4. Call out open questions, decisions, deadlines, and requested actions.

```bash
python scripts/email_cli.py thread --folder INBOX --uid <uid> --limit 50
```

## Attachment save

1. Fetch the message and inspect `attachments`.
2. Ask the user which attachments to save and where.
3. Run `save-attachments` only after confirmation. Omit `--out` to use the managed attachments directory, or pass an explicit user-requested path.

```bash
python scripts/email_cli.py save-attachments --folder INBOX --uid <uid> --confirm
```

## Send, reply, or forward

1. Draft the message text for the user.
2. Show recipients, subject, body summary, and attachment paths.
3. Ask for explicit confirmation.
4. Run `stage-compose` to save compose JSON in the managed compose directory.
5. Run the state-changing command with `--confirm`.
6. Run `cleanup-artifacts --confirm` after the user no longer needs local intermediates.

```bash
python scripts/email_cli.py stage-compose --stdin
python scripts/email_cli.py send --input <returned-compose-path> --confirm
python scripts/email_cli.py reply --folder INBOX --uid <uid> --input <returned-compose-path> --confirm
python scripts/email_cli.py forward --folder INBOX --uid <uid> --input <returned-compose-path> --confirm
```

## Delete, move, mark, flag

1. Show the folder, UID, subject if available, and intended state change.
2. Ask for explicit confirmation.
3. Run the command with `--confirm`.

```bash
python scripts/email_cli.py mark --folder INBOX --uid <uid> --read --confirm
python scripts/email_cli.py move --from-folder INBOX --uid <uid> --to-folder Archive --confirm
python scripts/email_cli.py delete --folder INBOX --uid <uid> --confirm
```

`delete` moves to `EMAIL_TRASH_FOLDER` when configured. Without a trash folder, it only performs direct deletion when the server supports UIDPLUS/UID EXPUNGE; otherwise it refuses the operation to avoid expunging unrelated deleted messages in the same folder.

## Artifact management

Use `artifacts` to inspect managed intermediate files without printing their contents:

```bash
python scripts/email_cli.py artifacts
```

Use cleanup only after explicit user confirmation:

```bash
python scripts/email_cli.py cleanup-artifacts --max-age-hours 24 --confirm
python scripts/email_cli.py cleanup-artifacts --all --confirm
```
