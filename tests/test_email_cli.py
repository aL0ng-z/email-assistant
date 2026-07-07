import argparse
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from email.message import EmailMessage
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "email_cli.py"

spec = importlib.util.spec_from_file_location("email_cli", SCRIPT)
email_cli = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules["email_cli"] = email_cli
spec.loader.exec_module(email_cli)


class EmailCliTests(unittest.TestCase):
    def test_parse_dotenv_and_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text(
                "\n".join(
                    [
                        "EMAIL_IMAP_HOST=imap.example.com",
                        "EMAIL_SMTP_HOST=smtp.example.com",
                        "EMAIL_USERNAME=user@example.com",
                        "EMAIL_AUTH_CODE=secret",
                        "EMAIL_FROM=user@example.com",
                    ]
                ),
                encoding="utf-8",
            )
            config = email_cli.load_config(str(env_path))
            self.assertEqual(config.imap_host, "imap.example.com")
            self.assertEqual(config.smtp_host, "smtp.example.com")
            self.assertEqual(config.username, "user@example.com")
            self.assertTrue(config.imap_send_id)
            self.assertEqual(config.imap_id_name, "email-assistant")
            safe = email_cli.safe_config(config)
            self.assertTrue(safe["auth_code_configured"])
            self.assertNotIn("secret", json.dumps(safe))

    def test_default_env_path_is_skill_root(self):
        env_path = email_cli.resolve_env_path(None)
        self.assertEqual(env_path, ROOT / ".env")

    def test_env_var_overrides_default_env_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = str(Path(tmp) / ".env")
            original = email_cli.os.environ.get("EMAIL_ASSISTANT_ENV")
            try:
                email_cli.os.environ["EMAIL_ASSISTANT_ENV"] = target
                self.assertEqual(email_cli.resolve_env_path(None), Path(target))
            finally:
                if original is None:
                    email_cli.os.environ.pop("EMAIL_ASSISTANT_ENV", None)
                else:
                    email_cli.os.environ["EMAIL_ASSISTANT_ENV"] = original

    def test_work_dir_uses_env_file_or_temp_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            missing_env = tmp_path / "missing.env"
            work_dir = tmp_path / "managed-work"
            env_path = tmp_path / ".env"
            env_path.write_text(f"EMAIL_ASSISTANT_WORK_DIR={work_dir}\n", encoding="utf-8")
            original = email_cli.os.environ.get("EMAIL_ASSISTANT_WORK_DIR")
            try:
                email_cli.os.environ.pop("EMAIL_ASSISTANT_WORK_DIR", None)
                self.assertEqual(
                    email_cli.resolve_work_dir(None, str(missing_env)),
                    Path(tempfile.gettempdir()) / "email-assistant",
                )
                self.assertEqual(email_cli.resolve_work_dir(None, str(env_path)), work_dir)
            finally:
                if original is not None:
                    email_cli.os.environ["EMAIL_ASSISTANT_WORK_DIR"] = original

    def test_configure_163_writes_env_without_leaking_secret(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "configure",
                    "--provider",
                    "163",
                    "--env",
                    str(env_path),
                    "--username",
                    "user@163.com",
                    "--auth-code-stdin",
                    "--non-interactive",
                ],
                input="secret-code\n",
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertNotIn("secret-code", result.stdout)
            content = env_path.read_text(encoding="utf-8")
            self.assertIn("EMAIL_IMAP_HOST=imap.163.com", content)
            self.assertIn("EMAIL_SMTP_HOST=smtp.163.com", content)
            self.assertIn("EMAIL_USERNAME=user@163.com", content)
            self.assertIn("EMAIL_FROM=user@163.com", content)
            self.assertIn("EMAIL_AUTH_CODE=secret-code", content)

    def test_imap_id_payload(self):
        config = email_cli.load_config(None)
        payload = email_cli.build_imap_id_payload(config)
        self.assertIn('"name" "email-assistant"', payload)
        self.assertIn('"version" "1.0"', payload)
        self.assertIn('"vendor" "email-assistant"', payload)

    def test_quote_mailbox_encodes_ampersand_for_imap(self):
        self.assertEqual(email_cli.quote_mailbox("A&B"), '"A&-B"')

    def test_quote_mailbox_encodes_non_ascii_as_modified_utf7(self):
        self.assertEqual(email_cli.quote_mailbox("\u6536\u4ef6\u7bb1"), '"&ZTZO9nux-"')

    def test_list_folders_decodes_modified_utf7_names(self):
        class DummyClient:
            def list(self):
                return "OK", [b'(\\HasNoChildren) "/" "~peter/mail/&U,BTFw-/&ZeVnLIqe-"']

            def logout(self):
                pass

        config = email_cli.MailConfig(
            env_path=None,
            env_exists=False,
            imap_host="",
            imap_port=993,
            imap_ssl=True,
            imap_send_id=True,
            imap_id_name="",
            imap_id_version="",
            imap_id_vendor="",
            smtp_host="",
            smtp_port=465,
            smtp_ssl=True,
            smtp_starttls=False,
            username="",
            auth_code="",
            from_addr="",
            default_folder="INBOX",
            sent_folder="",
            drafts_folder="",
            trash_folder="",
            timeout_seconds=30,
        )
        original_connect_imap = email_cli.connect_imap
        try:
            email_cli.connect_imap = lambda _config: DummyClient()
            folders = email_cli.list_folders(config)["folders"]
        finally:
            email_cli.connect_imap = original_connect_imap

        self.assertEqual(folders[0]["name"], "~peter/mail/\u53f0\u5317/\u65e5\u672c\u8a9e")

    def test_build_search_criteria(self):
        args = argparse.Namespace(
            unseen=True,
            seen=False,
            from_addr="sender@example.com",
            to=None,
            subject="Quarterly plan",
            since="2026-07-01",
            before=None,
        )
        criteria = email_cli.build_search_criteria(args)
        self.assertIn("UNSEEN", criteria)
        self.assertIn('FROM "sender@example.com"', criteria)
        self.assertIn('SUBJECT "Quarterly plan"', criteria)
        self.assertIn("SINCE 01-Jul-2026", criteria)

    def test_mime_parsing(self):
        message = EmailMessage()
        message["From"] = "Alice <alice@example.com>"
        message["To"] = "Bob <bob@example.com>"
        message["Subject"] = "Status"
        message.set_content("Plain body")
        message.add_attachment(b"hello", maintype="text", subtype="plain", filename="note.txt")

        parsed = email_cli.message_to_json(message, uid="42", max_chars=100)
        self.assertEqual(parsed["uid"], "42")
        self.assertEqual(parsed["subject"], "Status")
        self.assertEqual(parsed["body"]["text"], "Plain body")
        self.assertEqual(parsed["attachments"][0]["filename"], "note.txt")

    def test_compose_validation(self):
        data = {
            "to": ["bob@example.com"],
            "cc": [],
            "bcc": [],
            "subject": "Hello",
            "body_text": "Body",
            "attachments": [],
        }
        compose = email_cli.normalize_compose(data)
        self.assertEqual(compose["to"], ["bob@example.com"])

        bad = dict(data)
        bad["to"] = ["not-an-address"]
        with self.assertRaises(email_cli.CliError):
            email_cli.normalize_compose(bad)

    def test_mutating_command_requires_confirm_before_config(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "mark", "--folder", "INBOX", "--uid", "1", "--read"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 1)
        payload = json.loads(result.stdout)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "CONFIRMATION_REQUIRED")

    def test_stage_compose_writes_managed_file_without_printing_body(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            work_dir = tmp_path / "managed-work"
            env_path = tmp_path / ".env"
            env_path.write_text(f"EMAIL_ASSISTANT_WORK_DIR={work_dir}\n", encoding="utf-8")
            compose = {
                "to": ["bob@example.com"],
                "subject": "Hello",
                "body_text": "Sensitive body text",
                "attachments": [],
            }
            result = subprocess.run(
                [sys.executable, str(SCRIPT), "stage-compose", "--env", str(env_path), "--stdin"],
                input=json.dumps(compose),
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertNotIn("Sensitive body text", result.stdout)
            payload = json.loads(result.stdout)
            path = Path(payload["compose"]["path"])
            self.assertTrue(path.exists())
            self.assertEqual(path.parent, work_dir / "compose")
            self.assertIn("Sensitive body text", path.read_text(encoding="utf-8"))

    def test_cleanup_artifacts_requires_confirm_and_removes_managed_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = Path(tmp) / "managed-work"
            compose_dir = work_dir / "compose"
            compose_dir.mkdir(parents=True)
            artifact = compose_dir / "compose.json"
            artifact.write_text("{}", encoding="utf-8")

            missing_confirm = subprocess.run(
                [sys.executable, str(SCRIPT), "cleanup-artifacts", "--work-dir", str(work_dir), "--all"],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(missing_confirm.returncode, 1)
            payload = json.loads(missing_confirm.stdout)
            self.assertEqual(payload["error"]["code"], "CONFIRMATION_REQUIRED")
            self.assertTrue(artifact.exists())

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "cleanup-artifacts",
                    "--work-dir",
                    str(work_dir),
                    "--all",
                    "--confirm",
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["count"], 1)
            self.assertFalse(artifact.exists())


if __name__ == "__main__":
    unittest.main()
