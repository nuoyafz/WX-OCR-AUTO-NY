"""每日报告生成器"""
import os
import json
import sqlite3
from datetime import datetime, timedelta


def generate_daily_report(storage, date_str=None):
    """生成每日报告"""
    if date_str is None:
        date_str = datetime.now().strftime("%Y-%m-%d")

    if not storage or storage.storage_type != "sqlite":
        return None

    try:
        # 查询当天所有消息
        conn = sqlite3.connect(storage.db_path)
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            """SELECT * FROM messages
               WHERE timestamp LIKE ?
               ORDER BY timestamp ASC""",
            (f"{date_str}%",)
        )
        all_messages = [dict(row) for row in cur.fetchall()]
        conn.close()

        if not all_messages:
            return None

        # 解析JSON字段（数据库中以字符串存储）
        for msg in all_messages:
            kw = msg.get("matched_keywords")
            msg["matched_keywords"] = json.loads(kw) if kw else []
            la = msg.get("llm_analysis")
            try:
                msg["llm_analysis"] = json.loads(la) if la else {}
            except (json.JSONDecodeError, TypeError):
                msg["llm_analysis"] = {}

        # 按联系人分组
        contacts = {}
        important_msgs = []
        for msg in all_messages:
            contact = msg.get("contact", "未知")
            if contact not in contacts:
                contacts[contact] = []
            contacts[contact].append(msg)
            if msg.get("is_important"):
                important_msgs.append(msg)

        # 生成报告
        report = f"# 微信消息日报 - {date_str}\n\n"
        report += f"## 📊 统计\n"
        report += f"- 总消息: {len(all_messages)} 条\n"
        report += f"- 联系人: {len(contacts)} 个\n"
        report += f"- 重要消息: {len(important_msgs)} 条\n\n"

        if important_msgs:
            report += f"## 🔴 重要消息 ({len(important_msgs)}条)\n\n"
            for msg in important_msgs:
                contact = msg.get("contact", "")
                content = msg.get("raw_text", "")
                reason = msg.get("importance_reason", "")
                ts = msg.get("timestamp", "")
                time_part = ts.split(" ")[1] if " " in ts else ts
                llm = msg.get("llm_analysis", {})
                summary = ""
                if isinstance(llm, dict):
                    summary = llm.get("summary", "")
                report += f"### {contact} ({time_part})\n"
                report += f"> {content}\n"
                if reason:
                    report += f"- **原因**: {reason}\n"
                if summary:
                    report += f"- **摘要**: {summary}\n"
                report += "\n"

        report += f"## 💬 各联系人消息\n\n"
        for contact, msgs in sorted(contacts.items(), key=lambda x: -len(x[1])):
            report += f"### {contact} ({len(msgs)}条)\n\n"
            for msg in msgs:
                sender = "我" if msg.get("sender") == "me" else "对方"
                content = msg.get("raw_text", "")
                ts = msg.get("timestamp", "")
                time_part = ts.split(" ")[1] if " " in ts else ts
                llm = msg.get("llm_analysis", {})
                summary = ""
                if isinstance(llm, dict):
                    summary = llm.get("summary", "")
                report += f"- **{time_part}** {sender}: {content}"
                if summary:
                    report += f" _({summary})_"
                report += "\n"
            report += "\n"

        # 保存报告
        report_dir = "data/reports"
        os.makedirs(report_dir, exist_ok=True)
        report_path = os.path.join(report_dir, f"daily_{date_str}.md")
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(report)

        return report_path
    except Exception as e:
        return None


class ReportGenerator:
    """报告生成器（兼容UI调用）"""

    def __init__(self, storage, config=None):
        self.storage = storage
        self.config = config or {}
        self.report_dir = "data/reports"
        os.makedirs(self.report_dir, exist_ok=True)

    def generate_daily_report(self, date_str=None):
        """生成今日报告，返回报告字典"""
        from datetime import datetime
        if date_str is None:
            date_str = datetime.now().strftime("%Y-%m-%d")

        import sqlite3, json
        if not self.storage:
            return {"title": "无数据", "total_messages": 0, "total_contacts": 0, "total_important": 0, "contacts": {}, "important_messages": [], "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M")}

        try:
            conn = sqlite3.connect(self.storage.db_path)
            conn.row_factory = sqlite3.Row
            cur = conn.execute(
                "SELECT * FROM messages WHERE timestamp LIKE ? ORDER BY timestamp ASC",
                (f"{date_str}%",)
            )
            all_messages = [dict(row) for row in cur.fetchall()]
            conn.close()

            # 解析JSON字段
            for msg in all_messages:
                for field in ["llm_analysis", "matched_keywords", "keyword_categories", "regex_extracts"]:
                    if msg.get(field) and isinstance(msg[field], str):
                        try:
                            msg[field] = json.loads(msg[field])
                        except:
                            pass

            # 按联系人分组
            contacts = {}
            important_msgs = []
            for msg in all_messages:
                contact = msg.get("contact", "未知")
                if contact not in contacts:
                    contacts[contact] = []
                contacts[contact].append(msg)
                if msg.get("is_important"):
                    important_msgs.append(msg)

            return {
                "title": f"微信消息日报 - {date_str}",
                "date": date_str,
                "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
                "total_messages": len(all_messages),
                "total_contacts": len(contacts),
                "total_important": len(important_msgs),
                "contacts": contacts,
                "important_messages": important_msgs,
                "all_messages": all_messages,
            }
        except Exception as e:
            return {"title": f"报告生成失败: {e}", "total_messages": 0, "total_contacts": 0, "total_important": 0, "contacts": {}, "important_messages": [], "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M")}

    def export_html(self, report):
        """导出HTML格式报告"""
        from datetime import datetime
        date_str = report.get("date", datetime.now().strftime("%Y-%m-%d"))
        filepath = os.path.join(self.report_dir, f"daily_{date_str}.html")

        html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>{report['title']}</title>
