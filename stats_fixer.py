"""
数据统计修复模块 - 修复数据统计失效问题
============================================
修复内容：
1. 统计数据实时更新机制
2. 统计查询失败容错处理
3. 统计缓存优化
4. 多维度统计扩展
5. 统计可视化增强
"""
import sqlite3
import json
import logging
import threading
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from collections import defaultdict

logger = logging.getLogger(__name__)


class EnhancedStatistics:
    """增强版统计系统"""

    def __init__(self, storage):
        self.storage = storage
        self.stats_cache = {}
        self.cache_lock = threading.Lock()
        self.last_update = None
        self.cache_ttl = 30  # 缓存有效期30秒
        self.listeners = []  # 统计更新监听器

    def invalidate_cache(self):
        """使缓存失效"""
        with self.cache_lock:
            self.stats_cache = {}
            self.last_update = None
            logger.debug("[统计] 缓存已失效")

    def add_listener(self, callback):
        """添加统计更新监听器"""
        if callback not in self.listeners:
            self.listeners.append(callback)
            logger.debug(f"[统计] 添加监听器，当前数量: {len(self.listeners)}")

    def notify_listeners(self, stats: Dict):
        """通知所有监听器"""
        for listener in self.listeners:
            try:
                listener(stats)
            except Exception as e:
                logger.error(f"[统计] 监听器回调失败: {e}")

    def get_cached_stats(self, force_refresh: bool = False) -> Dict:
        """获取缓存的统计数据"""
        now = datetime.now()

        with self.cache_lock:
            # 检查缓存是否有效
            if (not force_refresh and
                self.last_update and
                (now - self.last_update).total_seconds() < self.cache_ttl and
                self.stats_cache):
                logger.debug("[统计] 使用缓存数据")
                return dict(self.stats_cache)

        # 重新计算统计
        try:
            stats = self.calculate_comprehensive_stats()

            with self.cache_lock:
                self.stats_cache = stats
                self.last_update = now

            # 通知监听器
            self.notify_listeners(stats)

            return stats

        except Exception as e:
            logger.error(f"[统计] 计算统计失败: {e}")
            # 返回缓存数据（如果有）
            with self.cache_lock:
                return dict(self.stats_cache) if self.stats_cache else {}

    def calculate_comprehensive_stats(self) -> Dict:
        """计算综合统计数据"""
        if self.storage.storage_type != "sqlite":
            logger.warning("[统计] 仅支持SQLite模式")
            return {}

        try:
            conn = sqlite3.connect(self.storage.db_path)
            conn.row_factory = sqlite3.Row

            # 基础统计
            total = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
            important = conn.execute("SELECT COUNT(*) FROM messages WHERE is_important=1").fetchone()[0]
            contacts = conn.execute("SELECT COUNT(DISTINCT contact) FROM messages").fetchone()[0]

            # 今日统计
            today = datetime.now().strftime("%Y-%m-%d")
            today_count = conn.execute(
                "SELECT COUNT(*) FROM messages WHERE timestamp LIKE ?",
                (f"{today}%",)
            ).fetchone()[0]

            # 本周统计
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

            # 按联系人统计（Top 10）
            contact_stats = conn.execute("""
                SELECT contact, COUNT(*) as count
                FROM messages
                GROUP BY contact
                ORDER BY count DESC
                LIMIT 10
            """).fetchall()

            top_contacts = [
                {"contact": row["contact"], "count": row["count"]}
                for row in contact_stats
            ]

            # 时间分布统计（最近24小时，按小时）
            time_stats = self._calculate_time_distribution(conn)

            # 关键词统计
            keyword_stats = self._calculate_keyword_stats(conn)

            # 情绪统计
            sentiment_stats = self._calculate_sentiment_stats(conn)

            # 分类统计
            category_stats = self._calculate_category_stats(conn)

            conn.close()

            stats = {
                "total_messages": total,
                "important_messages": important,
                "total_contacts": contacts,
                "today_messages": today_count,
                "week_messages": week_count,
                "sender_distribution": sender_counts,
                "top_contacts": top_contacts,
                "time_distribution": time_stats,
                "keyword_stats": keyword_stats,
                "sentiment_stats": sentiment_stats,
                "category_stats": category_stats,
                "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }

            logger.info(f"[统计] 计算完成 - 总消息: {total}, 联系人: {contacts}, 今日: {today_count}")
            return stats

        except Exception as e:
            logger.error(f"[统计] 计算失败: {e}")
            import traceback
            logger.error(traceback.format_exc()[-200:])
            return {}

    def _calculate_time_distribution(self, conn) -> Dict[str, int]:
        """计算时间分布（最近24小时，按小时）"""
        try:
            now = datetime.now()
            time_dist = defaultdict(int)

            for i in range(24):
                hour_start = (now - timedelta(hours=i)).replace(minute=0, second=0, microsecond=0)
                hour_end = hour_start + timedelta(hours=1)

                count = conn.execute(
                    "SELECT COUNT(*) FROM messages WHERE timestamp >= ? AND timestamp < ?",
                    (hour_start.strftime("%Y-%m-%d %H:%M:%S"), hour_end.strftime("%Y-%m-%d %H:%M:%S"))
                ).fetchone()[0]

                time_dist[hour_start.strftime("%H:00")] = count

            return dict(time_dist)
        except Exception as e:
            logger.error(f"[统计] 时间分布计算失败: {e}")
            return {}

    def _calculate_keyword_stats(self, conn) -> Dict[str, int]:
        """计算关键词统计"""
        try:
            # 从matched_keywords字段提取
            rows = conn.execute(
                "SELECT matched_keywords FROM messages WHERE matched_keywords IS NOT NULL AND matched_keywords != ''"
            ).fetchall()

            keyword_counts = defaultdict(int)

            for row in rows:
                try:
                    keywords = json.loads(row["matched_keywords"])
                    if isinstance(keywords, list):
                        for kw in keywords:
                            if kw:
                                keyword_counts[kw] += 1
                except json.JSONDecodeError:
                    continue

            # 返回Top 20关键词
            sorted_keywords = sorted(keyword_counts.items(), key=lambda x: x[1], reverse=True)[:20]
            return dict(sorted_keywords)

        except Exception as e:
            logger.error(f"[统计] 关键词统计失败: {e}")
            return {}

    def _calculate_sentiment_stats(self, conn) -> Dict[str, int]:
        """计算情绪统计"""
        try:
            sentiment_counts = defaultdict(int)

            rows = conn.execute(
                "SELECT llm_analysis FROM messages WHERE llm_analysis IS NOT NULL AND llm_analysis != ''"
            ).fetchall()

            for row in rows:
                try:
                    analysis = json.loads(row["llm_analysis"])
                    sentiment = analysis.get("sentiment", "neutral")
                    if sentiment:
                        sentiment_counts[sentiment] += 1
                except json.JSONDecodeError:
                    continue

            return dict(sentiment_counts)

        except Exception as e:
            logger.error(f"[统计] 情绪统计失败: {e}")
            return {}

    def _calculate_category_stats(self, conn) -> Dict[str, int]:
        """计算分类统计"""
        try:
            category_counts = defaultdict(int)

            rows = conn.execute(
                "SELECT llm_analysis FROM messages WHERE llm_analysis IS NOT NULL AND llm_analysis != ''"
            ).fetchall()

            for row in rows:
                try:
                    analysis = json.loads(row["llm_analysis"])
                    category = analysis.get("category", "其他")
                    if category:
                        category_counts[category] += 1
                except json.JSONDecodeError:
                    continue

            return dict(category_counts)

        except Exception as e:
            logger.error(f"[统计] 分类统计失败: {e}")
            return {}

    def get_contact_timeline(self, contact: str, days: int = 7) -> List[Dict]:
        """获取指定联系人的时间线"""
        if self.storage.storage_type != "sqlite":
            return []

        try:
            conn = sqlite3.connect(self.storage.db_path)
            conn.row_factory = sqlite3.Row

            start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

            rows = conn.execute("""
                SELECT timestamp, raw_text, sender, is_important
                FROM messages
                WHERE contact LIKE ? AND timestamp >= ?
                ORDER BY timestamp ASC
                LIMIT 100
            """, (f"%{contact}%", start_date)).fetchall()

            conn.close()

            timeline = []
            for row in rows:
                timeline.append({
                    "timestamp": row["timestamp"],
                    "content": row["raw_text"],
                    "sender": row["sender"],
                    "is_important": bool(row["is_important"])
                })

            return timeline

        except Exception as e:
            logger.error(f"[统计] 联系人时间线失败: {e}")
            return []

    def get_trending_keywords(self, hours: int = 24, top_n: int = 10) -> List[Dict]:
        """获取热门关键词"""
        if self.storage.storage_type != "sqlite":
            return []

        try:
            conn = sqlite3.connect(self.storage.db_path)

            start_time = (datetime.now() - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")

            rows = conn.execute(
                "SELECT matched_keywords FROM messages WHERE timestamp >= ? AND matched_keywords IS NOT NULL",
                (start_time,)
            ).fetchall()

            keyword_counts = defaultdict(int)

            for row in rows:
                try:
                    keywords = json.loads(row["matched_keywords"])
                    if isinstance(keywords, list):
                        for kw in keywords:
                            if kw:
                                keyword_counts[kw] += 1
                except json.JSONDecodeError:
                    continue

            sorted_keywords = sorted(keyword_counts.items(), key=lambda x: x[1], reverse=True)[:top_n]

            conn.close()

            return [{"keyword": kw, "count": count} for kw, count in sorted_keywords]

        except Exception as e:
            logger.error(f"[统计] 热门关键词失败: {e}")
            return []


# ============================================================
# UI 统计面板增强
# ============================================================

def create_enhanced_stats_panel(parent, stats_system):
    """创建增强版统计面板"""
    import customtkinter as ctk

    stats_frame = ctk.CTkFrame(parent)
    stats_frame.pack(fill="both", expand=True, padx=10, pady=10)

    # 标题
    ctk.CTkLabel(
        stats_frame,
        text="📊 实时数据统计",
        font=ctk.CTkFont(size=16, weight="bold")
    ).pack(pady=10)

    # 基础统计卡片容器
    basic_frame = ctk.CTkFrame(stats_frame)
    basic_frame.pack(fill="x", padx=10, pady=5)

    # 创建基础统计卡片
    basic_stats = [
        ("总消息数", "total_messages", "#07C160"),
        ("重要消息", "important_messages", "#FA5151"),
        ("联系人数", "total_contacts", "#1485EE"),
        ("今日消息", "today_messages", "#FF8A00"),
        ("本周消息", "week_messages", "#95EC69"),
    ]

    basic_labels = {}
    for i, (label_text, key, color) in enumerate(basic_stats):
        card = ctk.CTkFrame(basic_frame, fg_color=color, corner_radius=8)
        card.grid(row=i // 5, column=i % 5, padx=5, pady=5, sticky="nsew")

        ctk.CTkLabel(
            card,
            text=label_text,
            font=ctk.CTkFont(size=10),
            text_color="white"
        ).pack(pady=(5, 0))

        value_label = ctk.CTkLabel(
            card,
            text="0",
            font=ctk.CTkFont(size=18, weight="bold"),
            text_color="white"
        ).pack(pady=(0, 5))

        basic_labels[key] = value_label

    # 详细统计区域
    detail_frame = ctk.CTkFrame(stats_frame)
    detail_frame.pack(fill="both", expand=True, padx=10, pady=10)

    # 创建标签页
    tabview = ctk.CTkTabview(detail_frame)
    tabview.pack(fill="both", expand=True)

    # 发送者分布标签页
    sender_tab = tabview.add("发送者分布")
    create_sender_chart(sender_tab, stats_system)

    # 热门关键词标签页
    keyword_tab = tabview.add("热门关键词")
    create_keyword_chart(keyword_tab, stats_system)

    # 情绪分析标签页
    sentiment_tab = tabview.add("情绪分析")
    create_sentiment_chart(sentiment_tab, stats_system)

    # 刷新按钮
    refresh_btn = ctk.CTkButton(
        stats_frame,
        text="🔄 刷新统计",
        command=lambda: update_stats(stats_system, basic_labels)
    )
    refresh_btn.pack(pady=10)

    return stats_frame


def create_sender_chart(parent, stats_system):
    """创建发送者分布图表"""
    import customtkinter as ctk

    frame = ctk.CTkFrame(parent)
    frame.pack(fill="both", expand=True, padx=10, pady=10)

    ctk.CTkLabel(
        frame,
        text="消息发送者分布",
        font=ctk.CTkFont(size=14, weight="bold")
    ).pack(pady=10)

    # 这里可以添加图表显示逻辑
    # 暂时用文本显示
    text_label = ctk.CTkLabel(
        frame,
        text="点击刷新按钮获取最新数据",
        text_color="gray"
    )
    text_label.pack(pady=20)


def create_keyword_chart(parent, stats_system):
    """创建关键词统计图表"""
    import customtkinter as ctk

    frame = ctk.CTkFrame(parent)
    frame.pack(fill="both", expand=True, padx=10, pady=10)

    ctk.CTkLabel(
        frame,
        text="热门关键词统计",
        font=ctk.CTkFont(size=14, weight="bold")
    ).pack(pady=10)

    text_label = ctk.CTkLabel(
        frame,
        text="点击刷新按钮获取最新数据",
        text_color="gray"
    )
    text_label.pack(pady=20)


def create_sentiment_chart(parent, stats_system):
    """创建情绪分析图表"""
    import customtkinter as ctk

    frame = ctk.CTkFrame(parent)
    frame.pack(fill="both", expand=True, padx=10, pady=10)

    ctk.CTkLabel(
        frame,
        text="消息情绪分析",
        font=ctk.CTkFont(size=14, weight="bold")
    ).pack(pady=10)

    text_label = ctk.CTkLabel(
        frame,
        text="点击刷新按钮获取最新数据",
        text_color="gray"
    )
    text_label.pack(pady=20)


def update_stats(stats_system, labels):
    """更新统计数据显示"""
    try:
        stats = stats_system.get_cached_stats(force_refresh=True)

        # 更新基础统计
        for key, label in labels.items():
            value = stats.get(key, 0)
            label.configure(text=str(value))

        # 更新详细统计（这里可以扩展更多图表更新逻辑）

    except Exception as e:
        logger.error(f"[统计] 更新失败: {e}")


# ============================================================
# 集成到存储模块
# ============================================================

def patch_storage_with_stats(storage):
    """为存储模块添加增强统计功能"""
    if not hasattr(storage, 'enhanced_stats'):
        storage.enhanced_stats = EnhancedStatistics(storage)

        # 拦截保存操作，自动失效缓存
        original_save = storage.save

        def save_with_cache_invalidation(result):
            original_save(result)
            storage.enhanced_stats.invalidate_cache()

        storage.save = save_with_cache_invalidation
        logger.info("[统计] 已集成增强统计到存储模块")

    return storage.enhanced_stats