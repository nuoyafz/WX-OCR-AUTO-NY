"""
Obsidian 同步模块 — V4 重构（双向 + 结构化 + 缓冲写入）
================================================================
架构总览：
  - 方案A：文件直写（零配置，开箱即用，原子写入 + 内存缓冲批量）
  - 方案B：Local REST API（PUT 全量 / PATCH 追加到指定标题块）

文件结构（按联系人 + 日期拆分，避免单文件几万行）：
  vault/
  ├── 微信消息/
  │   ├── 每日/
  │   │   └── 2026-08-21.md              ← 每日聚合（嵌入当天所有会话）
  │   ├── 联系人/
  │   │   ├── 张三/
  │   │   │   ├── 张三-2026-08-21.md     ← 单日单联系人
  │   │   │   └── 张三-2026-08-22.md
  │   │   └── 李四/
  │   ├── 微信联系人/                     ← P2 联系人画像独立笔记
  │   │   └── 张三.md
  │   ├── 微信待办.md                     ← P1 Tasks 聚合块
  │   └── 重要消息.md

V4 关键能力：
  P0: 完整 Frontmatter / 按联系人+日期拆分 / 原子写入 / 内存缓冲 / 失败告警
  P1: API PATCH 追加指定块 / 双向内部链接 / Tasks 打通 / 元标签 / 读取知识库 / webhook
  P2: 联系人画像 / AI 会话摘要 / Dataview 片段 / 过滤规则 / 增量重同步
"""
import os
import re
import json
import time
import threading
import tempfile
import logging
import requests
from datetime import datetime, date

logger = logging.getLogger(__name__)

# 写缓冲默认参数
DEFAULT_FLUSH_INTERVAL = 30.0      # 秒
DEFAULT_FLUSH_BATCH = 8            # 条
DEFAULT_FLUSH_MAX_WAIT = 60.0      # 单条消息在缓冲中最长停留秒数


