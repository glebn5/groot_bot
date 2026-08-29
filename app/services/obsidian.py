import os
import re
import tempfile
import logging
from datetime import date
from typing import Optional
from webdav3.client import Client
from app.config import settings
from app.utils.timezone import get_today

logger = logging.getLogger(__name__)


class ObsidianService:
    def __init__(self):
        options = {
            'webdav_hostname': settings.WEBDAV_HOSTNAME,
            'webdav_login': settings.WEBDAV_LOGIN,
            'webdav_password': settings.WEBDAV_PASSWORD
        }
        self.client = Client(options)

    def is_configured(self) -> bool:
        login = (settings.WEBDAV_LOGIN or "").strip()
        pwd = (settings.WEBDAV_PASSWORD or "").strip()
        return bool(login and pwd and login != "your_email@mail.ru" and pwd != "your_app_password" and not login.startswith("your_"))

    def _get_remote_daily_path(self, target_date: date) -> str:
        date_str = target_date.strftime("%Y-%m-%d")
        vault = settings.WEBDAV_VAULT_PATH.rstrip('/')
        return f"{vault}/Daily/{date_str}.md"

    def _load_template_content(self) -> str:
        template_path = settings.TEMPLATE_PATH
        if os.path.exists(template_path):
            with open(template_path, "r", encoding="utf-8") as f:
                return f.read()
        logger.warning(f"Template path '{template_path}' not found locally. Using default fallback template.")
        return "---\n---\n\n## Задачи на сегодня\n\n"

    def append_task_to_markdown(self, md_content: str, task_text: str, target_section: str = "## Задачи на сегодня") -> str:
        """
        Parses markdown content, finds target_section without damaging YAML frontmatter,
        and appends '- [ ] {task_text}' into that section.
        """
        lines = md_content.splitlines()
        
        # Standardize target section format
        clean_target = target_section.strip()
        if not clean_target.startswith("#"):
            clean_target = f"## {clean_target}"

        # Find target header index
        header_index = -1
        for i, line in enumerate(lines):
            if line.strip().lower() == clean_target.lower():
                header_index = i
                break

        formatted_task = f"- [ ] {task_text.strip()}"

        if header_index != -1:
            # Find the end of the target section (next header or end of file)
            insert_index = len(lines)
            for j in range(header_index + 1, len(lines)):
                line_str = lines[j].strip()
                # If next header starting with # is reached
                if line_str.startswith("#"):
                    insert_index = j
                    break

            # Backtrack past trailing empty lines before next header
            while insert_index > header_index + 1 and not lines[insert_index - 1].strip():
                insert_index -= 1

            lines.insert(insert_index, formatted_task)
        else:
            # Section not found, append section and task at bottom
            if lines and lines[-1].strip() != "":
                lines.append("")
            lines.append(clean_target)
            lines.append(formatted_task)

        return "\n".join(lines) + "\n"

    async def add_task_to_daily_note(self, task_text: str, target_date: Optional[date] = None, target_section: str = "## Задачи на сегодня") -> Optional[str]:
        """
        Adds a task to the user's Obsidian daily note on WebDAV.
        If the file doesn't exist, initializes it from To-Do template.md first.
        """
        if not self.is_configured():
            logger.info("Obsidian WebDAV credentials not configured. Skipping Obsidian sync.")
            return None

        if not target_date:
            target_date = get_today()

        remote_path = self._get_remote_daily_path(target_date)
        logger.info(f"Targeting Obsidian WebDAV path: {remote_path}")

        try:
            with tempfile.NamedTemporaryFile(mode="w+", delete=False, encoding="utf-8", suffix=".md") as tmp_file:
                tmp_path = tmp_file.name

            # Check if remote note exists
            if self.client.check(remote_path):
                logger.info(f"Remote file exists: {remote_path}. Downloading...")
                self.client.download_sync(remote_path=remote_path, local_path=tmp_path)
                with open(tmp_path, "r", encoding="utf-8") as f:
                    content = f.read()
            else:
                logger.info(f"Remote file does not exist: {remote_path}. Using template...")
                content = self._load_template_content()

            # Append task
            updated_content = self.append_task_to_markdown(content, task_text, target_section)

            # Write updated content to temp file
            with open(tmp_path, "w", encoding="utf-8") as f:
                f.write(updated_content)

            # Ensure remote directory exists
            remote_dir = os.path.dirname(remote_path)
            if not self.client.check(remote_dir):
                self.client.mkdir(remote_dir)

            # Upload updated file to WebDAV
            self.client.upload_sync(remote_path=remote_path, local_path=tmp_path)
            logger.info(f"Successfully uploaded updated daily note to WebDAV: {remote_path}")

            if os.path.exists(tmp_path):
                os.remove(tmp_path)

            return remote_path

        except Exception as e:
            logger.error(f"Error updating Obsidian WebDAV note: {e}", exc_info=True)
            raise RuntimeError(f"Failed to sync with Obsidian WebDAV: {str(e)}")

    async def get_daily_tasks(self, target_date: date) -> list:
        """
        Retrieves task lines (e.g. '- [ ] ...' or '- [x] ...') from daily note in WebDAV.
        """
        if not self.is_configured():
            return []

        remote_path = self._get_remote_daily_path(target_date)
        try:
            if not self.client.check(remote_path):
                return []

            with tempfile.NamedTemporaryFile(mode="w+", delete=False, encoding="utf-8", suffix=".md") as tmp_file:
                tmp_path = tmp_file.name

            self.client.download_sync(remote_path=remote_path, local_path=tmp_path)
            with open(tmp_path, "r", encoding="utf-8") as f:
                content = f.read()

            if os.path.exists(tmp_path):
                os.remove(tmp_path)

            tasks = []
            for line in content.splitlines():
                stripped = line.strip()
                if stripped.startswith("- [") or stripped.startswith("* ["):
                    tasks.append(stripped)
            return tasks
        except Exception as e:
            logger.error(f"Error fetching Obsidian daily note tasks: {e}")
            return []


obsidian_service = ObsidianService()