<style>
body {{ font-family: 'Microsoft YaHei', sans-serif; margin: 20px; background: #f5f5f5; }}
.card {{ background: white; border-radius: 8px; padding: 15px; margin: 10px 0; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
.important {{ border-left: 4px solid #FF6B6B; }}
.contact {{ border-left: 4px solid #4ECDC4; }}
.msg {{ padding: 5px 0; border-bottom: 1px solid #eee; }}
.me {{ color: #2196F3; }} .other {{ color: #333; }}
.stat {{ display: inline-block; margin: 10px 20px; text-align: center; }}
.stat-num {{ font-size: 28px; font-weight: bold; color: #2196F3; }}
.stat-label {{ color: #666; }}
h1 {{ color: #333; }}
h2 {{ color: #2196F3; border-bottom: 2px solid #2196F3; padding-bottom: 5px; }}
</style></head><body>
<h1>{report['title']}</h1>
<p>生成时间: {report['generated_at']}</p>
<div class="card">
<div class="stat"><div class="stat-num">{report['total_messages']}</div><div class="stat-label">总消息</div></div>
<div class="stat"><div class="stat-num">{report['total_contacts']}</div><div class="stat-label">联系人</div></div>
<div class="stat"><div class="stat-num">{report['total_important']}</div><div class="stat-label">重要消息</div></div>
</div>
"""
        if report.get("important_messages"):
            html += "<h2>🔴 重要消息</h2>"
            for msg in report["important_messages"]:
                contact = msg.get("contact", "")
                content = msg.get("raw_text", "")
                reason = msg.get("importance_reason", "")
                ts = msg.get("timestamp", "")
                html += f'<div class="card important"><strong>{contact}</strong> ({ts})<br>{content}'
                if reason:
                    html += f'<br><em style="color:#FF6B6B">📌 {reason}</em>'
                html += '</div>'

        html += "<h2>💬 各联系人消息</h2>"
        for contact, msgs in report.get("contacts", {}).items():
            html += f'<div class="card contact"><h3>{contact} ({len(msgs)}条)</h3>'
            for msg in msgs:
                sender = "我" if msg.get("sender") == "me" else "对方"
                content = msg.get("raw_text", "")
                ts = msg.get("timestamp", "")
                time_part = ts.split(" ")[1] if " " in ts else ts
                html += f'<div class="msg"><span class="{"me" if sender=="我" else "other"}">{time_part} {sender}</span>: {content}</div>'
            html += '</div>'

        html += "</body></html>"

        with open(filepath, "w", encoding="utf-8") as f:
            f.write(html)
        return filepath

    def export_text(self, report):
        """导出文本格式报告"""
        from datetime import datetime
        date_str = report.get("date", datetime.now().strftime("%Y-%m-%d"))
        filepath = os.path.join(self.report_dir, f"daily_{date_str}.txt")

        text = f"{report['title']}\n生成时间: {report['generated_at']}\n\n"
        text += f"统计: {report['total_messages']}条消息, {report['total_contacts']}个联系人, {report['total_important']}条重要\n\n"

        if report.get("important_messages"):
            text += "=== 重要消息 ===\n"
            for msg in report["important_messages"]:
                text += f"[{msg.get('contact','')}] {msg.get('raw_text','')}\n"
                if msg.get("importance_reason"):
                    text += f"  原因: {msg['importance_reason']}\n"
            text += "\n"

        text += "=== 各联系人消息 ===\n"
        for contact, msgs in report.get("contacts", {}).items():
            text += f"\n--- {contact} ({len(msgs)}条) ---\n"
            for msg in msgs:
                sender = "我" if msg.get("sender") == "me" else "对方"
                text += f"  {msg.get('timestamp','')} {sender}: {msg.get('raw_text','')}\n"

        with open(filepath, "w", encoding="utf-8") as f:
            f.write(text)
        return filepath

    def generate_weekly_report(self):
        """生成本周报告"""
        from datetime import datetime, timedelta
        end_date = datetime.now()
        start_date = end_date - timedelta(days=7)

        all_reports = []
        for i in range(7):
            d = start_date + timedelta(days=i)
            date_str = d.strftime("%Y-%m-%d")
            report = self.generate_daily_report(date_str)
            if report.get("total_messages", 0) > 0:
                all_reports.append(report)

        total_msgs = sum(r["total_messages"] for r in all_reports)
        total_important = sum(r["total_important"] for r in all_reports)
        all_contacts = set()
        for r in all_reports:
            all_contacts.update(r.get("contacts", {}).keys())

        return {
            "title": f"微信消息周报 ({start_date.strftime('%m-%d')} ~ {end_date.strftime('%m-%d')})",
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "total_messages": total_msgs,
            "total_contacts": len(all_contacts),
            "total_important": total_important,
            "daily_reports": all_reports,
            "contacts": {},
            "important_messages": [],
        }
