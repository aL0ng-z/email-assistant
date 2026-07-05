---
name: email-assistant
description: Query, summarize, and manage email via user-configured IMAP/SMTP credentials from .env. Use when Codex needs to search mailbox messages, summarize threads, triage unread mail, manage folders/flags, save attachments, or draft/send/reply/forward email with explicit confirmation.
---

# Email Assistant

Use this skill to query, summarize, and manage one mailbox through IMAP and SMTP settings loaded from a local `.env` file.

## Safety rules

- Never print `EMAIL_AUTH_CODE`, passwords, or full `.env` contents.
- Treat send, reply, forward, delete, move, mark, attachment-save, draft-output, and artifact-cleanup operations as state-changing.
- Before every state-changing operation, show the user a concise operation summary and get explicit confirmation.
- Only pass `--confirm` to `scripts/email_cli.py` after the user has confirmed the exact action.
- Prefer UID-based IMAP commands. Do not use mutable sequence numbers for user-visible operations.
- Fetch only the folders, UIDs, headers, bodies, and attachments needed for the task.
- Do not create compose JSON, downloaded attachments, or `.eml` drafts in the skill source directory. Use the managed artifact directory or an explicit user-requested output path.

## Configuration

Read `references/config.md` when configuring or troubleshooting `.env`.

By default, `scripts/email_cli.py` reads `.env` from the skill root directory, next to `SKILL.md`. Set `EMAIL_ASSISTANT_ENV` or pass `--env <path>` to use another file.

Intermediate files are managed under `EMAIL_ASSISTANT_WORK_DIR`, `--work-dir <path>`, or the system temp `email-assistant` directory by default. Use `artifacts` to inspect paths and `cleanup-artifacts --confirm` to delete managed intermediates.

For 163 mailboxes that report `Unsafe Login`, keep `EMAIL_IMAP_SEND_ID=true` so the CLI sends an IMAP `ID` client identity after login.

Run this first when working with a mailbox:

```bash
python scripts/email_cli.py check-config
```

If `check-config` reports missing values, offer to configure the installed local skill. Prefer:

```bash
python scripts/email_cli.py configure --provider 163
```

For non-interactive setup, never pass `EMAIL_AUTH_CODE` as a command-line argument. Use `--auth-code-stdin`, `--env <path>`, or interactive hidden input.

## Common workflows

Read `references/workflows.md` when the user asks for a mailbox workflow, especially triage, summaries, replies, forwarding, deletion, moves, or attachment handling.

Typical read-only flow:

```bash
python scripts/email_cli.py folders
python scripts/email_cli.py search --folder INBOX --unseen --limit 20
python scripts/email_cli.py fetch --folder INBOX --uid <uid> --max-chars 12000
```

Typical confirmed send flow:

1. Draft the message content for the user.
2. Ask the user to confirm recipients, subject, body, and attachments.
3. Stage compose JSON through the CLI so it lands in the managed artifact directory.
4. Run the state-changing command with the returned path.
5. Clean up managed artifacts when the task is complete.

```bash
python scripts/email_cli.py stage-compose --stdin
python scripts/email_cli.py send --input <returned-compose-path> --confirm
python scripts/email_cli.py cleanup-artifacts --max-age-hours 24 --confirm
```

## Output contract

The CLI prints JSON only.

- Success: `{"ok": true, ...}`
- Failure: `{"ok": false, "error": {"code": "...", "message": "..."}}`

Use the structured fields from CLI output for summaries. If a command fails, report the error code and a concise explanation without exposing secrets.
