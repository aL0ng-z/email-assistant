#!/usr/bin/env python3
"""IMAP/SMTP helper CLI for the email-assistant Codex skill."""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import email
from email import policy
from email.header import decode_header, make_header
from email.message import EmailMessage, Message
from email.utils import formatdate, getaddresses, make_msgid, parseaddr
import getpass
import html
import imaplib
import json
import mimetypes
import os
from pathlib import Path
import re
import shutil
import smtplib
import ssl
import sys
import tempfile
from dataclasses import dataclass
from typing import Any, Iterable


ENV_KEY_ORDER = [
    "EMAIL_IMAP_HOST",
    "EMAIL_IMAP_PORT",
    "EMAIL_IMAP_SSL",
    "EMAIL_IMAP_SEND_ID",
    "EMAIL_IMAP_ID_NAME",
    "EMAIL_IMAP_ID_VERSION",
    "EMAIL_IMAP_ID_VENDOR",
    "EMAIL_SMTP_HOST",
    "EMAIL_SMTP_PORT",
    "EMAIL_SMTP_SSL",
    "EMAIL_SMTP_STARTTLS",
    "EMAIL_USERNAME",
    "EMAIL_AUTH_CODE",
    "EMAIL_FROM",
    "EMAIL_DEFAULT_FOLDER",
    "EMAIL_SENT_FOLDER",
    "EMAIL_DRAFTS_FOLDER",
    "EMAIL_TRASH_FOLDER",
    "EMAIL_TIMEOUT_SECONDS",
    "EMAIL_ASSISTANT_WORK_DIR",
]

ENV_KEYS = set(ENV_KEY_ORDER)

PROVIDER_PRESETS = {
    "generic": {
        "EMAIL_IMAP_PORT": "993",
        "EMAIL_IMAP_SSL": "true",
        "EMAIL_IMAP_SEND_ID": "true",
        "EMAIL_IMAP_ID_NAME": "email-assistant",
        "EMAIL_IMAP_ID_VERSION": "1.0",
        "EMAIL_IMAP_ID_VENDOR": "email-assistant",
        "EMAIL_SMTP_PORT": "465",
        "EMAIL_SMTP_SSL": "true",
        "EMAIL_SMTP_STARTTLS": "false",
        "EMAIL_DEFAULT_FOLDER": "INBOX",
        "EMAIL_TIMEOUT_SECONDS": "30",
    },
    "163": {
        "EMAIL_IMAP_HOST": "imap.163.com",
        "EMAIL_IMAP_PORT": "993",
        "EMAIL_IMAP_SSL": "true",
        "EMAIL_IMAP_SEND_ID": "true",
        "EMAIL_IMAP_ID_NAME": "email-assistant",
        "EMAIL_IMAP_ID_VERSION": "1.0",
        "EMAIL_IMAP_ID_VENDOR": "email-assistant",
        "EMAIL_SMTP_HOST": "smtp.163.com",
        "EMAIL_SMTP_PORT": "465",
        "EMAIL_SMTP_SSL": "true",
        "EMAIL_SMTP_STARTTLS": "false",
        "EMAIL_DEFAULT_FOLDER": "INBOX",
        "EMAIL_TIMEOUT_SECONDS": "30",
    },
}

MUTATING_COMMANDS = {
    "save-attachments",
    "mark",
    "move",
    "delete",
    "draft",
    "send",
    "reply",
    "forward",
    "cleanup-artifacts",
}

WORK_DIR_ENV = "EMAIL_ASSISTANT_WORK_DIR"
ARTIFACT_SUBDIRS = ("compose", "attachments", "drafts")