class ObsidianSync:
    """Obsidian 双向同步器（V4）"""

    def __init__(self, config, on_write_error=None):
        """
        config: dict，来自 config.yaml 的 obsidian 段
        on_write_error: callable(str) -> None，写入失败时回调（供 UI 弹窗告警）
        """
        self.config = config or {}
        self.vault_path = self.config.get("vault_path", "")
        self.mode = self.config.get("mode", "file")
        self.api_url = self.config.get("api_url", "http://127.0.0.1:27124")
        self.api_key = self.config.get("api_key", "")
        self.auto_sync = self.config.get("auto_sync", True)
        self.folder = self.config.get("folder", "微信消息")

        # P0: 缓冲写入配置
        self.flush_interval = float(self.config.get("flush_interval", DEFAULT_FLUSH_INTERVAL))
        self.flush_batch = int(self.config.get("flush_batch", DEFAULT_FLUSH_BATCH))
        self.on_write_error = on_write_error

        # P1: 高级能力开关
        adv = self.config.get("advanced", {}) or {}
        self.enable_bidirectional_links = adv.get("enable_bidirectional_links", True)
        self.enable_tags = adv.get("enable_tags", True)
        self.enable_read = adv.get("enable_read", False)          # 读取知识库作为上下文
        self.enable_webhook = adv.get("enable_webhook", False)
        self.webhook_url = adv.get("webhook_url", "")
        self.enable_canvas = adv.get("enable_canvas", False)
        self.canvas_name = adv.get("canvas_name", "微信社交图谱")
        self.enable_tasks = adv.get("enable_tasks", True)
        self.tasks_note = adv.get("tasks_note", f"{self.folder}/微信待办")

        # P2: 过滤规则
        filt = self.config.get("filter", {}) or {}
        self.filter_ads = filt.get("skip_ads", True)
        self.filter_low_priority = filt.get("skip_low_priority", False)
        self.filter_only_tasks_or_urgent = filt.get("only_tasks_or_urgent", False)

        # 联系人画像
        self.enable_profile = adv.get("enable_profile", True)
        # AI 会话摘要
        self.enable_summary = adv.get("enable_summary", True)
        # Dataview 片段
        self.enable_dataview = adv.get("enable_dataview", True)

        self._file_enabled = bool(self.vault_path) and self.mode in ("file", "both")
        self._api_enabled = bool(self.api_key) and self.mode in ("api", "both")

        if self._file_enabled:
            logger.info(f"[Obsidian] 文件模式已启用, vault={self.vault_path}")
        if self._api_enabled:
            logger.info(f"[Obsidian] API模式已启用, url={self.api_url}")

        # P0: 内存写缓冲 —— key=(contact, date_str) -> list[msg]
        self._buffer = {}
        self._buffer_lock = threading.Lock()
        self._buffer_first_ts = {}     # key -> 首次入缓冲时间戳
        self._flush_timer = None
        if self._file_enabled and self.flush_interval > 0:
            self._start_flush_timer()

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    def _start_flush_timer(self):
        try:
            self._flush_timer = threading.Timer(self.flush_interval, self._periodic_flush)
            self._flush_timer.daemon = True
            self._flush_timer.start()
        except Exception:
            pass

    def shutdown(self):
        """停止定时器并强制 flush 剩余缓冲（程序退出时调用）"""
        try:
            if self._flush_timer:
                self._flush_timer.cancel()
        except Exception:
            pass
        self.flush_all()

    def _periodic_flush(self):
        try:
            self._flush_due_buffers()
        except Exception as e:
            logger.error(f"[Obsidian] 定时flush异常: {e}")
        finally:
            self._start_flush_timer()

    def _flush_due_buffers(self):
        """把超过了 flush_interval 或攒够 flush_batch 的缓冲刷盘"""
        with self._buffer_lock:
            due_keys = []
            now = time.time()
            for key, msgs in self._buffer.items():
                first_ts = self._buffer_first_ts.get(key, now)
                if len(msgs) >= self.flush_batch or (now - first_ts) >= self.flush_interval:
                    due_keys.append(key)
            if not due_keys:
                return
            snapshot = {k: self._buffer.pop(k) for k in due_keys}
            for k in due_keys:
                self._buffer_first_ts.pop(k, None)
        # 在锁外刷盘，避免阻塞
        for key, msgs in snapshot.items():
            try:
                self._write_buffered(key, msgs)
            except Exception as e:
                self._report_error(f"缓冲写入失败({key}): {e}")

    def flush_all(self):
        """强制把全部缓冲写入（程序退出 / 手动触发）"""
        with self._buffer_lock:
            if not self._buffer:
                return
            snapshot = dict(self._buffer)
            self._buffer.clear()
            self._buffer_first_ts.clear()
        for key, msgs in snapshot.items():
            try:
                self._write_buffered(key, msgs)
            except Exception as e:
                self._report_error(f"强制flush失败({key}): {e}")

    def _report_error(self, msg):
        logger.error(f"[Obsidian] {msg}")
        if self.on_write_error:
            try:
                self.on_write_error(msg)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # 工具
    # ------------------------------------------------------------------
    @property
    def enabled(self):
        return self.auto_sync and (self._file_enabled or self._api_enabled)

    def _sanitize_filename(self, name):
        if not name:
            return "未命名"
        safe = re.sub(r'[<>:"/\\|?*]', '_', name)
        safe = safe.strip().rstrip('.')
        return safe[:50] if len(safe) > 50 else safe

    def _date_str(self, ts):
        if hasattr(ts, "strftime"):
            return ts.strftime("%Y-%m-%d")
        if isinstance(ts, str) and len(ts) >= 10:
            return ts[:10]
        return datetime.now().strftime("%Y-%m-%d")

    @staticmethod
    def _quote_yaml(v):
        """YAML 标量安全包裹：含特殊字符用双引号"""
        if v is None:
            return '""'
        s = str(v)
        if s == "" or re.search(r'[:#\-\[\]\{\},&*?|<>=!%@`"]', s) or s.strip() != s:
            escaped = s.replace('\\', '\\\\').replace('"', '\\"')
            return f'"{escaped}"'
        return s

    # ------------------------------------------------------------------
    # P0: Frontmatter 构造（Dataview 友好）
    # ------------------------------------------------------------------
    def _build_frontmatter(self, contact, msg_list, chat_type):
        """根据一批消息 + AI 抽取结果构造完整 Frontmatter"""
        date_str = self._date_str(msg_list[0].get("timestamp", "")) if msg_list else datetime.now().strftime("%Y-%m-%d")

        # 聚合来自抽取结果的字段
        emotions, categories, urgencies = [], [], []
        all_tags = set(["微信聊天"])
        task_count = 0
        for m in msg_list:
            ex = m.get("extracted_fields") or m.get("extracted") or {}
            if isinstance(ex, dict):
                if ex.get("emotion"):
                    emotions.append(ex["emotion"])
                if ex.get("category"):
                    categories.append(ex["category"])
                if ex.get("urgency") is not None:
                    try:
                        urgencies.append(int(ex["urgency"]))
                    except (ValueError, TypeError):
                        pass
                if ex.get("tags"):
                    for t in ex["tags"]:
                        all_tags.add(t)
            # 顶层也可能有
            if m.get("emotion"):
                emotions.append(m["emotion"])
            if m.get("category"):
                categories.append(m["category"])
            if m.get("urgency") is not None:
                try:
                    urgencies.append(int(m["urgency"]))
                except (ValueError, TypeError):
                    pass
            # 待办计数：从关键词或提取任务
            kws = m.get("keywords") or []
            if isinstance(kws, list):
                for k in kws:
                    if "待办" in str(k) or "任务" in str(k):
                        task_count += 1
            if m.get("extracted_tasks"):
                try:
                    task_count += len(m["extracted_tasks"])
                except TypeError:
                    task_count += 1

        # 取众数/最大值
        emotion = self._most_common(emotions) or "中性"
        category = self._most_common(categories) or "其他"
        urgency = max(urgencies) if urgencies else 1

        # 自动标签
        if self.enable_tags:
            for m in msg_list:
                if m.get("is_important"):
                    all_tags.add("微信-重要")
                if m.get("is_group"):
                    all_tags.add("微信-群聊")
                ex = m.get("extracted_fields") or m.get("extracted") or {}
                if isinstance(ex, dict):
                    if ex.get("is_ad"):
                        all_tags.add("微信-广告")
                    if ex.get("negative_emotion"):
                        all_tags.add("微信-消极情绪")
                if m.get("extracted_tasks"):
                    all_tags.add("待办-跟进")

        lines = [
            "---",
            f'title: {self._quote_yaml(f"{contact}-{date_str}")}',
            f'contact: {self._quote_yaml(contact)}',
            f'chat_type: {chat_type}',
            f'chat_date: {date_str}',
            f'emotion: {self._quote_yaml(emotion)}',
            f'category: {self._quote_yaml(category)}',
            f'urgency: {urgency}',
            f'source: NOYA-Chat',
            f'extracted_tasks_count: {task_count}',
            f'message_count: {len(msg_list)}',
        ]
        tag_list = sorted(t for t in all_tags if t)
        lines.append(f'tags: [{", ".join(self._quote_yaml(t) for t in tag_list)}]')
        lines.append(f'updated: {datetime.now().strftime("%Y-%m-%d %H:%M")}')
        lines.append("---")
        return "\n".join(lines)

    @staticmethod
    def _most_common(seq):
        if not seq:
            return None
        cnt = {}
        for s in seq:
            cnt[s] = cnt.get(s, 0) + 1
        return max(cnt.items(), key=lambda kv: kv[1])[0]

    # ------------------------------------------------------------------
    # 消息块格式（微信气泡 Callout）+ 双向链接
    # ------------------------------------------------------------------
    def _format_message_block(self, msg_data):
        contact = msg_data.get("contact", "")
        sender = msg_data.get("sender", "other")
        content = msg_data.get("content", "") or msg_data.get("raw_text", "")
        timestamp = msg_data.get("timestamp", "")
        is_important = msg_data.get("is_important", False)
        importance_reason = msg_data.get("importance_reason", "")
        keywords = msg_data.get("keywords", []) or msg_data.get("matched_keywords", [])
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

        if isinstance(timestamp, str) and timestamp:
            ts_display = timestamp
        elif hasattr(timestamp, "strftime"):
            ts_display = timestamp.strftime("%Y-%m-%d %H:%M")
        else:
            ts_display = datetime.now().strftime("%Y-%m-%d %H:%M")

        if is_important:
            callout_type = "quote-danger" if not is_self else "quote-success"
        elif is_self:
            callout_type = "quote-success"
        else:
            callout_type = "quote"

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

        safe_content = str(content).strip() or "(空)"
        content_lines = safe_content.splitlines() or [safe_content]
        content_callout = "\n".join([f"> {l}" for l in content_lines])

        # P1: 自动双向内部链接 —— 把联系人人名 / 项目名包成 [[...]]
        content_callout = self._auto_link(content_callout, msg_data)

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

        lines = [
            f"> [!{callout_type}] {callout_title}",
            ">",
            content_callout,
        ]
        if meta_chips:
            lines.append(">")
            for chip in meta_chips:
                lines.append(f"> - {chip}")
        # P1: 自动链接到当日日记
        if self.enable_bidirectional_links:
            lines.append(">")
            lines.append(f"> 🔗 [[{self._date_str(timestamp)}]]")
        lines.append("")
        return "\n".join(lines)

    _LINK_NAME_RE = re.compile(r'([一-龥]{2,8}同学|[一-龥]{2,6}(?:老师|学长|学姐|总|经理|老板|哥|姐|弟|妹))')

    def _auto_link(self, text, msg_data):
        """P1: 把消息里出现的人名/项目名自动 [[链接]]（简单规则，避免误伤正文）"""
        if not self.enable_bidirectional_links:
            return text
        contact = msg_data.get("contact", "")
        # 联系人本身链接已在标题，正文里额外匹配常见称谓
        def _repl(m):
            name = m.group(1)
            if name == contact:
                return name
            return f"[[{name}]]"
        try:
            return self._LINK_NAME_RE.sub(_repl, text)
        except Exception:
            return text

    # ------------------------------------------------------------------
    # 笔记正文构造（联系人 + 日期拆分）
    # ------------------------------------------------------------------
    def _format_contact_daily_note(self, contact, date_str, msgs):
        """单联系人单日笔记全文（含 Frontmatter + 摘要 + 聊天块 + Dataview）"""
        is_group = any(bool(m.get("is_group")) for m in msgs)
        chat_type = "group" if is_group else "private"
        fm = self._build_frontmatter(contact, msgs, chat_type)

        # 排序：最新在前
        def _ts(m):
            t = m.get("timestamp", "")
            return str(t)
        sorted_msgs = sorted(msgs, key=_ts, reverse=True)

        lines = [fm, ""]
        lines.append(f"# {'👥' if is_group else '👤'} {contact} · {date_str}")
        lines.append("")

        # P2: 双向链接到当日日记
        if self.enable_bidirectional_links:
            lines.append(f"> 关联日记：[[{date_str}]]")
            lines.append("")

        # P2: AI 会话摘要块
        if self.enable_summary:
            lines.append("## 🤖 AI 会话摘要")
            lines.append("")
            lines.append(self._build_daily_summary(contact, sorted_msgs))
            lines.append("")

        # 聊天记录块（PATCH 模式下只在 ## 聊天记录 下追加）
        lines.append("## 💬 聊天记录")
        lines.append("")
        for m in sorted_msgs:
            lines.append(self._format_message_block(m))

        # P2: Dataview 片段
        if self.enable_dataview:
            lines.append("")
            lines.append("---")
            lines.append("")
            lines.append("## 📊 Dataview 查询")
            lines.append("")
            lines.append("```dataview")
            lines.append(f'TABLE emotion, urgency, category, extracted_tasks_count')
            lines.append(f'FROM "{self.folder}/联系人/{self._sanitize_filename(contact)}"')
            lines.append(f'WHERE contact = "{contact}"')
            lines.append("SORT chat_date DESC")
            lines.append("```")
            lines.append("")

        return "\n".join(lines)

    def _build_daily_summary(self, contact, sorted_msgs):
        """P2: 生成当日会话摘要（基于抽取结果聚合，不调 LLM 以免卡顿）"""
        total = len(sorted_msgs)
        important = [m for m in sorted_msgs if m.get("is_important")]
        tasks = []
        for m in sorted_msgs:
            if m.get("extracted_tasks"):
                tasks.extend(m["extracted_tasks"])
            kws = m.get("keywords") or []
            if isinstance(kws, list):
                for k in kws:
                    if "待办" in str(k) or "任务" in str(k):
                        tasks.append(str(k))
        parts = [f"> 共 **{total}** 条消息" + ("（群聊）" if sorted_msgs and sorted_msgs[0].get("is_group") else "（私聊）")]
        if important:
            imp_txt = "；".join(f"{m.get('content','')[:20]}" for m in important[:3])
            parts.append(f"> ⭐ 重要：{imp_txt}")
        if tasks:
            parts.append(f"> 📌 待办：{'；'.join(str(t)[:30] for t in tasks[:5])}")
        return "\n".join(parts)

    # ------------------------------------------------------------------
    # P0: 原子写入（临时文件 rename 替换，杜绝 Obsidian 打开时冲突损坏）
    # ------------------------------------------------------------------
    def _atomic_write(self, filepath, content):
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        dir_name = os.path.dirname(filepath)
        fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(content)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, filepath)   # 原子替换
        except Exception:
            try:
                os.remove(tmp_path)
            except Exception:
                pass
            raise

    # ------------------------------------------------------------------
    # 缓冲落盘：把 key=(contact,date) 的一批消息写出
    # ------------------------------------------------------------------
    def _write_buffered(self, key, msgs):
        contact, date_str = key
        if self._file_enabled:
            self._write_contact_daily_file(contact, date_str, msgs)
            # 每日聚合笔记（嵌入当天会话链接）
            self._append_to_daily_aggregator(date_str, contact)
            # P2: 联系人画像更新
            if self.enable_profile:
                self._update_profile(contact, msgs)
            # P1: Tasks 聚合
            if self.enable_tasks:
                self._collect_tasks(contact, msgs)
            # P1: webhook
            if self.enable_webhook and self.webhook_url:
                self._fire_webhook(contact, date_str, msgs)
            # P1: Canvas
            if self.enable_canvas:
                self._update_canvas(contact, msgs)
        if self._api_enabled:
            self._api_write_buffered(contact, date_str, msgs)

    def _write_contact_daily_file(self, contact, date_str, msgs):
        """写出 联系人/<contact>/<contact>-<date>.md（全量重写该文件）"""
        try:
            contact_dir = os.path.join(self.vault_path, self.folder, "联系人", self._sanitize_filename(contact))
            os.makedirs(contact_dir, exist_ok=True)
            fname = f"{self._sanitize_filename(contact)}-{date_str}.md"
            fpath = os.path.join(contact_dir, fname)
            content = self._format_contact_daily_note(contact, date_str, msgs)
            self._atomic_write(fpath, content)
        except Exception as e:
            self._report_error(f"写联系人日笔记失败({contact}/{date_str}): {e}")

    def _append_to_daily_aggregator(self, date_str, contact):
        """每日聚合笔记：在 ## 聊天记录 下追加一条 [[联系人/日期]] 嵌入链接"""
        try:
            daily_dir = os.path.join(self.vault_path, self.folder, "每日")
            os.makedirs(daily_dir, exist_ok=True)
            fpath = os.path.join(daily_dir, f"{date_str}.md")
            ts = datetime.now().strftime("%Y-%m-%d %H:%M")
            embed_line = (f"- 🕒 {ts} → [[{self.folder}/联系人/{self._sanitize_filename(contact)}/"
                          f"{self._sanitize_filename(contact)}-{date_str}|{contact} {date_str}]]")
            if os.path.exists(fpath):
                self._append_under_heading(fpath, "## 💬 今日会话", embed_line)
            else:
                content = (
                    f"---\ndate: {date_str}\nsource: NOYA-Chat\n"
                    f"tags: [微信, 每日笔记]\nupdated: {ts}\n---\n\n"
                    f"# 📅 {date_str} 微信消息\n\n"
                    f"## 💬 今日会话\n\n{embed_line}\n"
                )
                self._atomic_write(fpath, content)
        except Exception as e:
            self._report_error(f"写每日聚合失败({date_str}): {e}")

    def _append_under_heading(self, filepath, heading, line):
        """在指定标题块下追加一行（标题不存在则创建）。原子读写。"""
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                existing = f.read()
            if heading not in existing:
                existing = existing.rstrip() + f"\n\n{heading}\n\n{line}\n"
            else:
                # 在 heading 之后第一个空行/下一个标题前插入
                lines = existing.splitlines()
                out = []
                inserted = False
                for i, ln in enumerate(lines):
                    out.append(ln)
                    if ln.strip() == heading and not inserted:
                        # 找到下一个非空内容行之前插入
                        j = i + 1
                        while j < len(lines) and lines[j].strip() == "":
                            out.append(lines[j])
                            j += 1
                        out.append(line)
                        inserted = True
                existing = "\n".join(out) + "\n"
            self._atomic_write(filepath, existing)
        except Exception as e:
            self._report_error(f"追加标题块失败({filepath}): {e}")

    # ------------------------------------------------------------------
    # P2: 联系人画像独立笔记
    # ------------------------------------------------------------------
    def _update_profile(self, contact, msgs):
        try:
            profile_dir = os.path.join(self.vault_path, self.folder, "微信联系人")
            os.makedirs(profile_dir, exist_ok=True)
            fpath = os.path.join(profile_dir, f"{self._sanitize_filename(contact)}.md")
            ts = datetime.now().strftime("%Y-%m-%d %H:%M")
            msgs_count = len(msgs)
            is_group = any(bool(m.get("is_group")) for m in msgs)
            emotions = [m.get("emotion") or (m.get("extracted_fields") or {}).get("emotion")
                        for m in msgs if (m.get("emotion") or (m.get("extracted_fields") or {}).get("emotion"))]
            last_emotion = emotions[-1] if emotions else "中性"

            if os.path.exists(fpath):
                with open(fpath, "r", encoding="utf-8") as f:
                    old = f.read()
                # 更新统计行
                old = re.sub(r'total_messages: \d+', f'total_messages: {msgs_count}', old)
                old = re.sub(r'last_emotion: .*', f'last_emotion: {last_emotion}', old)
                old = re.sub(r'updated: .*', f'updated: {ts}', old)
                old = re.sub(r'is_group: (true|false)', f'is_group: {"true" if is_group else "false"}', old)
                if "## 📈 沟通统计" not in old:
                    old = old.rstrip() + f"\n\n## 📈 沟通统计\n\n- 最近一次情绪：{last_emotion}\n- 最近更新：{ts}\n"
                self._atomic_write(fpath, old)
            else:
                content = (
                    f"---\ncontact: {self._quote_yaml(contact)}\n"
                    f"is_group: {'true' if is_group else 'false'}\n"
                    f"total_messages: {msgs_count}\n"
                    f"last_emotion: {last_emotion}\n"
                    f"tags: [微信联系人]\nupdated: {ts}\n---\n\n"
                    f"# 👤 {contact}\n\n"
                    f"> 自动生成的联系人画像（微信AI助手维护）\n\n"
                    f"## 🏷 标签\n\n- #微信联系人\n\n"
                    f"## 📈 沟通统计\n\n- 最近一次情绪：{last_emotion}\n- 最近更新：{ts}\n\n"
                    f"## 📝 重要事件\n\n\n"
                    f"## 🔗 反向链接\n\n- 会话记录：[[{self.folder}/联系人/{self._sanitize_filename(contact)}]]\n"
                )
                self._atomic_write(fpath, content)
        except Exception as e:
            self._report_error(f"写联系人画像失败({contact}): {e}")

    # ------------------------------------------------------------------
    # P1: Tasks 聚合（PATCH 追加待办块）
    # ------------------------------------------------------------------
    def _collect_tasks(self, contact, msgs):
        try:
            tasks = []
            for m in msgs:
                et = m.get("extracted_tasks")
                if et:
                    if isinstance(et, list):
                        tasks.extend(str(t) for t in et)
                    else:
                        tasks.append(str(et))
                kws = m.get("keywords") or []
                if isinstance(kws, list):
                    for k in kws:
                        if "待办" in str(k) or "任务" in str(k):
                            tasks.append(str(k))
            if not tasks:
                return
            ts = datetime.now().strftime("%Y-%m-%d")
            fpath = os.path.join(self.vault_path, self.folder, "微信待办.md")
            block = "\n".join(f"- [ ] {t} 📅 {ts} 🔗 [[{self._sanitize_filename(contact)}]]" for t in tasks[:10])
            if os.path.exists(fpath):
                self._append_under_heading(fpath, "## 📋 待办", block)
            else:
                content = (
                    f"---\ntype: 微信待办\nsource: NOYA-Chat\ntags: [微信, 待办]\n"
                    f"updated: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n---\n\n"
                    f"# 📋 微信待办\n\n## 📋 待办\n\n{block}\n"
                )
                self._atomic_write(fpath, content)
        except Exception as e:
            self._report_error(f"写待办失败: {e}")

    # ------------------------------------------------------------------
    # P1: webhook 回调
    # ------------------------------------------------------------------
    def _fire_webhook(self, contact, date_str, msgs):
        try:
            payload = {
                "event": "obsidian_message_synced",
                "contact": contact,
                "chat_date": date_str,
                "message_count": len(msgs),
                "timestamp": datetime.now().isoformat(),
                "messages": [
                    {
                        "sender": m.get("sender"),
                        "content": (m.get("content") or m.get("raw_text", ""))[:500],
                        "is_important": m.get("is_important", False),
                        "keywords": m.get("keywords", []),
                    } for m in msgs
                ],
            }
            requests.post(self.webhook_url, json=payload, timeout=5)
        except Exception as e:
            logger.warning(f"[Obsidian] webhook发送失败: {e}")

    # ------------------------------------------------------------------
    # P1: Canvas 社交图谱（API 模式）
    # ------------------------------------------------------------------
    def _update_canvas(self, contact, msgs):
        if not self._api_enabled:
            return
        try:
            canvas_file = f"{self.folder}/{self.canvas_name}.canvas"
            url = f"{self.api_url}/vault/{canvas_file}"
            headers = {"Authorization": f"Bearer {self.api_key}",
                       "Content-Type": "application/json"}
            resp = requests.get(url, headers=headers, timeout=5)
            nodes = []
            if resp.status_code == 200:
                try:
                    data = resp.json()
                    nodes = data.get("nodes", [])
                except Exception:
                    nodes = []
            # 找或建联系人节点
            node_id = f"node-{abs(hash(contact)) % 10**8}"
            found = False
            for nd in nodes:
                if nd.get("text") and contact in nd.get("text", ""):
                    found = True
                    break
            if not found:
                nodes.append({
                    "id": node_id,
                    "type": "text",
                    "text": f"[[{self._sanitize_filename(contact)}]]",
                    "x": (len(nodes) % 5) * 250,
                    "y": (len(nodes) // 5) * 200,
                    "width": 200, "height": 60,
                })
                payload = {"nodes": nodes, "edges": []}
                requests.put(url, headers=headers, data=json.dumps(payload).encode("utf-8"), timeout=10)
        except Exception as e:
            logger.warning(f"[Obsidian] Canvas更新失败: {e}")

    # ------------------------------------------------------------------
    # P1: 读取知识库（作为 AI 回复上下文）
    # ------------------------------------------------------------------
    def read_contact_context(self, contact):
        """读取联系人对应 Obsidian 笔记（画像 + 最近会话）作为上下文字符串。
        返回 str 或 None。API/文件模式都支持（文件模式直接读盘）。"""
        if not self.enable_read:
            return None
        parts = []
        # 画像笔记
        profile_path = os.path.join(self.vault_path, self.folder, "微信联系人",
                                    f"{self._sanitize_filename(contact)}.md")
        if self._file_enabled and os.path.exists(profile_path):
            try:
                with open(profile_path, "r", encoding="utf-8") as f:
                    parts.append(f"[联系人画像 {contact}]\n" + f.read()[:1500])
            except Exception:
                pass
        # 最近会话笔记（文件模式）
        if self._file_enabled:
            contact_dir = os.path.join(self.vault_path, self.folder, "联系人", self._sanitize_filename(contact))
            if os.path.isdir(contact_dir):
                files = sorted(os.listdir(contact_dir), reverse=True)
                for fn in files[:2]:
                    if fn.endswith(".md"):
                        try:
                            with open(os.path.join(contact_dir, fn), "r", encoding="utf-8") as f:
                                parts.append(f"[历史会话 {fn}]\n" + f.read()[:1000])
                        except Exception:
                            pass
                        break
        if self._api_enabled:
            # API 模式：读取画像
            try:
                url = f"{self.api_url}/vault/{self.folder}/微信联系人/{self._sanitize_filename(contact)}.md"
                headers = {"Authorization": f"Bearer {self.api_key}"}
                r = requests.get(url, headers=headers, timeout=5)
                if r.status_code == 200:
                    parts.append(f"[联系人画像 {contact}]\n" + r.text[:1500])
            except Exception:
                pass
        return "\n\n".join(parts) if parts else None

    # ------------------------------------------------------------------
    # 统一入口
    # ------------------------------------------------------------------
    def sync_message(self, msg_data):
        """同步单条消息。P0: 先进内存缓冲，按 (contact,date) 攒批后原子写出。"""
        if not self.enabled:
            return False
        # P2: 过滤规则
        if not self._pass_filter(msg_data):
            return False
        contact = msg_data.get("contact", "未命名")
        date_str = self._date_str(msg_data.get("timestamp", ""))
        key = (contact, date_str)
        with self._buffer_lock:
            self._buffer.setdefault(key, []).append(msg_data)
            if key not in self._buffer_first_ts:
                self._buffer_first_ts[key] = time.time()
            # 立即达到批量阈值则同步刷盘
            if len(self._buffer[key]) >= self.flush_batch:
                msgs = self._buffer.pop(key)
                self._buffer_first_ts.pop(key, None)
            else:
                msgs = None
        if msgs:
            try:
                self._write_buffered(key, msgs)
            except Exception as e:
                self._report_error(f"同步写入失败({key}): {e}")
                return False
        return True

    def _pass_filter(self, msg_data):
        """P2: 过滤规则。返回 True=同步，False=跳过。"""
        if self.filter_ads:
            ex = msg_data.get("extracted_fields") or msg_data.get("extracted") or {}
            if isinstance(ex, dict) and ex.get("is_ad"):
                return False
            content = (msg_data.get("content") or msg_data.get("raw_text", "")).lower()
            if any(k in content for k in ("点击领取", "优惠券", "拼多多", "限时折扣", "扫码关注")):
                return False
        if self.filter_only_tasks_or_urgent:
            is_task = bool(msg_data.get("extracted_tasks")) or any(
                "待办" in str(k) or "任务" in str(k) for k in (msg_data.get("keywords") or []))
            urgency = 1
            try:
                urgency = int((msg_data.get("extracted_fields") or {}).get("urgency", 1) or 1)
            except (ValueError, TypeError):
                pass
            if not (is_task or msg_data.get("is_important") or urgency >= 4):
                return False
        if self.filter_low_priority:
            try:
                urgency = int((msg_data.get("extracted_fields") or {}).get("urgency", 2) or 2)
            except (ValueError, TypeError):
                urgency = 2
            if urgency <= 1 and not msg_data.get("is_important"):
                return False
        return True

    def sync_batch(self, messages):
        if not self.enabled:
            return 0
        count = 0
        for msg in messages:
            if self.sync_message(msg):
                count += 1
        # 立即 flush（批量场景不需要等定时器）
        self.flush_all()
        logger.info(f"[Obsidian] 批量同步完成: {count}/{len(messages)} 条")
        return count

    def rebuild_vault(self, all_messages):
        """全量重建：按 (contact,date) 分组后逐组全量写出。"""
        if not self._file_enabled:
            logger.warning("[Obsidian] 文件模式未启用，无法重建")
            return False
        try:
            groups = {}
            for msg in all_messages:
                if not self._pass_filter(msg):
                    continue
                contact = msg.get("contact", "未命名")
                date_str = self._date_str(msg.get("timestamp", ""))
                groups.setdefault((contact, date_str), []).append(msg)
            for key, msgs in groups.items():
                self._write_buffered(key, msgs)
            logger.info(f"[Obsidian] vault重建完成: {len(groups)}个(联系人,日期)分组, {len(all_messages)}条消息")
            return True
        except Exception as e:
            self._report_error(f"vault重建失败: {e}")
            return False

    def delete_contact_note(self, contact):
        if not self._file_enabled or not contact:
            return False
        try:
            contact_dir = os.path.join(self.vault_path, self.folder, "联系人", self._sanitize_filename(contact))
            if os.path.isdir(contact_dir):
                import shutil
                shutil.rmtree(contact_dir)
                logger.info(f"[Obsidian] 已删除联系人笔记目录: {contact_dir}")
                return True
        except Exception as e:
            self._report_error(f"删除联系人笔记失败: {e}")
        return False

    def rebuild_contact_note(self, contact, messages):
        """根据剩余消息重建该联系人的全部日笔记（文件模式）。"""
        if not self._file_enabled or not contact:
            return False
        try:
            groups = {}
            for msg in messages:
                date_str = self._date_str(msg.get("timestamp", ""))
                groups.setdefault((contact, date_str), []).append(msg)
            if not groups:
                return self.delete_contact_note(contact)
            for key, msgs in groups.items():
                self._write_buffered(key, msgs)
            logger.info(f"[Obsidian] 已重建联系人笔记: {contact} ({len(groups)}天)")
            return True
        except Exception as e:
            self._report_error(f"重建联系人笔记失败: {e}")
        return False

    # ui_app.py 复用的兼容方法
    def _format_contact_note(self, contact_name, messages):
        """兼容旧接口：返回单联系人全部消息的 Markdown（按最新日期聚合）。"""
        if not messages:
            return ""
        date_str = self._date_str(messages[0].get("timestamp", ""))
        # 取最新一天作为代表；ui 导出用，直接复用日笔记格式
        return self._format_contact_daily_note(contact_name, date_str, messages)

    # ==================================================================
    # 方案B：Local REST API（PUT 全量 / PATCH 追加指定标题块）
    # ==================================================================
    def _api_write_buffered(self, contact, date_str, msgs):
        """API 模式：联系人日笔记 PATCH 追加到 ## 聊天记录；每日聚合嵌入链接。"""
        try:
            headers = {"Authorization": f"Bearer {self.api_key}",
                       "Content-Type": "text/markdown"}
            # 1. 联系人日笔记：PATCH 追加到 ## 聊天记录 块下
            contact_note = f"{self.folder}/联系人/{self._sanitize_filename(contact)}/{self._sanitize_filename(contact)}-{date_str}.md"
            block = "\n".join(self._format_message_block(m) for m in msgs)
            self._api_patch_append(contact_note,
                                   heading="## 💬 聊天记录",
                                   content=block,
                                   fm=self._build_frontmatter(contact, msgs,
                                                              "group" if any(bool(m.get("is_group")) for m in msgs) else "private"),
                                   default_title=f"{contact} · {date_str}")
            # 2. 每日聚合：PATCH 追加到 ## 今日会话
            daily_note = f"{self.folder}/每日/{date_str}.md"
            embed = (f"- 🕒 {datetime.now().strftime('%H:%M')} → "
                     f"[[{self.folder}/联系人/{self._sanitize_filename(contact)}/"
                     f"{self._sanitize_filename(contact)}-{date_str}|{contact} {date_str}]]")
            self._api_patch_append(daily_note,
                                   heading="## 💬 今日会话",
                                   content=embed,
                                   fm=(f"---\ndate: {date_str}\nsource: NOYA-Chat\n"
                                       f"tags: [微信, 每日笔记]\n---\n\n# 📅 {date_str} 微信消息"),
                                   default_title=f"📅 {date_str} 微信消息")
        except Exception as e:
            self._report_error(f"API写入失败({contact}/{date_str}): {e}")

    def _api_patch_append(self, filename, heading, content, fm, default_title):
        """P1: 通过 GET 读取 → 在指定 heading 下追加 content → PUT 写回。
        若文件不存在则用 fm + heading + content 创建。不破坏用户手动批注。"""
        url = f"{self.api_url}/vault/{filename}"
        headers = {"Authorization": f"Bearer {self.api_key}",
                   "Content-Type": "text/markdown"}
        try:
            resp = requests.get(url, headers=headers, timeout=5)
            if resp.status_code == 200:
                existing = resp.text or ""
                new_content = self._insert_under_heading(existing, heading, content)
                requests.put(url, headers=headers,
                             data=new_content.encode("utf-8"), timeout=10)
            elif resp.status_code == 404:
                new_content = (fm + f"\n\n# {default_title}\n\n{heading}\n\n{content}\n")
                requests.put(url, headers=headers,
                             data=new_content.encode("utf-8"), timeout=10)
        except requests.exceptions.ConnectionError:
            logger.warning("[Obsidian] REST API连接失败，请确保Obsidian已运行且Local REST API插件已启用")
        except Exception as e:
            logger.warning(f"[Obsidian] API PATCH失败({filename}): {e}")

    @staticmethod
    def _insert_under_heading(existing, heading, content):
        """在 existing 文本的 heading 标题下插入 content（不破坏其它块）。"""
        if heading not in existing:
            return existing.rstrip() + f"\n\n{heading}\n\n{content}\n"
        lines = existing.splitlines()
        out = []
        inserted = False
        for i, ln in enumerate(lines):
            out.append(ln)
            if ln.strip() == heading and not inserted:
                j = i + 1
                while j < len(lines) and lines[j].strip() == "":
                    out.append(lines[j])
                    j += 1
                out.append(content)
                inserted = True
        return "\n".join(out) + "\n"

    def _api_append(self, filename, content, headers):
        """兼容旧接口（重定向到 PATCH 追加 ## 聊天记录）"""
        self._api_patch_append(filename, "## 💬 聊天记录", content,
                               "---\ntags: [微信]\n---\n", "微信消息")

    def test_api_connection(self):
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
