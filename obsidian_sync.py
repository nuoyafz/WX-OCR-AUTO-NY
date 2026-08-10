"""
Obsidian 同步模块 — 方案A(文件直写) + 方案B(REST API) 双模式
================================================================
方案A：直接写Markdown文件到Vault目录（零配置，开箱即用）
方案B：通过Local REST API插件HTTP写入（支持双向交互）

文件结构：
  vault/
  ├── 微信消息/
  │   ├── 每日/
  │   │   └── 2026-08-10.md         ← 按日期汇总
  │   ├── 联系人/
  │   │   ├── 刘贵峰.md              ← 按联系人汇总
  │   │   └── 亚磊.md
  │   └── 重要消息.md                ← 重要消息汇总
"""
import os
import re
import json
import logging
import requests
from datetime import datetime

logger = logging.getLogger(__name__)


class ObsidianSync:
    """Obsidian双向同步器"""

    def __init__(self, config):
        self.config = config or {}
        self.vault_path = self.config.get("vault_path", "")
        self.mode = self.config.get("mode", "file")
        self.api_url = self.config.get("api_url", "http://127.0.0.1:27124")
        self.api_key = self.config.get("api_key", "")
        self.auto_sync = self.config.get("auto_sync", True)
        self.folder = self.config.get("folder", "微信消息")

        self._file_enabled = bool(self.vault_path) and self.mode in ("file", "both")
        self._api_enabled = bool(self.api_key) and self.mode in ("api", "both")

        if self._file_enabled:
            logger.info(f"[Obsidian] 文件模式已启用, vault={self.vault_path}")
        if self._api_enabled:
            logger.info(f"[Obsidian] API模式已启用, url={self.api_url}")

    @property
    def enabled(self):
        return self.auto_sync and (self._file_enabled or self._api_enabled)

    def _sanitize_filename(self, name):
        if not name:
            return "未命名"
        safe = re.sub(r'[<>:"/\\|?*]', '_', name)
        safe = safe.strip().rstrip('.')
        return safe[:50] if len(safe) > 50 else safe

    def _format_message_block(self, msg_data):
        """将单条消息格式化为Markdown块"""
        contact = msg_data.get("contact", "")
        sender = msg_data.get("sender", "other")
        content = msg_data.get("content", "") or msg_data.get("raw_text", "")
        timestamp = msg_data.get("timestamp", "")
        is_important = msg_data.get("is_important", False)
        importance_reason = msg_data.get("importance_reason", "")
        keywords = msg_data.get("keywords", []) or msg_data.get("matched_keywords", [])
        summary = msg_data.get("summary", "")

        if sender == "me":
            sender_display = "我"
        elif sender == "other":
            sender_display = contact or "对方"
        else:
            sender_display = sender

        if isinstance(timestamp, str) and timestamp:
            ts_display = timestamp
        elif hasattr(timestamp, "strftime"):
            ts_display = timestamp.strftime("%Y-%m-%d %H:%M")
        else:
            ts_display = datetime.now().strftime("%Y-%m-%d %H:%M")

        lines = []
        lines.append(f"## {ts_display}")
        lines.append("")
        lines.append(f"> {content}")
        lines.append("")

        meta = []
        meta.append(f"**发送者**: {sender_display}")
        if keywords:
            meta.append(f"**关键词**: {', '.join(keywords) if isinstance(keywords, list) else str(keywords)}")
        if summary:
            meta.append(f"**摘要**: {summary}")
        if is_important:
            meta.append("**重要**: ⭐ 是")
            if importance_reason:
                meta.append(f"**原因**: {importance_reason}")
        else:
            meta.append("**重要**: 否")

        lines.append(" - ".join(meta))
        lines.append("")
        lines.append("---")
        lines.append("")
        return "\n".join(lines)

    def _format_daily_note(self, messages, date_str=None):
        """格式化每日笔记"""
        if date_str is None:
            date_str = datetime.now().strftime("%Y-%m-%d")

        contacts = {}
        for msg in messages:
            name = msg.get("contact", "未命名")
            if name not in contacts:
                contacts[name] = []
            contacts[name].append(msg)

        important_count = sum(1 for m in messages if m.get("is_important"))

        lines = [
            "---",
            f"date: {date_str}",
            "source: 微信AI助手",
            f"total_messages: {len(messages)}",
            f"total_contacts: {len(contacts)}",
            f"important_count: {important_count}",
            "tags: [微信, 每日笔记]",
            "---",
            "",
            f"# {date_str} 微信消息",
            "",
            f"> 共 {len(messages)} 条消息, {len(contacts)} 个联系人, {important_count} 条重要消息",
            "",
        ]

        important_msgs = [m for m in messages if m.get("is_important")]
        if important_msgs:
            lines.append("## ⭐ 重要消息")
            lines.append("")
            for msg in important_msgs:
                lines.append(self._format_message_block(msg))
            lines.append("")

        for contact_name in sorted(contacts.keys()):
            msgs = contacts[contact_name]
            lines.append(f"### {contact_name} ({len(msgs)}条)")
            lines.append("")
            for msg in msgs:
                lines.append(self._format_message_block(msg))
            lines.append("")

        return "\n".join(lines)

    def _format_contact_note(self, contact_name, messages):
        """格式化联系人笔记"""
        important_count = sum(1 for m in messages if m.get("is_important"))

        lines = [
            "---",
            f"contact: {contact_name}",
            f"total_messages: {len(messages)}",
            f"important_count: {important_count}",
            "tags: [微信, 联系人]",
            "---",
            "",
            f"# {contact_name}",
            "",
            f"> 共 {len(messages)} 条消息, {important_count} 条重要",
            "",
        ]

        sorted_msgs = sorted(messages, key=lambda m: str(m.get("timestamp", "")))
        for msg in sorted_msgs:
            lines.append(self._format_message_block(msg))

        return "\n".join(lines)

    def _format_important_note(self, all_messages):
        """格式化重要消息汇总笔记"""
        important = [m for m in all_messages if m.get("is_important")]

        lines = [
            "---",
            "type: 重要消息汇总",
            f"total: {len(important)}",
            f"updated: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            "tags: [微信, 重要]",
            "---",
            "",
            "# ⭐ 重要消息汇总",
            "",
            f"> 共 {len(important)} 条重要消息",
            "",
        ]

        contacts = {}
        for msg in important:
            name = msg.get("contact", "未命名")
            if name not in contacts:
                contacts[name] = []
            contacts[name].append(msg)

        for contact_name in sorted(contacts.keys()):
            msgs = contacts[contact_name]
            lines.append(f"## {contact_name} ({len(msgs)}条)")
            lines.append("")
            for msg in msgs:
                lines.append(self._format_message_block(msg))

        return "\n".join(lines)

    # ================================================================
    # 方案A：文件直写
    # ================================================================

    def _sync_file(self, msg_data):
        """方案A：直接写Markdown文件到vault目录"""
        try:
            base_dir = os.path.join(self.vault_path, self.folder)
            daily_dir = os.path.join(base_dir, "每日")
            contact_dir = os.path.join(base_dir, "联系人")
            os.makedirs(daily_dir, exist_ok=True)
            os.makedirs(contact_dir, exist_ok=True)

            contact = self._sanitize_filename(msg_data.get("contact", "未命名"))
            date_str = datetime.now().strftime("%Y-%m-%d")

            # 1. 追加到每日笔记
            daily_path = os.path.join(daily_dir, f"{date_str}.md")
            block = self._format_message_block(msg_data)
            self._append_to_file(daily_path, block, title=f"{date_str} 微信消息")

            # 2. 追加到联系人笔记
            contact_path = os.path.join(contact_dir, f"{contact}.md")
            self._append_to_file(contact_path, block, title=contact)

            # 3. 重要消息追加到汇总
            if msg_data.get("is_important"):
                important_path = os.path.join(base_dir, "重要消息.md")
                self._append_to_file(important_path, block, title="⭐ 重要消息汇总")

            return True

        except Exception as e:
            logger.error(f"[Obsidian] 文件同步失败: {e}")
            return False

    def _append_to_file(self, filepath, content, title=""):
        """追加内容到文件，不存在则创建"""
        is_new = not os.path.exists(filepath)

        with open(filepath, "a", encoding="utf-8") as f:
            if is_new:
                ts = datetime.now().strftime("%Y-%m-%d %H:%M")
                f.write(f"---\ncreated: {ts}\nsource: 微信AI助手\ntags: [微信]\n---\n\n")
                f.write(f"# {title}\n\n")
            f.write(content)
            f.write("\n")

    def rebuild_vault(self, all_messages):
        """全量重建vault笔记"""
        if not self._file_enabled:
            logger.warning("[Obsidian] 文件模式未启用，无法重建")
            return False

        try:
            base_dir = os.path.join(self.vault_path, self.folder)
            daily_dir = os.path.join(base_dir, "每日")
            contact_dir = os.path.join(base_dir, "联系人")
            os.makedirs(daily_dir, exist_ok=True)
            os.makedirs(contact_dir, exist_ok=True)

            by_date = {}
            by_contact = {}
            for msg in all_messages:
                ts = msg.get("timestamp", "")
                if hasattr(ts, "strftime"):
                    date_str = ts.strftime("%Y-%m-%d")
                elif isinstance(ts, str) and len(ts) >= 10:
                    date_str = ts[:10]
                else:
                    date_str = datetime.now().strftime("%Y-%m-%d")

                if date_str not in by_date:
                    by_date[date_str] = []
                by_date[date_str].append(msg)

                contact = msg.get("contact", "未命名")
                if contact not in by_contact:
                    by_contact[contact] = []
                by_contact[contact].append(msg)

            for date_str, msgs in by_date.items():
                daily_path = os.path.join(daily_dir, f"{date_str}.md")
                content = self._format_daily_note(msgs, date_str)
                with open(daily_path, "w", encoding="utf-8") as f:
                    f.write(content)

            for contact, msgs in by_contact.items():
                safe_name = self._sanitize_filename(contact)
                contact_path = os.path.join(contact_dir, f"{safe_name}.md")
                content = self._format_contact_note(contact, msgs)
                with open(contact_path, "w", encoding="utf-8") as f:
                    f.write(content)

            important_path = os.path.join(base_dir, "重要消息.md")
            content = self._format_important_note(all_messages)
            with open(important_path, "w", encoding="utf-8") as f:
                f.write(content)

            logger.info(f"[Obsidian] vault重建完成: {len(by_date)}天, {len(by_contact)}联系人, "
                        f"{len(all_messages)}条消息")
            return True

        except Exception as e:
            logger.error(f"[Obsidian] vault重建失败: {e}")
            return False

    # ================================================================
    # 方案B：Local REST API
    # ================================================================

    def _sync_api(self, msg_data):
        """方案B：通过Local REST API写入"""
        try:
            contact = self._sanitize_filename(msg_data.get("contact", "未命名"))
            date_str = datetime.now().strftime("%Y-%m-%d")
            block = self._format_message_block(msg_data)

            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "text/markdown",
            }

            daily_filename = f"{self.folder}/每日/{date_str}.md"
            self._api_append(daily_filename, block, headers)

            contact_filename = f"{self.folder}/联系人/{contact}.md"
            self._api_append(contact_filename, block, headers)

            if msg_data.get("is_important"):
                important_filename = f"{self.folder}/重要消息.md"
                self._api_append(important_filename, block, headers)

            return True

        except Exception as e:
            logger.error(f"[Obsidian] API同步失败: {e}")
            return False

    def _api_append(self, filename, content, headers):
        """通过REST API追加内容到笔记"""
        url = f"{self.api_url}/vault/{filename}"

        try:
            resp = requests.get(url, headers=headers, timeout=5)
            if resp.status_code == 200:
                existing = resp.text
                if not existing.startswith("---"):
                    existing = f"---\nsource: 微信AI助手\ntags: [微信]\n---\n\n# 笔记\n\n" + existing
                new_content = existing + "\n" + content
                requests.put(url, headers={**headers, "Content-Type": "text/markdown"},
                           data=new_content.encode("utf-8"), timeout=10)
            else:
                title = os.path.splitext(os.path.basename(filename))[0]
                new_content = f"---\nsource: 微信AI助手\ntags: [微信]\n---\n\n# {title}\n\n{content}"
                requests.put(url, headers={**headers, "Content-Type": "text/markdown"},
                           data=new_content.encode("utf-8"), timeout=10)
        except requests.exceptions.ConnectionError:
            logger.warning("[Obsidian] REST API连接失败，请确保Obsidian已运行且Local REST API插件已启用")
        except Exception as e:
            logger.warning(f"[Obsidian] API追加失败({filename}): {e}")

    def test_api_connection(self):
        """测试REST API连接"""
        if not self._api_enabled:
            return False, "API模式未启用"
        try:
            headers = {"Authorization": f"Bearer {self.api_key}"}
            resp = requests.get(f"{self.api_url}/", headers=headers, timeout=5)
            if resp.status_code == 200:
                return True, "REST API连接正常"
            elif resp.status_code == 401:
                return False, "API密钥错误"
            else:
                return False, f"API返回状态码: {resp.status_code}"
        except requests.exceptions.ConnectionError:
            return False, "无法连接，请确保Obsidian已运行且Local REST API插件已启用"
        except Exception as e:
            return False, f"连接失败: {e}"

    # ================================================================
    # 统一入口
    # ================================================================

    def sync_message(self, msg_data):
        """同步单条消息到Obsidian"""
        if not self.enabled:
            return False

        success = False
        if self._file_enabled:
            if self._sync_file(msg_data):
                success = True
        if self._api_enabled:
            if self._sync_api(msg_data):
                success = True

        return success

    def sync_batch(self, messages):
        """批量同步消息"""
        if not self.enabled:
            return 0

        count = 0
        for msg in messages:
            if self.sync_message(msg):
                count += 1

        logger.info(f"[Obsidian] 批量同步完成: {count}/{len(messages)} 条")
        return count
