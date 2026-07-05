# Configuration

The skill reads mailbox settings from `.env` in the skill root directory by default, next to `SKILL.md`. This avoids agent-specific working-directory ambiguity. Use `EMAIL_ASSISTANT_ENV` or `--env <path>` to point at another file.

Managed intermediate files use a separate work directory. The lookup order is `--work-dir <path>`, process environment `EMAIL_ASSISTANT_WORK_DIR`, `.env` value `EMAIL_ASSISTANT_WORK_DIR`, then the system temp `email-assistant` directory. Prefer an absolute path when setting `EMAIL_ASSISTANT_WORK_DIR`.

After installation, prefer creating the local `.env` with the CLI:

```bash
python scripts/email_cli.py configure --provider 163
```

For unattended setup, pass non-secret values as arguments and send the authorization code on stdin:

```bash
printf '%s\n' '<authorization-code>' | python scripts/email_cli.py configure --provider 163 --username user@163.com --auth-code-stdin --non-interactive
```

Do not pass authorization codes as command-line arguments.

## Required values

- `EMAIL_IMAP_HOST`: IMAP server hostname.
- `EMAIL_IMAP_PORT`: IMAP port. Usually `993` for SSL.
- `EMAIL_IMAP_SSL`: Use `true` for implicit SSL.
- `EMAIL_IMAP_SEND_ID`: Send IMAP `ID` after login. Defaults to `true`; useful for providers such as 163 that may reject unidentified clients.
- `EMAIL_IMAP_ID_NAME`: Client name sent in IMAP `ID`. Defaults to `email-assistant`.
- `EMAIL_IMAP_ID_VERSION`: Client version sent in IMAP `ID`. Defaults to `1.0`.
- `EMAIL_IMAP_ID_VENDOR`: Client vendor sent in IMAP `ID`. Defaults to `email-assistant`.
- `EMAIL_SMTP_HOST`: SMTP server hostname.
- `EMAIL_SMTP_PORT`: SMTP port. Usually `465` for SSL or `587` for STARTTLS.
- `EMAIL_SMTP_SSL`: Use `true` for implicit SSL.
- `EMAIL_SMTP_STARTTLS`: Use `true` for STARTTLS. Do not enable both SSL and STARTTLS.
- `EMAIL_USERNAME`: Mail account username.
- `EMAIL_AUTH_CODE`: Mailbox app password or authorization code.
- `EMAIL_FROM`: Sender address. Defaults to `EMAIL_USERNAME` if omitted.
- `EMAIL_DEFAULT_FOLDER`: Default IMAP folder. Usually `INBOX`.
- `EMAIL_SENT_FOLDER`: Optional sent folder name.
- `EMAIL_DRAFTS_FOLDER`: Optional drafts folder name.
- `EMAIL_TRASH_FOLDER`: Optional trash folder name used by `delete`.
- `EMAIL_TIMEOUT_SECONDS`: Network timeout. Defaults to `30`.
- `EMAIL_ASSISTANT_WORK_DIR`: Optional managed artifact directory for compose JSON, saved attachments, and `.eml` drafts.

## Security

- Never commit `.env`.
- Commit `.env.example` only.
- Do not paste authorization codes into prompts or issue summaries.
- Prefer app passwords or authorization codes over account passwords.
- Use a dedicated test mailbox for development of send, move, and delete workflows.
- In Hermes or other agents, check `python scripts/email_cli.py check-config` and verify `config.env_path` points at the intended `.env`.
- Check `python scripts/email_cli.py artifacts` before and after workflows that create local files, then run `cleanup-artifacts --confirm` when managed intermediates are no longer needed.

## Provider notes

The skill is provider-neutral. Folder names vary by provider and locale. Use `python scripts/email_cli.py folders` to discover actual folder names before moving or deleting messages.

Some providers require enabling IMAP/SMTP in account settings before an authorization code works.

163 personal mailboxes may report `Unsafe Login` when an IMAP client logs in without sending the IMAP `ID` command. Keep `EMAIL_IMAP_SEND_ID=true` unless troubleshooting another provider that rejects the command.
