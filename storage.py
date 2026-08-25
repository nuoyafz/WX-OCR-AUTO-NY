"""
数据存储模块 — SQLite按联系人存储 + JSON/CSV导出
=================================================
- 每条消息按联系人存储到SQLite数据库
- 支持JSON和CSV格式导出
- 提供查询接口（按联系人/时间/关键词/重要性筛选）
"""
import os
import json
import csv
import sqlite3
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


def _app_base():
    """返回项目根目录（与 ui_app.py 的 APP_BASE 对齐），用于把相对数据路径绝对化。

    根治「运行写入 A 库、重启读 B 库」：无论进程 CWD 在哪、无论哪个
    MessageStorage 实例（engine 的 / ui 自建缓存的），只要传的是相对路径
    （data/messages.db），都统一解析到「脚本/可执行文件所在目录」下的同一文件。
    """
    import sys
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def _resolve(path):
    """相对路径基于项目根绝对化；绝对路径原样返回。"""
    if not path:
        return path
    if os.path.isabs(path):
        return path
    return os.path.join(_app_base(), path)


# -----------------------------
# 安全工具函数：彻底解决 dict join 报错
# -----------------------------
def _safe_str(value):
    """把任意类型值转换为可写入CSV的安全字符串。
    防止: sequence item 0: expected str instance, dict found
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, (list, tuple)):
        try:
            return "[" + ", ".join(_safe_str(v) for v in value) + "]"
        except Exception:
            return str(value)
    if isinstance(value, dict):
        try:
            return json.dumps(value, ensure_ascii=False)
        except Exception:
            return str(value)
    try:
        return str(value)
    except Exception:
        return ""


def _safe_join(separator, iterable):
    """安全join任何可迭代对象：先每个元素_safe_str()再拼接。"""
    if not iterable:
        return ""
    try:
        return separator.join(_safe_str(item) for item in iterable)
    except Exception as e:
        logger.warning(f"_safe_join 回退到str(): {e}")
        return str(iterable)


def _safe_action_items_str(action_items):
    """action_items可能是list[str]或list[dict]或混合，统一转成可读字符串"""
    if not action_items:
        return ""
    items = []
    for ai in action_items:
        if isinstance(ai, dict):
            # 如果是结构化待办，拼出可读文本
            parts = []
            if "content" in ai:
                parts.append(str(ai["content"]))
            elif "text" in ai:
                parts.append(str(ai["text"]))
            elif "title" in ai:
                parts.append(str(ai["title"]))
            else:
                # 兜底：用json
                parts.append(json.dumps(ai, ensure_ascii=False))
            for extra_key in ("due", "deadline", "priority", "owner"):
                if ai.get(extra_key):
                    parts.append(f"{extra_key}={ai[extra_key]}")
            items.append("; ".join(parts))
        else:
            items.append(_safe_str(ai))
    return ", ".join(items)


class MessageStorage:
    """消息存储管理器"""

    def __init__(self, storage_config):
        self.config = storage_config or {}
        self.storage_type = self.config.get("type", "sqlite")
        self.db_path = _resolve(self.config.get("db_path", "data/messages.db"))
        self.json_path = _resolve(self.config.get("json_path", "data/messages.json"))
        self.csv_path = _resolve(self.config.get("csv_path", "data/messages.csv"))

        # 确保数据目录存在
        db_dir = os.path.dirname(self.db_path)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)

        if self.storage_type == "sqlite":
            self._init_db()

    def _init_db(self):
        """初始化SQLite数据库"""
        try:
            conn = sqlite3.connect(self.db_path)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    contact TEXT NOT NULL,
                    sender TEXT NOT NULL,
                    raw_text TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    matched_keywords TEXT,
                    keyword_categories TEXT,
                    regex_extracts TEXT,
                    is_important INTEGER DEFAULT 0,
                    importance_reason TEXT,
                    llm_analysis TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_contact ON messages(contact)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_timestamp ON messages(timestamp)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_important ON messages(is_important)
            """)
            conn.commit()
            conn.close()
            logger.info(f"数据库初始化完成: {self.db_path}")
        except Exception as e:
            logger.error(f"数据库初始化失败: {e}")

    def save(self, extraction_result):
        """保存一条提取结果"""
        if self.storage_type == "sqlite":
            self._save_sqlite(extraction_result)
        elif self.storage_type == "json":
            self._save_json(extraction_result)
        elif self.storage_type == "csv":
            self._save_csv(extraction_result)

    def _save_sqlite(self, result):
        """保存到SQLite（带内容级去重，防止重复入库）"""
        try:
            contact = result.get("contact", "")
            sender = result.get("sender", "other")
            raw_text = result.get("raw_text", "")
            if not raw_text.strip():
                return

            # 进程内去重键：contact|sender|raw_text（同源重复秒挡，不查db）
            dedup_key = f"{contact}|{sender}|{raw_text.strip()}"
            if not hasattr(self, "_seen_keys"):
                self._seen_keys = set()
            if dedup_key in self._seen_keys:
                return
            # db 兜底去重：跨重启/多入口重复也能挡（同一联系人同内容当天已有则跳过）
            conn = sqlite3.connect(self.db_path)
            try:
                _existing = conn.execute(
                    "SELECT 1 FROM messages WHERE contact=? AND sender=? AND raw_text=? "
                    "AND date(timestamp)=date('now','localtime') LIMIT 1",
                    (contact, sender, raw_text.strip()),
                ).fetchone()
                if _existing:
                    self._seen_keys.add(dedup_key)
                    return
                conn.execute("""
                    INSERT INTO messages (
                        contact, sender, raw_text, timestamp,
                        matched_keywords, keyword_categories, regex_extracts,
                        is_important, importance_reason, llm_analysis
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    contact,
                    sender,
                    raw_text,
                    result.get("timestamp", ""),
                    json.dumps(result.get("matched_keywords", []), ensure_ascii=False),
                    json.dumps(result.get("keyword_categories", []), ensure_ascii=False),
                    json.dumps(result.get("regex_extracts", {}), ensure_ascii=False),
                    1 if result.get("is_important") else 0,
                    result.get("importance_reason", ""),
                    json.dumps(result.get("llm_analysis", {}), ensure_ascii=False),
                ))
                conn.commit()
                self._seen_keys.add(dedup_key)
                # 限制内存集合大小，避免长期运行膨胀
                if len(self._seen_keys) > 5000:
                    self._seen_keys = set(list(self._seen_keys)[-2500:])
            finally:
                conn.close()
        except Exception as e:
            logger.error(f"保存到SQLite失败: {e}")

    def _save_json(self, result):
        """保存到JSON文件（追加模式）"""
        data = []
        if os.path.exists(self.json_path):
            try:
                with open(self.json_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except (json.JSONDecodeError, IOError):
                data = []
        data.append(result)
        json_dir = os.path.dirname(self.json_path)
        if json_dir:
            os.makedirs(json_dir, exist_ok=True)
        with open(self.json_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def _save_csv(self, result):
        """保存到CSV文件（追加模式）—— 使用安全join"""
        csv_dir = os.path.dirname(self.csv_path)
        if csv_dir:
            os.makedirs(csv_dir, exist_ok=True)
        file_exists = os.path.exists(self.csv_path)
        with open(self.csv_path, "a", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            if not file_exists:
                writer.writerow([
                    "contact", "sender", "raw_text", "timestamp",
                    "matched_keywords", "keyword_categories", "regex_extracts",
                    "is_important", "importance_reason", "llm_analysis"
                ])
            writer.writerow([
                _safe_str(result.get("contact", "")),
                _safe_str(result.get("sender", "")),
                _safe_str(result.get("raw_text", "")),
                _safe_str(result.get("timestamp", "")),
                _safe_join("|", result.get("matched_keywords", [])),
                _safe_join("|", result.get("keyword_categories", [])),
                json.dumps(result.get("regex_extracts", {}), ensure_ascii=False),
                "是" if result.get("is_important") else "否",
                _safe_str(result.get("importance_reason", "")),
                json.dumps(result.get("llm_analysis", {}), ensure_ascii=False),
            ])

    def query(self, contact=None, keyword=None, important_only=False,
              start_time=None, end_time=None, limit=100):
        """查询消息"""
        if self.storage_type != "sqlite":
            logger.warning("查询功能仅支持SQLite模式")
            return []

        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            sql = "SELECT * FROM messages WHERE 1=1"
            params = []

            if contact:
                sql += " AND contact LIKE ?"
                params.append(f"%{contact}%")
            if keyword:
                sql += " AND raw_text LIKE ?"
                params.append(f"%{keyword}%")
            if important_only:
                sql += " AND is_important = 1"
            if start_time:
                sql += " AND timestamp >= ?"
                params.append(start_time)
            if end_time:
                sql += " AND timestamp <= ?"
                params.append(end_time)

            sql += " ORDER BY timestamp DESC LIMIT ?"
            params.append(limit)

            rows = conn.execute(sql, params).fetchall()
            conn.close()

            results = []
            for row in rows:
                kw = row["matched_keywords"]
                kc = row["keyword_categories"]
                re_row = row["regex_extracts"]
                la = row["llm_analysis"]
                results.append({
                    "id": row["id"],
                    "contact": row["contact"],
                    "sender": row["sender"],
                    "raw_text": row["raw_text"],
                    "timestamp": row["timestamp"],
                    "matched_keywords": json.loads(kw) if kw else [],
                    "keyword_categories": json.loads(kc) if kc else [],
                    "regex_extracts": json.loads(re_row) if re_row else {},
                    "is_important": bool(row["is_important"]),
                    "importance_reason": row["importance_reason"] or "",
                    "llm_analysis": json.loads(la) if la else {},
                })
            return results
        except Exception as e:
            logger.error(f"查询失败: {e}")
            import traceback
            logger.error(traceback.format_exc()[-200:])
            return []

    def get_contacts(self):
        """获取所有联系人列表"""
        if self.storage_type != "sqlite":
            return []
        try:
            conn = sqlite3.connect(self.db_path)
            rows = conn.execute(
                "SELECT DISTINCT contact FROM messages ORDER BY contact"
            ).fetchall()
            conn.close()
            return [row[0] for row in rows]
        except Exception:
            return []

    def delete_message(self, msg_id=None, contact=None, raw_text=None,
                       timestamp=None):
        """
        删除单条消息（SQLite）。
        优先按 id 删除；未提供 id 时按 (contact, raw_text, timestamp) 组合删除。
        返回删除的行数。
        """
        if self.storage_type != "sqlite":
            return 0
        try:
            conn = sqlite3.connect(self.db_path)
            if msg_id is not None:
                cur = conn.execute("DELETE FROM messages WHERE id = ?", (msg_id,))
            elif contact and raw_text:
                cur = conn.execute(
                    "DELETE FROM messages WHERE contact = ? AND raw_text = ? "
                    "AND (? IS NULL OR timestamp = ?)",
                    (contact, raw_text, timestamp, timestamp))
            else:
                conn.close()
                return 0
            conn.commit()
            n = cur.rowcount
            conn.close()
            return n
        except Exception as e:
            logger.error(f"删除消息失败: {e}")
            return 0

    def delete_contact(self, contact):
        """删除某个联系人的全部消息，返回删除条数。"""
        if self.storage_type != "sqlite":
            return 0
        try:
            conn = sqlite3.connect(self.db_path)
            cur = conn.execute(
                "DELETE FROM messages WHERE contact = ?", (contact,))
            conn.commit()
            n = cur.rowcount
            conn.close()
            return n
        except Exception as e:
            logger.error(f"删除联系人记录失败: {e}")
            return 0

    def clear_all(self):
        """清空全部消息记录（SQLite），返回删除条数。"""
        if self.storage_type != "sqlite":
            return 0
        try:
            conn = sqlite3.connect(self.db_path)
            cur = conn.execute("DELETE FROM messages")
            conn.commit()
            n = cur.rowcount
            conn.close()
            return n
        except Exception as e:
            logger.error(f"清空记录失败: {e}")
            return 0

    def delete_orphan_contacts(self):
        """删除“伪联系人”记录：空联系人名、或日期/时间分隔符被误当联系人名
        （如 '' / '昨天11' / '今天2' / '星期三'）。返回删除条数。"""
        if self.storage_type != "sqlite":
            return 0
        try:
            conn = sqlite3.connect(self.db_path)
            cur = conn.execute(
                "DELETE FROM messages WHERE contact IS NULL OR contact = '' "
                "OR contact LIKE '昨天%' OR contact LIKE '今天%' "
                "OR contact LIKE '明天%' OR contact LIKE '前天%' "
                "OR contact LIKE '星期%' OR contact LIKE '周%' "
                "OR contact GLOB '[0-9]*' "
                "OR contact GLOB '[0-9][0-9]:[0-9][0-9]'"
            )
            conn.commit()
            n = cur.rowcount
            conn.close()
            return n
        except Exception as e:
            logger.error(f"删除伪联系人记录失败: {e}")
            return 0

    def search(self, keyword, limit=50):
        """搜索消息"""
        if self.storage_type != "sqlite":
            return []
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cur = conn.execute(
                """SELECT * FROM messages
                   WHERE raw_text LIKE ? OR contact LIKE ?
                   ORDER BY timestamp DESC LIMIT ?""",
                (f"%{keyword}%", f"%{keyword}%", limit)
            )
            rows = cur.fetchall()
            conn.close()
            return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"搜索失败: {e}")
            return []

    def get_stats(self):
        """获取统计信息（修复版：增强容错和多维度统计）"""
        if self.storage_type != "sqlite":
            logger.warning("查询功能仅支持SQLite模式")
            return {}

        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row

            # 基础统计
            total = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
            important = conn.execute("SELECT COUNT(*) FROM messages WHERE is_important=1").fetchone()[0]
            contacts = conn.execute("SELECT COUNT(DISTINCT contact) FROM messages").fetchone()[0]

            # 今日统计
            from datetime import datetime
            today = datetime.now().strftime("%Y-%m-%d")
            today_count = conn.execute(
                "SELECT COUNT(*) FROM messages WHERE timestamp LIKE ?",
                (f"{today}%",)
            ).fetchone()[0]

            # 本周统计
            from datetime import timedelta
            week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
            week_count = conn.execute(
                "SELECT COUNT(*) FROM messages WHERE timestamp >= ?",
                (week_ago,)
            ).fetchone()[0]

            # 发送者统计
            sender_stats = conn.execute("""
                SELECT sender, COUNT(*) as count
                FROM messages
                GROUP BY sender
            """).fetchall()
            sender_counts = {row["sender"]: row["count"] for row in sender_stats}

            conn.close()

            stats = {
                "total_messages": total,
                "important_messages": important,
                "total_contacts": contacts,
                "today_messages": today_count,
                "week_messages": week_count,
                "sender_distribution": sender_counts,
                "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }

            logger.info(f"[统计] 计算完成 - 总: {total}, 联系人: {contacts}, 今日: {today_count}")
            return stats

        except Exception as e:
            logger.error(f"[统计] 计算失败: {e}")
            import traceback
            logger.error(traceback.format_exc()[-200:])
            # 返回默认值防止UI崩溃
            return {
                "total_messages": 0,
                "important_messages": 0,
                "total_contacts": 0,
                "today_messages": 0,
                "week_messages": 0,
                "sender_distribution": {},
                "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "error": str(e)
            }

    # ------------------------------------------------------------------
    # 按联系人分组导出 CSV （所有join均使用安全版，杜绝dict导致的崩溃）
    # ------------------------------------------------------------------
    def export_csv(self, filepath=None):
        """导出全部数据为CSV（按联系人分组，对话合并格式）"""
        filepath = filepath or self.csv_path
        csv_dir = os.path.dirname(filepath)
        if csv_dir:
            os.makedirs(csv_dir, exist_ok=True)

        all_data = self.query(limit=999999)

        # 按联系人分组
        contacts = {}
        for row in all_data:
            name = row["contact"] or "（未命名联系人）"
            if name not in contacts:
                contacts[name] = []
            contacts[name].append(row)

        try:
            with open(filepath, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f)
                # 汇总信息
                writer.writerow(["========== 联系人消息汇总 =========="])
                writer.writerow([f"导出时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"])
                writer.writerow([f"总联系人: {len(contacts)}  总消息数: {len(all_data)}"])
                writer.writerow([])

                for contact_name in sorted(contacts.keys()):
                    rows = contacts[contact_name]
                    important_count = sum(1 for m in rows if m["is_important"])
                    writer.writerow([f"========== {contact_name} (消息数:{len(rows)}, 重要:{important_count}) =========="])

                    # 按时间排序消息
                    rows.sort(key=lambda x: x.get("timestamp", ""))

                    # 写入消息（对话格式）
                    current_date = ""
                    for row in rows:
                        ts = row.get("timestamp", "")
                        date_part = ts.split(" ")[0] if " " in ts else ts

                        # 日期分隔
                        if date_part != current_date:
                            current_date = date_part
                            f.write(f"\n--- {date_part} ---\n")

                        sender = "我" if row.get("sender") == "me" else contact_name
                        content = row.get("raw_text", "")
                        important = "★" if row.get("is_important") else ""

                        # 对话格式：时间 发送者: 内容 [重要标记]
                        time_part = ts.split(" ")[1] if " " in ts else ""
                        keywords = _safe_join(" ", row.get("matched_keywords", []))
                        summary = ""
                        llm = row.get("llm_analysis", {})
                        if isinstance(llm, dict):
                            summary = llm.get("summary", "")

                        line = f"{time_part}\t{sender}: {content}"
                        if important:
                            line += f" [★{row.get('importance_reason', '')}]"
                        if keywords:
                            line += f" [关键词: {keywords}]"
                        if summary:
                            line += f" [摘要: {summary}]"
                        f.write(line + "\n")
                    writer.writerow([])  # 空行分隔

            logger.info(f"已导出CSV（按联系人分组）: {filepath} ({len(all_data)} 条, {len(contacts)} 个联系人)")
            return True
        except Exception as e:
            logger.error(f"导出CSV失败: {e}", exc_info=True)
            import traceback
            logger.error(traceback.format_exc()[-400:])
            raise

    def export_json(self, filepath=None):
        """导出全部数据为JSON（按联系人分组）"""
        filepath = filepath or self.json_path
        json_dir = os.path.dirname(filepath)
        if json_dir:
            os.makedirs(json_dir, exist_ok=True)
        all_data = self.query(limit=999999)

        # 按联系人分组
        contacts = {}
        for row in all_data:
            name = row["contact"] or "（未命名联系人）"
            if name not in contacts:
                contacts[name] = {
                    "contact": name,
                    "message_count": 0,
                    "important_count": 0,
                    "messages": [],
                }
            contacts[name]["messages"].append({
                "sender": "我" if row["sender"] == "me" else "对方",
                "text": row["raw_text"],
                "timestamp": row["timestamp"],
                "keywords": row["matched_keywords"],
                "regex_extracts": row["regex_extracts"],
                "is_important": row["is_important"],
                "importance_reason": row["importance_reason"],
                "llm_analysis": row["llm_analysis"],
            })
            contacts[name]["message_count"] += 1
            if row["is_important"]:
                contacts[name]["important_count"] += 1

        export_data = {
            "export_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "total_contacts": len(contacts),
            "total_messages": len(all_data),
            "contacts": list(contacts.values()),
        }

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(export_data, f, ensure_ascii=False, indent=2)
        logger.info(f"已导出JSON（按联系人分组）: {filepath} ({len(all_data)} 条, {len(contacts)} 个联系人)")

    def delete_older_than(self, date_str):
        """删除指定日期之前的消息，返回删除条数"""
        if self.storage_type != "sqlite":
            return 0
        try:
            conn = sqlite3.connect(self.db_path)
            cur = conn.execute(
                "DELETE FROM messages WHERE timestamp < ?", (date_str,))
            deleted = cur.rowcount
            conn.commit()
            conn.close()
            if deleted:
                logger.info(f"[清理] 删除了 {deleted} 条旧消息 (早于 {date_str})")
            return deleted
        except Exception as e:
            logger.warning(f"[清理] 删除旧消息失败: {e}")
            return 0

    def vacuum(self):
        """压缩数据库文件，回收已删除记录的空间"""
        if self.storage_type != "sqlite":
            return
        try:
            conn = sqlite3.connect(self.db_path)
            conn.execute("VACUUM")
            conn.close()
            logger.info("[清理] 数据库 VACUUM 完成")
        except Exception as e:
            logger.warning(f"[清理] VACUUM 失败: {e}")