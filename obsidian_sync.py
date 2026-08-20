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

    # =================================================================
    # V3: 微信 PC 风格 Markdown 格式（最新消息在顶部 + Callout 气泡）
    # =================================================================

    def _format_message_block(self, msg_data):
        """V3: 微信气泡 Callout 样式
        - 自己：callout-success（微信绿 #07C160 风格）
        - 对方：callout 灰色边框 + 白背景
        - 群聊：callout 标题追加"群成员 · xxx"
        - 重要：callout 追加 ⭐ 徽章
        - 关键词/提取字段/摘要 用 ` ` 反引号包裹
        """
        contact = msg_data.get("contact", "")
        sender = msg_data.get("sender", "other")
        content = msg_data.get("content", "") or msg_data.get("raw_text", "")
        timestamp = msg_data.get("timestamp", "")
        is_important = msg_data.get("is_important", False)
        importance_reason = msg_data.get("importance_reason", "")
        keywords = msg_data.get("keywords", []) or msg_data.get("matched_keywords", [])
        keyword_categories = msg_data.get("keyword_categories") or []
        if keyword_categories and isinstance(keyword_categories, list):
            # 兼容关键词分类（若 keywords 空就合并过来）
            if not keywords:
                keywords = list(keyword_categories)
        extracted = msg_data.get("extracted_fields") or msg_data.get("extracted") or {}
        summary = msg_data.get("summary", "")
        llm = msg_data.get("llm_analysis") or {}
        if not summary and isinstance(llm, dict) and llm.get("summary"):
            summary = llm["summary"]
        is_group = bool(msg_data.get("is_group", False))
        group_member = msg_data.get("group_member") or None

        is_self = (sender == "me")
        if is_self:
            sender_display = "我"
        elif is_group and group_member:
            sender_display = f"{group_member}"
        else:
            sender_display = contact or "对方"

        # 时间戳
        if isinstance(timestamp, str) and timestamp:
            ts_display = timestamp
        elif hasattr(timestamp, "strftime"):
            ts_display = timestamp.strftime("%Y-%m-%d %H:%M")
        else:
            ts_display = datetime.now().strftime("%Y-%m-%d %H:%M")

        # Callout 类型（Obsidian Callout 官方：success=绿, note=灰蓝, warning=橙, danger=红）
        if is_important:
            callout_type = "quote-danger" if not is_self else "quote-success"
        elif is_self:
            callout_type = "quote-success"
        else:
            callout_type = "quote"

        # Callout 标题：重要用 ⭐；群聊单独标 👥；私聊 👤；自己 自己
        kind_mark = "👥" if is_group else ("🟢" if is_self else "👤")
        title_bits = [kind_mark]
        if is_group:
            title_bits.append(f"群聊：{contact}｜")
            if group_member and not is_self:
                title_bits.append(f"成员：{group_member}｜")
        else:
            if is_self:
                title_bits.append("自己 （我发出）")
            else:
                title_bits.append(f"私聊：{contact}")
        title_bits.append(f"🕒 {ts_display}")
        if is_important:
            title_bits.append("⭐ 重要")
        callout_title = " ".join(title_bits)

        # 正文：防止 Callout 折叠，我们把内容放在第一行">"之后，并换行
        safe_content = str(content).strip() or "(空)"
        content_lines = safe_content.splitlines() or [safe_content]
        # 每行前加 "> "（Callout 多行需要加）
        content_callout = "\n".join([f"> {l}" for l in content_lines])

        # 元数据行
        meta_chips = []
        if keywords and isinstance(keywords, list):
            kws = [f"`{str(k)}`" for k in keywords[:6]]
            if kws:
                meta_chips.append("🏷 关键词：" + " ".join(kws))
        if extracted and isinstance(extracted, dict):
            fields = [f"`{k}={str(v)[:30]}`" for k, v in list(extracted.items())[:4]]
            if fields:
                meta_chips.append("💎 提取：" + " ".join(fields))
        if summary:
            meta_chips.append(f"📝 摘要：{str(summary)[:200]}")
        if is_important and importance_reason:
            meta_chips.append(f"📌 重要原因：{str(importance_reason)[:120]}")

        lines = []
        lines.append(f"> [!{callout_type}] {callout_title}")
        lines.append(f">")
        lines.append(content_callout)
        if meta_chips:
            lines.append(">")
            for chip in meta_chips:
                lines.append(f"> - {chip}")
        lines.append("")
        return "\n".join(lines)

    def _prepend_to_file(self, filepath, block, title=""):
        """V3: 把"新消息块"插入到 Frontmatter 之后（最新消息在最顶部）"""
        is_new = not os.path.exists(filepath)
        if is_new:
            ts = datetime.now().strftime("%Y-%m-%d %H:%M")
            header = (
                f"---\ncreated: {ts}\nsource: 微信AI助手\ntags: [微信]\n"
                f"updated: {ts}\n---\n\n"
                f"# {title}\n\n"
            )
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(header + block + "\n")
            return

        with open(filepath, "r", encoding="utf-8") as f:
            existing = f.read() or ""

        # 寻找 Frontmatter 块（以 --- 起止的第一块），并在其后插入
        body = existing
        prefix = ""
        if body.startswith("---\n"):
            # 找到结束的 ---
            end = body.find("\n---", 4)
            if end > 0:
                next_newline = body.find("\n", end + 4)
                cut = next_newline if next_newline != -1 else len(body)
                prefix = body[:cut].rstrip() + "\n\n"
                body = body[cut:].lstrip("\n")

        # 再跳过"介绍区" — 连续空行、H1~H2 标题、以及普通的">"引用（不是 callout 的 [!xxx]）
        #  注意：绝对不能跳过已有的 callout 正文（callout 首行必须包含 [! 开头）
        rest_lines = body.splitlines()
        skip_end = 0
        for line in rest_lines:
            s = line.strip()
            if not s:
                skip_end += 1
                continue
            # H1/H2/H3/HR (---)
            if s.startswith(("# ", "## ", "### ", "---")):
                skip_end += 1
                continue
            # 普通引用介绍（不含 callout 语法 "[!...]"）
            if s.startswith("> ") and ("[!" not in s[:30]):
                skip_end += 1
                continue
            break

        kept_prefix = "\n".join(rest_lines[:skip_end])
        kept_body = "\n".join(rest_lines[skip_end:]).lstrip("\n")

        ts = datetime.now().strftime("%Y-%m-%d %H:%M")
        # 在 frontmatter 末尾补一下 updated
        if prefix.startswith("---\n"):
            # 在最后一行 --- 之前插入 updated 行（若已有则覆盖）
            fm_lines = prefix.splitlines()
            new_fm = []
            seen_updated = False
            for ln in fm_lines:
                if ln.startswith("updated:"):
                    new_fm.append(f"updated: {ts}")
                    seen_updated = True
                else:
                    new_fm.append(ln)
            if not seen_updated:
                # 找到倒数第二行（最后一行是 ---）之前插入
                if new_fm and new_fm[-1] == "---":
                    new_fm.insert(-1, f"updated: {ts}")
                else:
                    new_fm.append(f"updated: {ts}")
            prefix = "\n".join(new_fm).rstrip() + "\n\n"

        with open(filepath, "w", encoding="utf-8") as f:
            f.write(prefix)
            if kept_prefix.strip():
                f.write(kept_prefix.rstrip() + "\n\n")
            f.write(block + "\n\n")
            if kept_body.strip():
                f.write(kept_body + "\n")

    def _format_daily_note(self, messages, date_str=None):
        """V3: 每日笔记按时间 倒序（最新在顶部），并先输出"今日摘要"再输出消息"""
        if date_str is None:
            date_str = datetime.now().strftime("%Y-%m-%d")

        contacts = {}
        for msg in messages:
            name = msg.get("contact", "未命名")
            if name not in contacts:
                contacts[name] = []
            contacts[name].append(msg)

        important_count = sum(1 for m in messages if m.get("is_important"))
        self_count = sum(1 for m in messages if m.get("sender") == "me")
        group_count = sum(1 for m in messages if m.get("is_group"))

        # 按时间 倒序
        def _ts(m):
            t = m.get("timestamp", "")
            return str(t)
        sorted_messages = sorted(messages, key=_ts, reverse=True)

        lines = [
            "---",
            f"date: {date_str}",
            "source: 微信AI助手",
            f"total_messages: {len(messages)}",
            f"total_contacts: {len(contacts)}",
            f"important_count: {important_count}",
            f"self_sent: {self_count}",
            f"group_messages: {group_count}",
            f"updated: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            "tags: [微信, 每日笔记]",
            "---",
            "",
            f"# 📅 {date_str} 微信消息",
            "",
            f"> 共 **{len(messages)}** 条，"
            f"**{len(contacts)}** 个会话，"
            f"**⭐ {important_count}** 重要，"
            f"**🟢 {self_count}** 自己，"
            f"**👥 {group_count}** 群聊",
            "",
            "---",
            "",
            "## 🕒 最新消息",
            "",
        ]

        # 最新消息区（按时间 倒序 = 最新在前）
        for msg in sorted_messages:
            lines.append(self._format_message_block(msg))

        # 会话分组概览
        lines.append("")
        lines.append("---")
        lines.append("")
        lines.append("## 👥 会话分栏")
        lines.append("")
        for contact_name in sorted(contacts.keys()):
            msgs = contacts[contact_name]
            sorted_group = sorted(msgs, key=_ts, reverse=True)
            is_group = any(bool(m.get("is_group")) for m in msgs)
            mark = "👥" if is_group else "👤"
            lines.append(f"### {mark} {contact_name}  ({len(msgs)}条)")
            lines.append("")
            # 群聊：成员聚合
            if is_group:
                members = {}
                for m in sorted_group:
                    s = m.get("sender", "other")
                    gm = m.get("group_member")
                    if s == "me":
                        key = "🟢 我"
                    elif gm:
                        key = f"👥 {gm}"
                    else:
                        key = f"👤 {contact_name}"
                    if key not in members:
                        members[key] = []
                    members[key].append(m)
                for member_key, member_msgs in members.items():
                    lines.append(f"#### {member_key}  ({len(member_msgs)}条)")
                    lines.append("")
                    for m in member_msgs:
                        lines.append(self._format_message_block(m))
                    lines.append("")
            else:
                for m in sorted_group:
                    lines.append(self._format_message_block(m))
                lines.append("")

        # ⭐ 重要消息（在最底部的分栏，方便用户快速定位，也可翻顶部最新区直接看）
        important_msgs = [m for m in messages if m.get("is_important")]
        if important_msgs:
            lines.append("---")
            lines.append("")
            lines.append("## ⭐ 重要消息 汇总")
            lines.append("")
            for m in sorted(important_msgs, key=_ts, reverse=True):
                lines.append(self._format_message_block(m))
            lines.append("")

        return "\n".join(lines)

    def _format_contact_note(self, contact_name, messages):
        """V3: 联系人笔记 — 最新消息在顶部，群聊分 成员 子标题"""
        important_count = sum(1 for m in messages if m.get("is_important"))
        is_group = any(bool(m.get("is_group")) for m in messages)

        def _ts(m):
            t = m.get("timestamp", "")
            return str(t)
        sorted_msgs = sorted(messages, key=_ts, reverse=True)

        lines = [
            "---",
            f"contact: {contact_name}",
            f"chat_type: {'group' if is_group else 'personal'}",
            f"total_messages: {len(messages)}",
            f"important_count: {important_count}",
            f"updated: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            "tags: [微信, 联系人]",
            "---",
            "",
            f"# {'👥' if is_group else '👤'} {contact_name}",
            "",
            f"> 共 **{len(messages)}** 条，"
            f"**⭐ {important_count}** 重要"
            f"{'（群聊，按成员分栏）' if is_group else '（私聊）'}",
            "",
            "---",
            "",
            "## 🕒 最新消息",
            "",
        ]

        # 最新一条流
        for msg in sorted_msgs:
            lines.append(self._format_message_block(msg))

        # 分成员
        if is_group:
            lines.append("")
            lines.append("---")
            lines.append("")
            lines.append("## 👥 群成员分栏")
            lines.append("")
            members = {}
            for m in sorted_msgs:
                s = m.get("sender", "other")
                gm = m.get("group_member")
                if s == "me":
                    key = "🟢 我"
                elif gm:
                    key = f"👥 {gm}"
                else:
                    key = f"👤 {contact_name}"
                if key not in members:
                    members[key] = []
                members[key].append(m)
            for member_key, member_msgs in members.items():
                lines.append(f"### {member_key}  ({len(member_msgs)}条)")
                lines.append("")
                for m in member_msgs:
                    lines.append(self._format_message_block(m))
                lines.append("")

        # 重要消息
        important_msgs = [m for m in messages if m.get("is_important")]
        if important_msgs:
            lines.append("---")
            lines.append("")
            lines.append("## ⭐ 重要消息")
            lines.append("")
            for m in sorted(important_msgs, key=_ts, reverse=True):
                lines.append(self._format_message_block(m))
            lines.append("")

        return "\n".join(lines)

    def _format_important_note(self, all_messages):
        """V3: 重要消息汇总 — 最新在顶部"""
        important = [m for m in all_messages if m.get("is_important")]

        def _ts(m):
            t = m.get("timestamp", "")
            return str(t)
        sorted_important = sorted(important, key=_ts, reverse=True)

        lines = [
            "---",
            "type: 重要消息汇总",
            f"total: {len(sorted_important)}",
            f"updated: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            "tags: [微信, 重要]",
            "---",
            "",
            "# ⭐ 重要消息汇总",
            "",
            f"> 共 **{len(sorted_important)}** 条重要消息（按时间从新到旧）",
            "",
            "---",
            "",
        ]

        contacts = {}
        for msg in sorted_important:
            name = msg.get("contact", "未命名")
            if name not in contacts:
                contacts[name] = []
            contacts[name].append(msg)

        # 最顶部：最新消息列表（全局流）
        for msg in sorted_important:
            lines.append(self._format_message_block(msg))
        lines.append("")
        lines.append("---")
        lines.append("")
        lines.append("## 👥 按会话分组")
        lines.append("")
        for contact_name in sorted(contacts.keys()):
            msgs = sorted(contacts[contact_name], key=_ts, reverse=True)
            is_group = any(bool(m.get("is_group")) for m in msgs)
            mark = "👥" if is_group else "👤"
            lines.append(f"### {mark} {contact_name}  ({len(msgs)}条)")
            lines.append("")
            for msg in msgs:
                lines.append(self._format_message_block(msg))
            lines.append("")

        return "\n".join(lines)

    # ================================================================
    # 方案A：文件直写
    # ================================================================

    def _sync_file(self, msg_data):
        """方案A：文件直写 — 最新消息插在 Frontmatter 之后（顶部）"""
        try:
            base_dir = os.path.join(self.vault_path, self.folder)
            daily_dir = os.path.join(base_dir, "每日")
            contact_dir = os.path.join(base_dir, "联系人")
            os.makedirs(daily_dir, exist_ok=True)
            os.makedirs(contact_dir, exist_ok=True)

            contact = self._sanitize_filename(msg_data.get("contact", "未命名"))
            date_str = datetime.now().strftime("%Y-%m-%d")

            block = self._format_message_block(msg_data)

            # 1. 每日笔记 — 最新在顶部
            daily_path = os.path.join(daily_dir, f"{date_str}.md")
            self._prepend_to_file(daily_path, block, title=f"📅 {date_str} 微信消息")

            # 2. 联系人笔记 — 最新在顶部
            contact_path = os.path.join(contact_dir, f"{contact}.md")
            self._prepend_to_file(contact_path, block, title=contact)

            # 3. 重要消息 — 最新在顶部
            if msg_data.get("is_important"):
                important_path = os.path.join(base_dir, "重要消息.md")
                self._prepend_to_file(important_path, block, title="⭐ 重要消息汇总")

            return True

        except Exception as e:
            logger.error(f"[Obsidian] 文件同步失败: {e}")
            return False

    def _append_to_file(self, filepath, content, title=""):
        """兼容：重定向到顶部插入"""
        self._prepend_to_file(filepath, content, title=title)

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
        """方案B：REST API — 最新消息插在 Frontmatter 之后（顶部）"""
        try:
            contact = self._sanitize_filename(msg_data.get("contact", "未命名"))
            date_str = datetime.now().strftime("%Y-%m-%d")
            block = self._format_message_block(msg_data)

            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "text/markdown",
            }

            daily_filename = f"{self.folder}/每日/{date_str}.md"
            self._api_prepend(daily_filename, block, headers,
                              default_title=f"📅 {date_str} 微信消息")

            contact_filename = f"{self.folder}/联系人/{contact}.md"
            self._api_prepend(contact_filename, block, headers,
                              default_title=contact)

            if msg_data.get("is_important"):
                important_filename = f"{self.folder}/重要消息.md"
                self._api_prepend(important_filename, block, headers,
                                  default_title="⭐ 重要消息汇总")

            return True

        except Exception as e:
            logger.error(f"[Obsidian] API同步失败: {e}")
            return False

    def _api_prepend(self, filename, content, headers, default_title="笔记"):
        """V3: 通过 REST API 把新消息块插入到 Frontmatter 之后（最新在顶部）"""
        url = f"{self.api_url}/vault/{filename}"
        ts = datetime.now().strftime("%Y-%m-%d %H:%M")

        try:
            resp = requests.get(url, headers=headers, timeout=5)
            if resp.status_code == 200:
                existing = resp.text or ""

                prefix = ""
                body = existing
                if existing.startswith("---\n"):
                    end = existing.find("\n---", 4)
                    if end > 0:
                        next_newline = existing.find("\n", end + 4)
                        cut = next_newline if next_newline != -1 else len(existing)
                        fm_lines = existing[:cut].splitlines()
                        new_fm = []
                        seen_updated = False
                        for ln in fm_lines:
                            if ln.startswith("updated:"):
                                new_fm.append(f"updated: {ts}")
                                seen_updated = True
                            else:
                                new_fm.append(ln)
                        if not seen_updated and new_fm:
                            # 在倒数第二行（最后一行 ---）之前插入
                            if new_fm[-1] == "---":
                                new_fm.insert(-1, f"updated: {ts}")
                            else:
                                new_fm.append(f"updated: {ts}")
                        prefix = "\n".join(new_fm).rstrip() + "\n\n"
                        body = existing[cut:].lstrip("\n")

                # 再跳过"介绍区"（H1~H3/HR/空行/普通>引用；不能跳过已有的 > [!callout]）
                rest_lines = body.splitlines()
                skip_end = 0
                for line in rest_lines:
                    s = line.strip()
                    if not s:
                        skip_end += 1
                        continue
                    if s.startswith(("# ", "## ", "### ", "---")):
                        skip_end += 1
                        continue
                    if s.startswith("> ") and ("[!" not in s[:30]):
                        skip_end += 1
                        continue
                    break
                kept_prefix = "\n".join(rest_lines[:skip_end]).rstrip()
                kept_body = "\n".join(rest_lines[skip_end:]).lstrip("\n")

                if not prefix:
                    prefix = (f"---\ncreated: {ts}\nsource: 微信AI助手"
                              f"\ntags: [微信]\nupdated: {ts}\n---\n\n"
                              f"# {default_title}\n\n")

                new_content = prefix
                if kept_prefix:
                    new_content += kept_prefix + "\n\n"
                new_content += content + "\n\n"
                if kept_body.strip():
                    new_content += kept_body.rstrip() + "\n"

                requests.put(url,
                             headers={**headers, "Content-Type": "text/markdown"},
                             data=new_content.encode("utf-8"), timeout=10)
            else:
                new_content = (f"---\ncreated: {ts}\nsource: 微信AI助手"
                               f"\ntags: [微信]\nupdated: {ts}\n---\n\n"
                               f"# {default_title}\n\n{content}")
                requests.put(url,
                             headers={**headers, "Content-Type": "text/markdown"},
                             data=new_content.encode("utf-8"), timeout=10)
        except requests.exceptions.ConnectionError:
            logger.warning("[Obsidian] REST API连接失败，请确保Obsidian已运行且Local REST API插件已启用")
        except Exception as e:
            logger.warning(f"[Obsidian] API前插失败({filename}): {e}")

    def _api_append(self, filename, content, headers):
        """兼容：重定向到前插 API"""
        self._api_prepend(filename, content, headers)

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