class CliError(Exception):
    """Expected command failure with a stable error code."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class MailConfig:
    env_path: str | None
    env_exists: bool
    imap_host: str
    imap_port: int
    imap_ssl: bool
    imap_send_id: bool
    imap_id_name: str
    imap_id_version: str
    imap_id_vendor: str
    smtp_host: str
    smtp_port: int
    smtp_ssl: bool
    smtp_starttls: bool
    username: str
    auth_code: str
    from_addr: str
    default_folder: str
    sent_folder: str
    drafts_folder: str
    trash_folder: str
    timeout_seconds: int


def parse_bool(value: str | None, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    raise CliError("INVALID_CONFIG", f"Invalid boolean value: {value}")


def parse_int(value: str | None, default: int) -> int:
    if value is None or value == "":
        return default
    try:
        parsed = int(value)
    except ValueError as exc:
        raise CliError("INVALID_CONFIG", f"Invalid integer value: {value}") from exc
    if parsed <= 0:
        raise CliError("INVALID_CONFIG", f"Integer value must be positive: {value}")
    return parsed


def parse_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values

    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            raise CliError("INVALID_ENV", f"Invalid .env line {line_number}: missing '='")
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            raise CliError("INVALID_ENV", f"Invalid .env line {line_number}: empty key")
        if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
            value = value[1:-1]
        values[key] = value
    return values


def skill_root() -> Path:
    return Path(__file__).resolve().parents[1]


def resolve_env_path(explicit_path: str | None) -> Path:
    if explicit_path:
        return Path(explicit_path).expanduser()
    if os.environ.get("EMAIL_ASSISTANT_ENV"):
        return Path(os.environ["EMAIL_ASSISTANT_ENV"]).expanduser()
    return skill_root() / ".env"


def dotenv_value(value: str) -> str:
    if "\n" in value or "\r" in value:
        raise CliError("INVALID_CONFIG", "Config values cannot contain newlines")
    return value


def set_private_permissions(path: Path) -> None:
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def write_dotenv(path: Path, values: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"{key}={dotenv_value(values.get(key, ''))}" for key in ENV_KEY_ORDER]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    set_private_permissions(path)


def env_file_value(key: str, env_path_arg: str | None = None) -> str:
    env_path = resolve_env_path(env_path_arg)
    return parse_dotenv(env_path).get(key, "")


def resolve_work_dir(explicit_path: str | None = None, env_path_arg: str | None = None) -> Path:
    if explicit_path:
        return Path(explicit_path).expanduser()
    if os.environ.get(WORK_DIR_ENV):
        return Path(os.environ[WORK_DIR_ENV]).expanduser()
    configured = env_file_value(WORK_DIR_ENV, env_path_arg)
    if configured:
        return Path(configured).expanduser()
    return Path(tempfile.gettempdir()) / "email-assistant"


def ensure_work_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    marker = path / ".email-assistant-workdir"
    if not marker.exists():
        marker.write_text("managed by email-assistant\n", encoding="utf-8")
        set_private_permissions(marker)


def prompt_text(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    sys.stderr.write(f"{prompt}{suffix}: ")
    sys.stderr.flush()
    value = sys.stdin.readline()
    if value == "":
        raise CliError("CONFIGURE_INPUT_REQUIRED", f"Missing value for {prompt}")
    value = value.rstrip("\r\n")
    return value if value else default


def prompt_secret(prompt: str, default: str = "") -> str:
    suffix = " [keep existing]" if default else ""
    value = getpass.getpass(f"{prompt}{suffix}: ", stream=sys.stderr)
    return value if value else default


def configured_values(path: Path, provider: str) -> dict[str, str]:
    if provider not in PROVIDER_PRESETS:
        raise CliError("INVALID_ARGUMENT", f"Unknown provider: {provider}")
    values = {key: "" for key in ENV_KEY_ORDER}
    values.update(PROVIDER_PRESETS["generic"])
    values.update(PROVIDER_PRESETS[provider])
    values.update({key: value for key, value in parse_dotenv(path).items() if key in ENV_KEYS})
    return values


def command_configure(args: argparse.Namespace) -> int:
    env_path = resolve_env_path(args.env)
    values = configured_values(env_path, args.provider)

    if args.imap_host:
        values["EMAIL_IMAP_HOST"] = args.imap_host
    if args.smtp_host:
        values["EMAIL_SMTP_HOST"] = args.smtp_host
    if args.username:
        values["EMAIL_USERNAME"] = args.username
    if args.from_addr:
        values["EMAIL_FROM"] = args.from_addr
    if args.auth_code_stdin:
        auth_code = sys.stdin.readline().rstrip("\r\n")
        if not auth_code:
            raise CliError("CONFIGURE_INPUT_REQUIRED", "Missing authorization code on stdin")
        values["EMAIL_AUTH_CODE"] = auth_code

    if values["EMAIL_USERNAME"] and not values["EMAIL_FROM"]:
        values["EMAIL_FROM"] = values["EMAIL_USERNAME"]

    interactive = not args.non_interactive and not args.auth_code_stdin
    if interactive:
        values["EMAIL_IMAP_HOST"] = prompt_text("IMAP host", values["EMAIL_IMAP_HOST"])
        values["EMAIL_SMTP_HOST"] = prompt_text("SMTP host", values["EMAIL_SMTP_HOST"])
        values["EMAIL_USERNAME"] = prompt_text("Email username", values["EMAIL_USERNAME"])
        values["EMAIL_FROM"] = prompt_text("From address", values["EMAIL_FROM"] or values["EMAIL_USERNAME"])
        values["EMAIL_AUTH_CODE"] = prompt_secret("Email authorization code", values["EMAIL_AUTH_CODE"])

    missing = [
        key
        for key in ("EMAIL_IMAP_HOST", "EMAIL_SMTP_HOST", "EMAIL_USERNAME", "EMAIL_AUTH_CODE", "EMAIL_FROM")
        if not values.get(key)
    ]
    if missing:
        raise CliError("MISSING_CONFIG", f"Missing required config: {', '.join(missing)}")

    write_dotenv(env_path, values)
    return json_success(
        env_path=str(env_path.resolve()),
        provider=args.provider,
        written_keys=ENV_KEY_ORDER,
        auth_code_configured=bool(values["EMAIL_AUTH_CODE"]),
    )


def load_config(env_path_arg: str | None = None) -> MailConfig:
    env_path = resolve_env_path(env_path_arg)
    file_values = parse_dotenv(env_path)
    values = {key: file_values.get(key, "") for key in ENV_KEYS}
    for key in ENV_KEYS:
        if os.environ.get(key) is not None:
            values[key] = os.environ[key]

    from_addr = values["EMAIL_FROM"] or values["EMAIL_USERNAME"]
    smtp_ssl = parse_bool(values["EMAIL_SMTP_SSL"], True)
    smtp_starttls = parse_bool(values["EMAIL_SMTP_STARTTLS"], False)
    if smtp_ssl and smtp_starttls:
        raise CliError("INVALID_CONFIG", "EMAIL_SMTP_SSL and EMAIL_SMTP_STARTTLS cannot both be true")

    return MailConfig(
        env_path=str(env_path),
        env_exists=env_path.exists(),
        imap_host=values["EMAIL_IMAP_HOST"],
        imap_port=parse_int(values["EMAIL_IMAP_PORT"], 993),
        imap_ssl=parse_bool(values["EMAIL_IMAP_SSL"], True),
        imap_send_id=parse_bool(values["EMAIL_IMAP_SEND_ID"], True),
        imap_id_name=values["EMAIL_IMAP_ID_NAME"] or "email-assistant",
        imap_id_version=values["EMAIL_IMAP_ID_VERSION"] or "1.0",
        imap_id_vendor=values["EMAIL_IMAP_ID_VENDOR"] or "email-assistant",
        smtp_host=values["EMAIL_SMTP_HOST"],
        smtp_port=parse_int(values["EMAIL_SMTP_PORT"], 465),
        smtp_ssl=smtp_ssl,
        smtp_starttls=smtp_starttls,
        username=values["EMAIL_USERNAME"],
        auth_code=values["EMAIL_AUTH_CODE"],
        from_addr=from_addr,
        default_folder=values["EMAIL_DEFAULT_FOLDER"] or "INBOX",
        sent_folder=values["EMAIL_SENT_FOLDER"],
        drafts_folder=values["EMAIL_DRAFTS_FOLDER"],
        trash_folder=values["EMAIL_TRASH_FOLDER"],
        timeout_seconds=parse_int(values["EMAIL_TIMEOUT_SECONDS"], 30),
    )


def validate_config(config: MailConfig, *, need_imap: bool = False, need_smtp: bool = False) -> None:
    missing: list[str] = []
    if need_imap:
        for key, value in {
            "EMAIL_IMAP_HOST": config.imap_host,
            "EMAIL_USERNAME": config.username,
            "EMAIL_AUTH_CODE": config.auth_code,
        }.items():
            if not value:
                missing.append(key)
    if need_smtp:
        for key, value in {
            "EMAIL_SMTP_HOST": config.smtp_host,
            "EMAIL_USERNAME": config.username,
            "EMAIL_AUTH_CODE": config.auth_code,
            "EMAIL_FROM": config.from_addr,
        }.items():
            if not value:
                missing.append(key)
    if missing:
        raise CliError("MISSING_CONFIG", f"Missing required config: {', '.join(sorted(set(missing)))}")


def safe_config(config: MailConfig) -> dict[str, Any]:
    return {
        "env_path": config.env_path,
        "env_exists": config.env_exists,
        "imap": {
            "host_configured": bool(config.imap_host),
            "port": config.imap_port,
            "ssl": config.imap_ssl,
            "send_id": config.imap_send_id,
            "id_name": config.imap_id_name,
            "id_version": config.imap_id_version,
            "id_vendor": config.imap_id_vendor,
        },
        "smtp": {
            "host_configured": bool(config.smtp_host),
            "port": config.smtp_port,
            "ssl": config.smtp_ssl,
            "starttls": config.smtp_starttls,
        },
        "username_configured": bool(config.username),
        "auth_code_configured": bool(config.auth_code),
        "from_configured": bool(config.from_addr),
        "default_folder": config.default_folder,
        "sent_folder_configured": bool(config.sent_folder),
        "drafts_folder_configured": bool(config.drafts_folder),
        "trash_folder_configured": bool(config.trash_folder),
        "timeout_seconds": config.timeout_seconds,
    }


def require_confirm(args: argparse.Namespace, action: str) -> None:
    if not getattr(args, "confirm", False):
        raise CliError("CONFIRMATION_REQUIRED", f"{action} requires --confirm after explicit user approval")


def json_success(**payload: Any) -> int:
    result = {"ok": True}
    result.update(payload)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def json_error(error: CliError) -> int:
    print(
        json.dumps(
            {"ok": False, "error": {"code": error.code, "message": error.message}},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 1


def decode_header_value(value: Any) -> str:
    if value is None:
        return ""
    try:
        return str(make_header(decode_header(str(value))))
    except Exception:
        return str(value)


def decode_addresses(values: Iterable[str]) -> list[dict[str, str]]:
    decoded_values = [decode_header_value(value) for value in values if value]
    addresses: list[dict[str, str]] = []
    for name, address in getaddresses(decoded_values):
        addresses.append({"name": decode_header_value(name), "address": address})
    return addresses


def html_to_text(value: str) -> str:
    value = re.sub(r"(?is)<(script|style).*?>.*?</\1>", "", value)
    value = re.sub(r"(?i)<br\s*/?>", "\n", value)
    value = re.sub(r"(?i)</p\s*>", "\n\n", value)
    value = re.sub(r"(?s)<[^>]+>", " ", value)
    value = html.unescape(value)
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r"\n\s+", "\n", value)
    return value.strip()


def part_text(part: Message) -> str:
    payload = part.get_payload(decode=True)
    if payload is None:
        raw_payload = part.get_payload()
        if isinstance(raw_payload, str):
            return raw_payload
        return ""
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except LookupError:
        return payload.decode("utf-8", errors="replace")


def is_attachment(part: Message) -> bool:
    disposition = (part.get_content_disposition() or "").lower()
    return disposition == "attachment" or bool(part.get_filename())


def extract_body(message: Message, max_chars: int | None = None) -> dict[str, Any]:
    plain_parts: list[str] = []
    html_parts: list[str] = []

    if message.is_multipart():
        for part in message.walk():
            if part.is_multipart() or is_attachment(part):
                continue
            content_type = part.get_content_type().lower()
            if content_type == "text/plain":
                plain_parts.append(part_text(part))
            elif content_type == "text/html":
                html_parts.append(html_to_text(part_text(part)))
    else:
        content_type = message.get_content_type().lower()
        if content_type == "text/plain":
            plain_parts.append(part_text(message))
        elif content_type == "text/html":
            html_parts.append(html_to_text(part_text(message)))

    body_type = "plain" if plain_parts else "html" if html_parts else "none"
    text = "\n\n".join(plain_parts or html_parts).strip()
    truncated = False
    if max_chars is not None and max_chars >= 0 and len(text) > max_chars:
        text = text[:max_chars]
        truncated = True
    return {"type": body_type, "text": text, "truncated": truncated, "chars": len(text)}


def attachment_info(message: Message) -> list[dict[str, Any]]:
    attachments: list[dict[str, Any]] = []
    if not message.is_multipart():
        return attachments
    for index, part in enumerate(message.walk()):
        if part.is_multipart() or not is_attachment(part):
            continue
        payload = part.get_payload(decode=True) or b""
        filename = decode_header_value(part.get_filename()) or f"attachment-{index}"
        attachments.append(
            {
                "index": index,
                "filename": filename,
                "content_type": part.get_content_type(),
                "content_id": part.get("Content-ID", ""),
                "size": len(payload),
            }
        )
    return attachments


def message_metadata(message: Message, uid: str | None = None) -> dict[str, Any]:
    return {
        "uid": uid,
        "subject": decode_header_value(message.get("Subject")),
        "from": decode_addresses(message.get_all("From", [])),
        "to": decode_addresses(message.get_all("To", [])),
        "cc": decode_addresses(message.get_all("Cc", [])),
        "bcc": decode_addresses(message.get_all("Bcc", [])),
        "date": decode_header_value(message.get("Date")),
        "message_id": decode_header_value(message.get("Message-ID")),
        "in_reply_to": decode_header_value(message.get("In-Reply-To")),
        "references": decode_header_value(message.get("References")),
    }


def parse_message_bytes(data: bytes) -> Message:
    return email.message_from_bytes(data, policy=policy.default)


def message_to_json(message: Message, uid: str | None = None, max_chars: int | None = None) -> dict[str, Any]:
    data = message_metadata(message, uid)
    data["body"] = extract_body(message, max_chars)
    data["attachments"] = attachment_info(message)
    return data


def quote_mailbox(folder: str) -> str:
    if folder.upper() == "INBOX":
        return "INBOX"
    escaped = folder.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def connect_imap(config: MailConfig) -> imaplib.IMAP4:
    validate_config(config, need_imap=True)
    try:
        if config.imap_ssl:
            client = imaplib.IMAP4_SSL(config.imap_host, config.imap_port, timeout=config.timeout_seconds)
        else:
            client = imaplib.IMAP4(config.imap_host, config.imap_port, timeout=config.timeout_seconds)
        status, _ = client.login(config.username, config.auth_code)
        if status != "OK":
            raise CliError("IMAP_LOGIN_FAILED", "IMAP login failed")
        send_imap_id(client, config)
        return client
    except CliError:
        raise
    except Exception as exc:
        raise CliError("IMAP_ERROR", f"IMAP connection failed: {exc.__class__.__name__}: {exc}") from exc


def imap_quoted(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def build_imap_id_payload(config: MailConfig) -> str:
    fields = {
        "name": config.imap_id_name,
        "version": config.imap_id_version,
        "vendor": config.imap_id_vendor,
    }
    pairs: list[str] = []
    for key, value in fields.items():
        if value:
            pairs.extend([imap_quoted(key), imap_quoted(value)])
    return "(" + " ".join(pairs) + ")"


def send_imap_id(client: imaplib.IMAP4, config: MailConfig) -> bool:
    if not config.imap_send_id:
        return False
    imaplib.Commands["ID"] = ("AUTH", "SELECTED")
    try:
        status, _ = client._simple_command("ID", build_imap_id_payload(config))
    except Exception:
        return False
    return status == "OK"


def select_folder(client: imaplib.IMAP4, folder: str, *, readonly: bool = True) -> None:
    status, data = client.select(quote_mailbox(folder), readonly=readonly)
    if status != "OK":
        details = " ".join(item.decode("utf-8", "replace") if isinstance(item, bytes) else str(item) for item in data)
        raise CliError("IMAP_SELECT_FAILED", f"Could not select folder {folder}: {details}")


def imap_logout(client: imaplib.IMAP4) -> None:
    try:
        client.logout()
    except Exception:
        pass


def has_capability(client: imaplib.IMAP4, capability: str) -> bool:
    wanted = capability.upper()
    existing = getattr(client, "capabilities", ()) or ()
    normalized = {
        item.decode("ascii", "ignore").upper() if isinstance(item, bytes) else str(item).upper()
        for item in existing
    }
    if wanted in normalized:
        return True
    try:
        status, data = client.capability()
    except Exception:
        return False
    if status != "OK":
        return False
    for item in data:
        text = item.decode("ascii", "ignore") if isinstance(item, bytes) else str(item)
        normalized.update(token.upper() for token in text.split())
    return wanted in normalized


def imap_date(value: str) -> str:
    try:
        parsed = dt.datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise CliError("INVALID_ARGUMENT", f"Invalid date {value}; expected YYYY-MM-DD") from exc
    return parsed.strftime("%d-%b-%Y")


def quote_search_value(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def build_search_criteria(args: argparse.Namespace) -> str:
    criteria: list[str] = []
    if getattr(args, "unseen", False):
        criteria.append("UNSEEN")
    if getattr(args, "seen", False):
        criteria.append("SEEN")
    if getattr(args, "from_addr", None):
        criteria.extend(["FROM", quote_search_value(args.from_addr)])
    if getattr(args, "to", None):
        criteria.extend(["TO", quote_search_value(args.to)])
    if getattr(args, "subject", None):
        criteria.extend(["SUBJECT", quote_search_value(args.subject)])
    if getattr(args, "since", None):
        criteria.extend(["SINCE", imap_date(args.since)])
    if getattr(args, "before", None):
        criteria.extend(["BEFORE", imap_date(args.before)])
    return " ".join(criteria) if criteria else "ALL"


def uid_search(client: imaplib.IMAP4, criteria: str) -> list[str]:
    status, data = client.uid("SEARCH", None, criteria)
    if status != "OK":
        raise CliError("IMAP_SEARCH_FAILED", f"IMAP search failed for criteria: {criteria}")
    raw = data[0] or b""
    if isinstance(raw, str):
        raw_text = raw
    else:
        raw_text = raw.decode("ascii", "ignore")
    return [uid for uid in raw_text.split() if uid]


def response_message_bytes(fetch_data: list[Any]) -> bytes:
    chunks: list[bytes] = []
    for item in fetch_data:
        if isinstance(item, tuple) and len(item) >= 2 and isinstance(item[1], bytes):
            chunks.append(item[1])
    if not chunks:
        raise CliError("IMAP_FETCH_FAILED", "IMAP fetch returned no message bytes")
    return b"\r\n".join(chunks)


def fetch_message(client: imaplib.IMAP4, uid: str, *, full: bool = True) -> Message:
    item = "BODY.PEEK[]" if full else "BODY.PEEK[HEADER]"
    status, data = client.uid("FETCH", uid, f"({item})")
    if status != "OK":
        raise CliError("IMAP_FETCH_FAILED", f"Could not fetch UID {uid}")
    return parse_message_bytes(response_message_bytes(data))


def fetch_summary(client: imaplib.IMAP4, uid: str) -> dict[str, Any]:
    fields = "FROM TO CC SUBJECT DATE MESSAGE-ID IN-REPLY-TO REFERENCES"
    status, data = client.uid(
        "FETCH",
        uid,
        f"(FLAGS RFC822.SIZE BODY.PEEK[HEADER.FIELDS ({fields})])",
    )
    if status != "OK":
        raise CliError("IMAP_FETCH_FAILED", f"Could not fetch summary for UID {uid}")
    metadata_blob = b" ".join(
        item[0] if isinstance(item, tuple) and isinstance(item[0], bytes) else item
        for item in data
        if isinstance(item, (tuple, bytes))
    )
    flags_match = re.search(rb"FLAGS \((.*?)\)", metadata_blob)
    size_match = re.search(rb"RFC822\.SIZE (\d+)", metadata_blob)
    message = parse_message_bytes(response_message_bytes(data))
    result = message_metadata(message, uid)
    result["flags"] = flags_match.group(1).decode("utf-8", "replace").split() if flags_match else []
    result["size"] = int(size_match.group(1)) if size_match else None
    return result


def list_folders(config: MailConfig) -> dict[str, Any]:
    client = connect_imap(config)
    try:
        status, data = client.list()
        if status != "OK":
            raise CliError("IMAP_LIST_FAILED", "Could not list folders")
        folders = []
        for raw in data:
            if not raw:
                continue
            line = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
            match = re.match(r"\((?P<flags>.*?)\)\s+\"(?P<delimiter>.*?)\"\s+(?P<name>.*)", line)
            if match:
                name = match.group("name").strip()
                if name.startswith('"') and name.endswith('"'):
                    name = name[1:-1]
                folders.append(
                    {
                        "name": name,
                        "delimiter": match.group("delimiter"),
                        "flags": match.group("flags").split(),
                    }
                )
            else:
                folders.append({"name": line, "delimiter": None, "flags": []})
        return {"folders": folders}
    finally:
        imap_logout(client)


def command_check_config(args: argparse.Namespace) -> int:
    config = load_config(args.env)
    imap_missing = []
    smtp_missing = []
    for key, value in {
        "EMAIL_IMAP_HOST": config.imap_host,
        "EMAIL_USERNAME": config.username,
        "EMAIL_AUTH_CODE": config.auth_code,
    }.items():
        if not value:
            imap_missing.append(key)
    for key, value in {
        "EMAIL_SMTP_HOST": config.smtp_host,
        "EMAIL_USERNAME": config.username,
        "EMAIL_AUTH_CODE": config.auth_code,
        "EMAIL_FROM": config.from_addr,
    }.items():
        if not value:
            smtp_missing.append(key)
    return json_success(
        config=safe_config(config),
        imap_missing=imap_missing,
        smtp_missing=smtp_missing,
        artifacts=managed_artifact_summary(env_path_arg=args.env),
    )


def command_folders(args: argparse.Namespace) -> int:
    config = load_config(args.env)
    return json_success(**list_folders(config))


def command_search(args: argparse.Namespace) -> int:
    config = load_config(args.env)
    folder = args.folder or config.default_folder
    client = connect_imap(config)
    try:
        select_folder(client, folder, readonly=True)
        criteria = build_search_criteria(args)
        uids = uid_search(client, criteria)
        if not args.oldest_first:
            uids = list(reversed(uids))
        if args.limit is not None:
            uids = uids[: args.limit]
        messages = [fetch_summary(client, uid) for uid in uids]
        return json_success(folder=folder, criteria=criteria, count=len(messages), messages=messages)
    finally:
        imap_logout(client)


def command_fetch(args: argparse.Namespace) -> int:
    config = load_config(args.env)
    folder = args.folder or config.default_folder
    client = connect_imap(config)
    try:
        select_folder(client, folder, readonly=True)
        message = fetch_message(client, args.uid, full=True)
        return json_success(folder=folder, message=message_to_json(message, args.uid, args.max_chars))
    finally:
        imap_logout(client)


def extract_message_ids(message: Message) -> list[str]:
    values = [
        decode_header_value(message.get("Message-ID")),
        decode_header_value(message.get("In-Reply-To")),
        decode_header_value(message.get("References")),
    ]
    ids: list[str] = []
    for value in values:
        for match in re.findall(r"<[^>]+>", value):
            if match not in ids:
                ids.append(match)
    return ids


def command_thread(args: argparse.Namespace) -> int:
    config = load_config(args.env)
    folder = args.folder or config.default_folder
    client = connect_imap(config)
    try:
        select_folder(client, folder, readonly=True)
        root = fetch_message(client, args.uid, full=True)
        message_ids = extract_message_ids(root)
        uids = {args.uid}
        for message_id in message_ids[:10]:
            for header in ("Message-ID", "In-Reply-To", "References"):
                try:
                    found = uid_search(client, f'HEADER {header} {quote_search_value(message_id)}')
                    uids.update(found)
                except CliError:
                    continue
        ordered_uids = sorted(uids, key=lambda value: int(value) if value.isdigit() else value)
        ordered_uids = ordered_uids[: args.limit]
        messages = [message_to_json(fetch_message(client, uid, full=True), uid, args.max_chars) for uid in ordered_uids]
        return json_success(folder=folder, seed_uid=args.uid, count=len(messages), messages=messages)
    finally:
        imap_logout(client)


def safe_filename(name: str, fallback: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip().strip(".")
    return cleaned or fallback


def unique_path(directory: Path, filename: str) -> Path:
    candidate = directory / filename
    if not candidate.exists():
        return candidate
    stem = candidate.stem
    suffix = candidate.suffix
    for index in range(1, 10000):
        next_candidate = directory / f"{stem}-{index}{suffix}"
        if not next_candidate.exists():
            return next_candidate
    raise CliError("FILE_ERROR", f"Could not create unique filename for {filename}")


def artifact_dir(kind: str, work_dir_arg: str | None = None, env_path_arg: str | None = None, *, create: bool = True) -> Path:
    if kind not in ARTIFACT_SUBDIRS:
        raise CliError("INVALID_ARGUMENT", f"Unknown artifact kind: {kind}")
    work_dir = resolve_work_dir(work_dir_arg, env_path_arg)
    target = work_dir / kind
    if create:
        ensure_work_dir(work_dir)
        target.mkdir(parents=True, exist_ok=True)
    return target


def timestamped_artifact_path(
    kind: str,
    prefix: str,
    suffix: str,
    work_dir_arg: str | None = None,
    env_path_arg: str | None = None,
) -> Path:
    timestamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    filename = f"{safe_filename(prefix, 'artifact')}-{timestamp}{suffix}"
    return unique_path(artifact_dir(kind, work_dir_arg, env_path_arg), filename)


def managed_artifact_summary(work_dir_arg: str | None = None, env_path_arg: str | None = None) -> dict[str, Any]:
    work_dir = resolve_work_dir(work_dir_arg, env_path_arg)
    return {
        "work_dir": str(work_dir),
        "env_var": WORK_DIR_ENV,
        "exists": work_dir.exists(),
        "subdirs": {kind: str(work_dir / kind) for kind in ARTIFACT_SUBDIRS},
    }


def artifact_file_info(path: Path, work_dir: Path) -> dict[str, Any]:
    stat = path.stat()
    relative = path.relative_to(work_dir)
    return {
        "kind": relative.parts[0] if relative.parts else "",
        "path": str(path.resolve()),
        "relative_path": str(relative),
        "size": stat.st_size,
        "modified": dt.datetime.fromtimestamp(stat.st_mtime, dt.timezone.utc).isoformat(),
    }


def list_artifact_files(work_dir: Path, limit: int) -> list[dict[str, Any]]:
    if not work_dir.exists():
        return []
    files: list[Path] = []
    for kind in ARTIFACT_SUBDIRS:
        base = work_dir / kind
        if not base.exists():
            continue
        files.extend(path for path in base.rglob("*") if path.is_file() or path.is_symlink())
    files.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    return [artifact_file_info(path, work_dir) for path in files[:limit]]


def assert_artifact_subdir_is_safe(work_dir: Path, base: Path) -> None:
    if not base.exists():
        return
    try:
        base.resolve().relative_to(work_dir.resolve())
    except ValueError as exc:
        raise CliError("UNSAFE_WORK_DIR", f"Artifact subdir escapes work dir: {base}") from exc


def remove_empty_dirs(base: Path) -> None:
    if not base.exists():
        return
    for path in sorted((item for item in base.rglob("*") if item.is_dir()), key=lambda item: len(item.parts), reverse=True):
        try:
            path.rmdir()
        except OSError:
            pass
    try:
        base.rmdir()
    except OSError:
        pass


def command_artifacts(args: argparse.Namespace) -> int:
    if args.limit < 0:
        raise CliError("INVALID_ARGUMENT", "--limit must be non-negative")
    work_dir = resolve_work_dir(args.work_dir, args.env)
    summary = managed_artifact_summary(args.work_dir, args.env)
    summary["files"] = list_artifact_files(work_dir, args.limit)
    summary["count"] = len(summary["files"])
    return json_success(artifacts=summary)


def command_cleanup_artifacts(args: argparse.Namespace) -> int:
    require_confirm(args, "Cleaning artifacts")
    if args.max_age_hours < 0:
        raise CliError("INVALID_ARGUMENT", "--max-age-hours must be non-negative")
    work_dir = resolve_work_dir(args.work_dir, args.env)
    if not work_dir.exists():
        return json_success(work_dir=str(work_dir), removed=[])

    cutoff = None if args.all else dt.datetime.now(dt.timezone.utc).timestamp() - (args.max_age_hours * 3600)
    removed: list[dict[str, Any]] = []
    for kind in ARTIFACT_SUBDIRS:
        base = work_dir / kind
        assert_artifact_subdir_is_safe(work_dir, base)
        if not base.exists():
            continue
        for path in sorted(base.rglob("*"), key=lambda item: len(item.parts), reverse=True):
            if not (path.is_file() or path.is_symlink()):
                continue
            stat = path.stat()
            if cutoff is not None and stat.st_mtime >= cutoff:
                continue
            info = artifact_file_info(path, work_dir)
            path.unlink()
            removed.append(info)
        remove_empty_dirs(base)
    return json_success(work_dir=str(work_dir), removed=removed, count=len(removed))


def read_json_object(path: str | None, *, use_stdin: bool = False) -> dict[str, Any]:
    try:
        if use_stdin:
            raw = sys.stdin.read()
        elif path:
            raw = Path(path).read_text(encoding="utf-8")
        else:
            raise CliError("INVALID_ARGUMENT", "Choose --input or --stdin")
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CliError("INVALID_JSON", f"JSON is invalid: {exc}") from exc
    except OSError as exc:
        raise CliError("FILE_ERROR", f"Could not read JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise CliError("INVALID_JSON", "JSON must be an object")
    return data


def command_stage_compose(args: argparse.Namespace) -> int:
    compose = normalize_compose(read_json_object(args.input, use_stdin=args.stdin))
    out_path = (
        Path(args.out).expanduser()
        if args.out
        else timestamped_artifact_path("compose", "compose", ".json", args.work_dir, args.env)
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(compose, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    set_private_permissions(out_path)
    return json_success(
        compose={
            "path": str(out_path.resolve()),
            "to": compose["to"],
            "cc": compose["cc"],
            "bcc_count": len(compose["bcc"]),
            "subject": compose["subject"],
            "body_text_chars": len(compose["body_text"]),
            "body_html_chars": len(compose["body_html"] or ""),
            "attachments": compose["attachments"],
        }
    )


def command_save_attachments(args: argparse.Namespace) -> int:
    require_confirm(args, "Saving attachments")
    config = load_config(args.env)
    folder = args.folder or config.default_folder
    out_dir = Path(args.out).expanduser() if args.out else artifact_dir("attachments", args.work_dir, args.env)
    client = connect_imap(config)
    try:
        select_folder(client, folder, readonly=True)
        message = fetch_message(client, args.uid, full=True)
        out_dir.mkdir(parents=True, exist_ok=True)
        saved = []
        for index, part in enumerate(message.walk()):
            if part.is_multipart() or not is_attachment(part):
                continue
            if args.indices and index not in args.indices:
                continue
            payload = part.get_payload(decode=True) or b""
            filename = safe_filename(decode_header_value(part.get_filename()), f"attachment-{index}")
            target = unique_path(out_dir, filename)
            target.write_bytes(payload)
            saved.append(
                {
                    "index": index,
                    "filename": filename,
                    "path": str(target.resolve()),
                    "content_type": part.get_content_type(),
                    "size": len(payload),
                }
            )
        return json_success(folder=folder, uid=args.uid, saved=saved)
    finally:
        imap_logout(client)


def command_mark(args: argparse.Namespace) -> int:
    require_confirm(args, "Marking messages")
    config = load_config(args.env)
    folder = args.folder or config.default_folder
    flag_action = None
    if args.read:
        flag_action = ("+FLAGS", r"(\Seen)")
    elif args.unread:
        flag_action = ("-FLAGS", r"(\Seen)")
    elif args.flag:
        flag_action = ("+FLAGS", r"(\Flagged)")
    elif args.unflag:
        flag_action = ("-FLAGS", r"(\Flagged)")
    if flag_action is None:
        raise CliError("INVALID_ARGUMENT", "Choose one mark action")

    client = connect_imap(config)
    try:
        select_folder(client, folder, readonly=False)
        status, data = client.uid("STORE", args.uid, flag_action[0], flag_action[1])
        if status != "OK":
            raise CliError("IMAP_STORE_FAILED", f"Could not update flags for UID {args.uid}: {data}")
        return json_success(folder=folder, uid=args.uid, action=flag_action[0], flag=flag_action[1])
    finally:
        imap_logout(client)


def move_uid(client: imaplib.IMAP4, uid: str, to_folder: str) -> str:
    status, _ = client.uid("MOVE", uid, quote_mailbox(to_folder))
    if status == "OK":
        return "MOVE"
    if not has_capability(client, "UIDPLUS"):
        raise CliError(
            "IMAP_MOVE_FAILED",
            "UID MOVE failed and server lacks UIDPLUS; refusing unsafe folder-wide EXPUNGE fallback",
        )
    status, data = client.uid("COPY", uid, quote_mailbox(to_folder))
    if status != "OK":
        raise CliError("IMAP_MOVE_FAILED", f"Could not copy UID {uid} to {to_folder}: {data}")
    status, data = client.uid("STORE", uid, "+FLAGS", r"(\Deleted)")
    if status != "OK":
        raise CliError("IMAP_MOVE_FAILED", f"Copied UID {uid}, but could not mark original deleted: {data}")
    status, data = client.uid("EXPUNGE", uid)
    if status != "OK":
        client.uid("STORE", uid, "-FLAGS", r"(\Deleted)")
        raise CliError(
            "IMAP_MOVE_FAILED",
            f"Copied UID {uid}, but UID EXPUNGE failed; original was kept: {data}",
        )
    return "COPY_UID_EXPUNGE"


def command_move(args: argparse.Namespace) -> int:
    require_confirm(args, "Moving messages")
    config = load_config(args.env)
    from_folder = args.from_folder or config.default_folder
    client = connect_imap(config)
    try:
        select_folder(client, from_folder, readonly=False)
        method = move_uid(client, args.uid, args.to_folder)
        return json_success(from_folder=from_folder, to_folder=args.to_folder, uid=args.uid, method=method)
    finally:
        imap_logout(client)


def command_delete(args: argparse.Namespace) -> int:
    require_confirm(args, "Deleting messages")
    config = load_config(args.env)
    folder = args.folder or config.default_folder
    client = connect_imap(config)
    try:
        select_folder(client, folder, readonly=False)
        if config.trash_folder:
            method = move_uid(client, args.uid, config.trash_folder)
            return json_success(folder=folder, trash_folder=config.trash_folder, uid=args.uid, method=method)
        if not has_capability(client, "UIDPLUS"):
            raise CliError(
                "IMAP_DELETE_FAILED",
                "EMAIL_TRASH_FOLDER is not configured and server lacks UIDPLUS; refusing unsafe folder-wide EXPUNGE",
            )
        status, data = client.uid("STORE", args.uid, "+FLAGS", r"(\Deleted)")
        if status != "OK":
            raise CliError("IMAP_DELETE_FAILED", f"Could not mark UID {args.uid} deleted: {data}")
        status, data = client.uid("EXPUNGE", args.uid)
        if status != "OK":
            client.uid("STORE", args.uid, "-FLAGS", r"(\Deleted)")
            raise CliError("IMAP_DELETE_FAILED", f"UID EXPUNGE failed; original was kept: {data}")
        return json_success(folder=folder, uid=args.uid, method="UID_EXPUNGE")
    finally:
        imap_logout(client)


def load_compose(path: str) -> dict[str, Any]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CliError("INVALID_COMPOSE", f"Compose JSON is invalid: {exc}") from exc
    except OSError as exc:
        raise CliError("FILE_ERROR", f"Could not read compose JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise CliError("INVALID_COMPOSE", "Compose JSON must be an object")
    return data


def as_string_list(value: Any, field: str) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, str):
        values = [value]
    elif isinstance(value, list) and all(isinstance(item, str) for item in value):
        values = value
    else:
        raise CliError("INVALID_COMPOSE", f"{field} must be a string or list of strings")
    return [item.strip() for item in values if item.strip()]


def validate_addresses(addresses: list[str], field: str, *, required: bool = False) -> list[str]:
    if required and not addresses:
        raise CliError("INVALID_COMPOSE", f"{field} must contain at least one address")
    for address in addresses:
        parsed = parseaddr(address)[1]
        if not parsed or "@" not in parsed:
            raise CliError("INVALID_COMPOSE", f"Invalid address in {field}: {address}")
    return addresses


def normalize_compose(
    data: dict[str, Any],
    *,
    require_to: bool = True,
    require_subject: bool = True,
) -> dict[str, Any]:
    to = validate_addresses(as_string_list(data.get("to"), "to"), "to", required=require_to)
    cc = validate_addresses(as_string_list(data.get("cc"), "cc"), "cc")
    bcc = validate_addresses(as_string_list(data.get("bcc"), "bcc"), "bcc")
    subject = str(data.get("subject") or "").strip()
    body_text = str(data.get("body_text") or "")
    body_html = data.get("body_html")
    if body_html is not None:
        body_html = str(body_html)
    attachments = as_string_list(data.get("attachments"), "attachments")
    if require_subject and not subject:
        raise CliError("INVALID_COMPOSE", "subject is required")
    if not body_text and not body_html:
        raise CliError("INVALID_COMPOSE", "body_text or body_html is required")
    for attachment in attachments:
        if not Path(attachment).expanduser().is_file():
            raise CliError("INVALID_COMPOSE", f"Attachment not found: {attachment}")
    return {
        "to": to,
        "cc": cc,
        "bcc": bcc,
        "subject": subject,
        "body_text": body_text,
        "body_html": body_html,
        "attachments": attachments,
    }


def add_attachments(message: EmailMessage, paths: list[str]) -> None:
    for raw_path in paths:
        path = Path(raw_path).expanduser()
        ctype, encoding = mimetypes.guess_type(str(path))
        if ctype is None or encoding is not None:
            ctype = "application/octet-stream"
        maintype, subtype = ctype.split("/", 1)
        message.add_attachment(path.read_bytes(), maintype=maintype, subtype=subtype, filename=path.name)


def build_email_message(config: MailConfig, compose: dict[str, Any]) -> EmailMessage:
    message = EmailMessage()
    message["From"] = config.from_addr
    message["To"] = ", ".join(compose["to"])
    if compose["cc"]:
        message["Cc"] = ", ".join(compose["cc"])
    message["Subject"] = compose["subject"]
    message["Date"] = formatdate(localtime=True)
    message["Message-ID"] = make_msgid()
    if compose["body_html"]:
        message.set_content(compose["body_text"] or html_to_text(compose["body_html"]))
        message.add_alternative(compose["body_html"], subtype="html")
    else:
        message.set_content(compose["body_text"])
    add_attachments(message, compose["attachments"])
    return message


def smtp_send(config: MailConfig, message: EmailMessage, bcc: list[str] | None = None) -> None:
    validate_config(config, need_smtp=True)
    header_values = list(message.get_all("To", [])) + list(message.get_all("Cc", []))
    recipients = [address for _, address in getaddresses(header_values) if address] + (bcc or [])
    try:
        if config.smtp_ssl:
            server: smtplib.SMTP = smtplib.SMTP_SSL(
                config.smtp_host,
                config.smtp_port,
                timeout=config.timeout_seconds,
                context=ssl.create_default_context(),
            )
        else:
            server = smtplib.SMTP(config.smtp_host, config.smtp_port, timeout=config.timeout_seconds)
            if config.smtp_starttls:
                server.ehlo()
                server.starttls(context=ssl.create_default_context())
                server.ehlo()
        try:
            server.login(config.username, config.auth_code)
            server.send_message(message, from_addr=config.from_addr, to_addrs=recipients)
        finally:
            server.quit()
    except Exception as exc:
        raise CliError("SMTP_ERROR", f"SMTP operation failed: {exc.__class__.__name__}: {exc}") from exc


def command_draft(args: argparse.Namespace) -> int:
    require_confirm(args, "Creating a draft")
    config = load_config(args.env)
    if not config.from_addr:
        raise CliError("MISSING_CONFIG", "Missing required config: EMAIL_FROM or EMAIL_USERNAME")
    compose = normalize_compose(load_compose(args.input), require_to=True)
    message = build_email_message(config, compose)
    payload = {
        "to": compose["to"],
        "cc": compose["cc"],
        "bcc_count": len(compose["bcc"]),
        "subject": compose["subject"],
        "attachments": compose["attachments"],
    }
    if args.out and args.managed_out:
        raise CliError("INVALID_ARGUMENT", "Choose either --out or --managed-out")
    if args.out or args.managed_out:
        out_path = (
            Path(args.out).expanduser()
            if args.out
            else timestamped_artifact_path("drafts", "draft", ".eml", args.work_dir, args.env)
        )
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(message.as_bytes(policy=policy.SMTP))
        set_private_permissions(out_path)
        payload["path"] = str(out_path.resolve())
    return json_success(draft=payload)


def command_send(args: argparse.Namespace) -> int:
    require_confirm(args, "Sending email")
    config = load_config(args.env)
    compose = normalize_compose(load_compose(args.input), require_to=True)
    message = build_email_message(config, compose)
    smtp_send(config, message, compose["bcc"])
    return json_success(sent={"to": compose["to"], "cc": compose["cc"], "bcc_count": len(compose["bcc"]), "subject": compose["subject"]})


def address_values(items: list[dict[str, str]]) -> list[str]:
    return [item["address"] for item in items if item.get("address")]


def without_self(addresses: list[str], config: MailConfig) -> list[str]:
    self_addresses = {config.username.lower(), config.from_addr.lower()}
    result = []
    for address in addresses:
        if address.lower() not in self_addresses and address not in result:
            result.append(address)
    return result


def re_subject(subject: str, prefix: str) -> str:
    if subject.lower().startswith(prefix.lower()):
        return subject
    return f"{prefix} {subject}".strip()


def command_reply(args: argparse.Namespace) -> int:
    require_confirm(args, "Replying to email")
    config = load_config(args.env)
    folder = args.folder or config.default_folder
    compose_data = load_compose(args.input)
    compose = normalize_compose(compose_data, require_to=False, require_subject=False)
    client = connect_imap(config)
    try:
        select_folder(client, folder, readonly=True)
        original = fetch_message(client, args.uid, full=True)
    finally:
        imap_logout(client)

    original_json = message_metadata(original, args.uid)
    if not compose["to"]:
        recipients = address_values(original_json["from"])
        if args.reply_all:
            recipients.extend(address_values(original_json["to"]))
            recipients.extend(address_values(original_json["cc"]))
        compose["to"] = without_self(recipients, config)
    if not compose["to"]:
        raise CliError("INVALID_COMPOSE", "Could not infer reply recipient; provide to in compose JSON")
    if not compose["subject"]:
        compose["subject"] = re_subject(original_json["subject"], "Re:")

    message = build_email_message(config, compose)
    if original_json["message_id"]:
        message["In-Reply-To"] = original_json["message_id"]
        refs = original_json["references"]
        message["References"] = f"{refs} {original_json['message_id']}".strip()
    smtp_send(config, message, compose["bcc"])
    return json_success(replied={"folder": folder, "uid": args.uid, "to": compose["to"], "subject": compose["subject"]})


def command_forward(args: argparse.Namespace) -> int:
    require_confirm(args, "Forwarding email")
    config = load_config(args.env)
    folder = args.folder or config.default_folder
    compose = normalize_compose(load_compose(args.input), require_to=True, require_subject=False)
    client = connect_imap(config)
    try:
        select_folder(client, folder, readonly=True)
        original = fetch_message(client, args.uid, full=True)
    finally:
        imap_logout(client)

    original_json = message_to_json(original, args.uid, max_chars=None)
    forwarded = [
        compose["body_text"],
        "",
        "----- Forwarded message -----",
        f"From: {', '.join(address_values(original_json['from']))}",
        f"Date: {original_json['date']}",
        f"Subject: {original_json['subject']}",
        f"To: {', '.join(address_values(original_json['to']))}",
        "",
        original_json["body"]["text"],
    ]
    compose["body_text"] = "\n".join(forwarded).strip()
    if not compose["subject"]:
        compose["subject"] = re_subject(original_json["subject"], "Fwd:")
    message = build_email_message(config, compose)
    smtp_send(config, message, compose["bcc"])
    return json_success(forwarded={"folder": folder, "uid": args.uid, "to": compose["to"], "subject": compose["subject"]})


def add_common_env_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--env", help="Path to .env file. Defaults to EMAIL_ASSISTANT_ENV or the skill root .env.")


def add_common_work_dir_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--work-dir",
        help="Managed artifact directory. Defaults to EMAIL_ASSISTANT_WORK_DIR or the system temp email-assistant directory.",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Email Assistant IMAP/SMTP CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    configure = subparsers.add_parser("configure", help="Create or update the skill-root .env")
    add_common_env_arg(configure)
    configure.add_argument("--provider", choices=sorted(PROVIDER_PRESETS), default="generic")
    configure.add_argument("--imap-host")
    configure.add_argument("--smtp-host")
    configure.add_argument("--username")
    configure.add_argument("--from", dest="from_addr")
    configure.add_argument("--auth-code-stdin", action="store_true", help="Read EMAIL_AUTH_CODE from the first stdin line")
    configure.add_argument("--non-interactive", action="store_true", help="Do not prompt for missing values")
    configure.set_defaults(func=command_configure)

    check = subparsers.add_parser("check-config", help="Validate and summarize configuration without secrets")
    add_common_env_arg(check)
    check.set_defaults(func=command_check_config)

    artifacts = subparsers.add_parser("artifacts", help="List managed intermediate files without printing their contents")
    add_common_env_arg(artifacts)
    add_common_work_dir_arg(artifacts)
    artifacts.add_argument("--limit", type=int, default=50)
    artifacts.set_defaults(func=command_artifacts)

    cleanup = subparsers.add_parser("cleanup-artifacts", help="Delete managed intermediate files")
    add_common_env_arg(cleanup)
    add_common_work_dir_arg(cleanup)
    cleanup.add_argument("--max-age-hours", type=float, default=24)
    cleanup.add_argument("--all", action="store_true", help="Delete all managed artifacts")
    cleanup.add_argument("--confirm", action="store_true")
    cleanup.set_defaults(func=command_cleanup_artifacts)

    stage_compose = subparsers.add_parser("stage-compose", help="Validate and write compose JSON to the managed work dir")
    add_common_env_arg(stage_compose)
    add_common_work_dir_arg(stage_compose)
    stage_input = stage_compose.add_mutually_exclusive_group(required=True)
    stage_input.add_argument("--input", help="Compose JSON to normalize and stage")
    stage_input.add_argument("--stdin", action="store_true", help="Read compose JSON from stdin")
    stage_compose.add_argument("--out", help="Explicit output path. Defaults to managed compose artifacts.")
    stage_compose.set_defaults(func=command_stage_compose)

    folders = subparsers.add_parser("folders", help="List IMAP folders")
    add_common_env_arg(folders)
    folders.set_defaults(func=command_folders)

    search = subparsers.add_parser("search", help="Search messages and return header summaries")
    add_common_env_arg(search)
    search.add_argument("--folder")
    search.add_argument("--unseen", action="store_true")
    search.add_argument("--seen", action="store_true")
    search.add_argument("--from", dest="from_addr")
    search.add_argument("--to")
    search.add_argument("--subject")
    search.add_argument("--since")
    search.add_argument("--before")
    search.add_argument("--limit", type=int, default=20)
    search.add_argument("--oldest-first", action="store_true")
    search.set_defaults(func=command_search)

    fetch = subparsers.add_parser("fetch", help="Fetch a full message by UID")
    add_common_env_arg(fetch)
    fetch.add_argument("--folder")
    fetch.add_argument("--uid", required=True)
    fetch.add_argument("--max-chars", type=int, default=12000)
    fetch.set_defaults(func=command_fetch)

    thread = subparsers.add_parser("thread", help="Fetch likely messages in the same thread")
    add_common_env_arg(thread)
    thread.add_argument("--folder")
    thread.add_argument("--uid", required=True)
    thread.add_argument("--limit", type=int, default=50)
    thread.add_argument("--max-chars", type=int, default=12000)
    thread.set_defaults(func=command_thread)

    save = subparsers.add_parser("save-attachments", help="Save attachments from a message")
    add_common_env_arg(save)
    add_common_work_dir_arg(save)
    save.add_argument("--folder")
    save.add_argument("--uid", required=True)
    save.add_argument("--out")
    save.add_argument("--indices", type=int, nargs="*")
    save.add_argument("--confirm", action="store_true")
    save.set_defaults(func=command_save_attachments)

    mark = subparsers.add_parser("mark", help="Mark a message read/unread/flagged/unflagged")
    add_common_env_arg(mark)
    mark.add_argument("--folder")
    mark.add_argument("--uid", required=True)
    mark_action = mark.add_mutually_exclusive_group(required=True)
    mark_action.add_argument("--read", action="store_true")
    mark_action.add_argument("--unread", action="store_true")
    mark_action.add_argument("--flag", action="store_true")
    mark_action.add_argument("--unflag", action="store_true")
    mark.add_argument("--confirm", action="store_true")
    mark.set_defaults(func=command_mark)

    move = subparsers.add_parser("move", help="Move a message to another folder")
    add_common_env_arg(move)
    move.add_argument("--from-folder")
    move.add_argument("--uid", required=True)
    move.add_argument("--to-folder", required=True)
    move.add_argument("--confirm", action="store_true")
    move.set_defaults(func=command_move)

    delete = subparsers.add_parser("delete", help="Delete a message or move it to trash when configured")
    add_common_env_arg(delete)
    delete.add_argument("--folder")
    delete.add_argument("--uid", required=True)
    delete.add_argument("--confirm", action="store_true")
    delete.set_defaults(func=command_delete)

    draft = subparsers.add_parser("draft", help="Validate compose JSON and optionally write an .eml draft")
    add_common_env_arg(draft)
    add_common_work_dir_arg(draft)
    draft.add_argument("--input", required=True)
    draft.add_argument("--out")
    draft.add_argument("--managed-out", action="store_true", help="Write the .eml draft under the managed drafts artifact dir")
    draft.add_argument("--confirm", action="store_true")
    draft.set_defaults(func=command_draft)

    send = subparsers.add_parser("send", help="Send compose JSON through SMTP")
    add_common_env_arg(send)
    send.add_argument("--input", required=True)
    send.add_argument("--confirm", action="store_true")
    send.set_defaults(func=command_send)

    reply = subparsers.add_parser("reply", help="Reply to a message through SMTP")
    add_common_env_arg(reply)
    reply.add_argument("--folder")
    reply.add_argument("--uid", required=True)
    reply.add_argument("--input", required=True)
    reply.add_argument("--reply-all", action="store_true")
    reply.add_argument("--confirm", action="store_true")
    reply.set_defaults(func=command_reply)

    forward = subparsers.add_parser("forward", help="Forward a message through SMTP")
    add_common_env_arg(forward)
    forward.add_argument("--folder")
    forward.add_argument("--uid", required=True)
    forward.add_argument("--input", required=True)
    forward.add_argument("--confirm", action="store_true")
    forward.set_defaults(func=command_forward)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except CliError as exc:
        return json_error(exc)
    except KeyboardInterrupt:
        return json_error(CliError("INTERRUPTED", "Interrupted"))
    except Exception as exc:
        return json_error(CliError("UNEXPECTED_ERROR", f"{exc.__class__.__name__}: {exc}"))


if __name__ == "__main__":
    sys.exit(main())
