import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class RepoContractTests(unittest.TestCase):
    def test_required_files_exist(self):
        for relative in [
            "SKILL.md",
            "agents/openai.yaml",
            "scripts/email_cli.py",
            "references/config.md",
            "references/workflows.md",
            ".env.example",
            ".gitignore",
            "README.md",
            "LICENSE",
        ]:
            self.assertTrue((ROOT / relative).exists(), relative)

    def test_skill_frontmatter(self):
        content = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
        self.assertIsNotNone(match)
        frontmatter = match.group(1)
        self.assertIn("name: email-assistant", frontmatter)
        self.assertIn("description:", frontmatter)

    def test_env_example_fields(self):
        content = (ROOT / ".env.example").read_text(encoding="utf-8")
        for key in [
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
        ]:
            self.assertRegex(content, rf"(?m)^{key}=", key)

    def test_gitignore_excludes_env_but_keeps_example(self):
        content = (ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertRegex(content, r"(?m)^\.env$")
        self.assertRegex(content, r"(?m)^!\.env\.example$")

    def test_openai_yaml_minimal_contract(self):
        content = (ROOT / "agents" / "openai.yaml").read_text(encoding="utf-8")
        self.assertIn('display_name: "Email Assistant"', content)
        self.assertIn('default_prompt: "Use $email-assistant', content)
        self.assertIn("allow_implicit_invocation: true", content)


if __name__ == "__main__":
    unittest.main()
