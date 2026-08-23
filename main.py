import ctypes
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass
"""
NOYA Chat 微信助手 — 核心引擎
========================
整合截图+OCR+信息提取+自动回复，可被UI调用或命令行运行。
自动回复默认关闭，可通过config或UI开启。
"""
import sys
import io
import time
import signal
import os
import random
import hashlib
import json
import re
import logging
import argparse
import threading
from datetime import datetime
from pathlib import Path
import win32con

from window_manager import (
    find_wechat_window, setup_window_guide, focus_window, is_window_visible,
    get_contact_name, analyze_chat_context,
)
from screenshot import capture_chat_bottom, is_image_blank
from ocr_engine import recognize, identify_senders, recognize_with_group_enhance, identify_senders_v4
from message_parser import MessageParser
from role_manager import RoleManager
from llm_client import LLMClient
from extractor import InfoExtractor
from smart_monitor import SmartMonitor
from storage import MessageStorage
from contact_scanner import ContactScanner
from sender import send_text
from ai_trainer import AITrainer
from obsidian_sync import ObsidianSync

# 便携模式基准目录：打包后为 exe 同目录（可写），开发态为 __file__ 目录
if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
    APP_BASE = os.path.dirname(os.path.abspath(sys.executable))
else:
    APP_BASE = os.path.dirname(os.path.abspath(__file__))


def app_path(*parts):
    """拼可写路径，始终落在 APP_BASE（exe 同目录 / 项目根）。"""
    return os.path.join(APP_BASE, *parts)


logger = logging.getLogger(__name__)


class WeChatEngine:
    """微信AI助手核心引擎，可在后台线程中运行"""

    def __init__(self, config_path="config.yaml", callbacks=None):
        """
        Args:
            config_path: 配置文件路径
            callbacks: 回调函数字典
                on_log: function(level, message) - 日志回调
                on_extract: function(result) - 提取结果回调
                on_reply: function(contact, reply) - 回复发送回调
                on_stats: function(stats) - 统计更新回调
                on_status: function(status) - 状态变更回调
        """
        self.config_path = config_path
        self.callbacks = callbacks or {}
        self._on_new_message = callbacks.get("on_new_message") if callbacks else None  # 新消息实时回调
        self._on_capture_cb = callbacks.get("on_capture") if callbacks else None  # 截图预览回调
        self._running = False
        self._thread = None
        self._stop_flag = threading.Event()

        self.role_manager = RoleManager(config_path)
        self.llm_config = self.role_manager.get_llm_config()
        self.wechat_config = self.role_manager.get_wechat_config()
        self.auto_reply_config = self.role_manager.config.get("auto_reply", {})
        self.extraction_config = self.role_manager.config.get("extraction", {})
        self.storage_config = self.role_manager.config.get("storage", {})
        self.scanner_config = self.role_manager.config.get("contact_scanner", {})

        self.auto_reply_enabled = self.auto_reply_config.get("enabled", False)

        # P1 聚合回复：同一联系人多条消息累积后一次性综合回复（而非识别一条回一条）
        self._reply_agg_cfg = self.auto_reply_config.get("aggregate", {})
        self._reply_agg_enabled = self._reply_agg_cfg.get("enabled", False)
        self._reply_agg_max_wait = float(self._reply_agg_cfg.get("max_wait", 8.0))   # 超时强制flush(秒)
        self._reply_agg_max_msgs = int(self._reply_agg_cfg.get("max_msgs", 3))       # 累积条数触发flush
        self._reply_buffer = {}      # contact -> [{"content","sender","ts"}, ...]
        self._reply_buffer_lock = threading.Lock()
        self._reply_agg_stop = threading.Event()
        self._reply_agg_thread = None
        if self._reply_agg_enabled:
            self._start_reply_aggregator()

        self.parser = None
        self.llm_client = None
        self.extractor = None
        self.storage = None
        self.scanner = None
        self.red_dot_monitor = None
        self.ai_trainer = None
        # 增量检测引擎
        smart_cfg = self.wechat_config.get("smart_monitor", {})
        self.smart_monitor = SmartMonitor(smart_cfg) if smart_cfg.get("enabled", True) else None
        if self.smart_monitor:
            logger.info("增量检测引擎已启用（4层优化：帧间差异+区域裁剪+pHash去重+底部检测）")

        # Obsidian同步（V4：传入写入失败回调，供UI弹窗告警）
        obsidian_cfg = self.role_manager.config.get("obsidian", {})
        self.obsidian = ObsidianSync(obsidian_cfg, on_write_error=self._on_obsidian_error,
                                      llm_client=getattr(self, "llm_client", None))
        if self.obsidian.enabled:
            self._log("info", "[Obsidian] 同步已启用")

        # ===== P0: 绑定 UI 上下文取数回调 (由 UI 在创建引擎后注入) =====
        self._fetch_context_fn = None
        self._context_turns = 5

        self._ui = None
        self.window = None
        self._last_contact_name = ""   # 最近一次成功解析到的有效联系人名（瞬时OCR失败兜底）
        self._last_red_dot_check = 0

        # 最小化监控模式：offscreen=屏幕外保活(推荐), minimized=闪现截图, normal=前台
        self.minimize_mode = self.wechat_config.get("minimize_mode", "offscreen")
        # 快速模式属性
        self.fast_mode = self.wechat_config.get("fast_mode", True)
        self._last_window_title = ""
        self._last_title_check = 0

        # 勿扰模式属性
        self.dnd_enabled = self.wechat_config.get("do_not_disturb", True)
        self._last_keyboard_time = 0
        self._last_mouse_pos = (0, 0)
        self._mouse_still_count = 0
        # 红点路径跨轮去重：{contact: set(md5(sender|content))}，防止冷却后重扫重复上报历史消息
        # 持久化到 data/reddot_seen.json，重启不丢失
        self._reddot_seen_path = app_path("data", "reddot_seen.json")
        self._reddot_msg_seen = self._load_reddot_seen()

        self.stats = {
            "frames_captured": 0,
            "ocr_calls": 0,
            "messages_detected": 0,
            "replies_sent": 0,
            "extracted": 0,
            "important": 0,
            "start_time": datetime.now(),
        }

    def _log(self, level, message):
        """发送日志到回调"""
        getattr(logger, level.lower(), logger.info)(message)
        if self.callbacks.get("on_log"):
            try:
                self.callbacks["on_log"](level, message)
            except Exception as e:
                logger.warning("[UI回调] on_log失败: %s", e)

    def _on_extract(self, result):
        if self.callbacks.get("on_extract"):
            try:
                self.callbacks["on_extract"](result)
            except Exception as e:
                logger.warning("[UI回调] on_extract失败: %s", e)

    def _on_reply(self, contact, reply):
        if self.callbacks.get("on_reply"):
            try:
                self.callbacks["on_reply"](contact, reply)
            except Exception as e:
                logger.warning("[UI回调] on_reply失败: %s", e)

    def _on_stats(self):
        if self.callbacks.get("on_stats"):
            try:
                self.callbacks["on_stats"](dict(self.stats))
            except Exception as e:
                logger.warning("[UI回调] on_stats失败: %s", e)

    def _on_status(self, status):
        if self.callbacks.get("on_status"):
            try:
                self.callbacks["on_status"](status)
            except Exception as e:
                logger.warning("[UI回调] on_status失败: %s", e)

    def _on_capture(self, image):
        """截图预览回调：把截图发送到UI预览窗口"""
        cb = getattr(self, "_on_capture_cb", None) or self.callbacks.get("on_capture")
        if cb:
            try:
                cb(image)
            except Exception as e:
                logger.warning("[预览] 回调失败: %s", e)

    def _on_new_message_cb(self, message):
        """新消息回调：发送到UI实时消息面板"""
        if self._on_new_message:
            try:
                self._on_new_message(message)
            except Exception as e:
                logger.warning("[UI回调] on_new_message失败: %s", e)

    def _on_obsidian_error(self, msg):
        """V4: Obsidian 写入失败回调 —— 转发给 UI 弹窗告警（不静默吞掉）。
        同一错误 60s 内只报一次，避免 WinError 5 等反复刷屏。"""
        import time as _t
        _now = _t.time()
        _last = getattr(self, "_obs_err_last", {})
        if _last.get(msg, 0) + 60.0 > _now:
            return
        _last[msg] = _now
        self._log("error", f"[Obsidian] {msg}")
        cb = getattr(self, "_on_obsidian_error_cb", None)
        if cb:
            try:
                cb(msg)
            except Exception:
                pass

    def _send_ui_card(self, contact, sender, content, timestamp,
                      extracted=None, is_important=False,
                      importance_reason="", msg_key=None, is_update=False,
                      is_group=False, group_member=None,
                      confidence=None, sender_confidence=None):
        """
        统一发送富信息消息卡片（附带 keywords/summary/category 供UI彩色展示）。
        - 首次发送：立即发基础卡（快速反馈）
        - 提取完成后：用同一 msg_key 发更新卡，UI 原位刷新，避免重复卡片
        - V3 新增：is_group / group_member / confidence / sender_confidence
        """
        if not self._on_new_message:
            return

        # 空联系人名保护：红点切换/切回瞬间窗口名未解析到时，
        # 跳过本轮卡片，避免落库 contact="" 幽灵会话
        if not contact or not str(contact).strip():
            return

        if msg_key is None:
            try:
                import hashlib
                # V3: 去重key包含群成员（同群不同成员发相同内容不是重复）
                key_src = f"{contact}|{group_member or ''}|{sender}|{content}|{timestamp}"
                msg_key = hashlib.md5(key_src.encode("utf-8")).hexdigest()[:14]
            except Exception:
                msg_key = f"{contact}_{timestamp}"

        payload = {
            "contact": contact,
            "sender": sender,
            "content": content,
            "timestamp": timestamp,
            "is_important": bool(is_important),
            "importance_reason": importance_reason or "",
            "msg_key": msg_key,
            "is_update": bool(is_update),
            # V3 新增字段
            "is_group": bool(is_group),
            "group_member": group_member,
            "confidence": confidence,
            "sender_confidence": sender_confidence,
        }

        # 富信息：提取结果中的关键词/摘要/分类/正则信息
        if extracted:
            payload["keywords"] = extracted.get("matched_keywords", []) or []
            payload["categories"] = extracted.get("keyword_categories", []) or []
            payload["regex_extracts"] = extracted.get("regex_extracts", {}) or {}
            payload["extracted_fields"] = extracted.get("extracted_fields") or extracted.get("fields") or {}
            llm_analysis = extracted.get("llm_analysis") or {}
            if isinstance(llm_analysis, dict):
                payload["summary"] = llm_analysis.get("summary", "") or ""
                payload["category"] = llm_analysis.get("category", "") or ""
                payload["urgency"] = llm_analysis.get("urgency", 0) or 0
                payload["sentiment"] = llm_analysis.get("sentiment", "") or ""
                action_items = llm_analysis.get("action_items") or []
                if isinstance(action_items, list):
                    payload["action_items"] = action_items[:5]
            if not payload.get("summary") and not payload.get("keywords") and not payload.get("categories"):
                cats = payload["categories"]
                if cats:
                    payload["category"] = "、".join(cats)
            # 补充提取器注入的群聊字段（万一上游没赋值）
            payload.setdefault("chat_kind", extracted.get("chat_kind"))
            # 分类优先级规则结果（classification / priority）
            payload["classification"] = extracted.get("classification", "") or ""
            payload["priority"] = extracted.get("priority", 0) or 0

        # 情感分析 + 紧急度分级（本地规则引擎，不依赖 LLM）
        if self._sentiment_analyzer is not None:
            try:
                sa = self._sentiment_analyzer.analyze(content, sender=sender)
                payload["sentiment"] = payload.get("sentiment") or sa.get("sentiment", "")
                payload["urgency"] = payload.get("urgency") or sa.get("urgency", 0)
                payload["is_urgent"] = sa.get("is_urgent", False)
                payload["urgency_reason"] = sa.get("urgency_reason", "")
                payload["sentiment_method"] = sa.get("method", "rule")
            except Exception:
                pass

        try:
            self._on_new_message(payload)
        except Exception as e:
            logger.warning(f"[UI卡片] 发送失败: {e}")

    def initialize(self):
        """初始化各模块（在启动前调用）— 分步加载，逐步输出日志，避免用户感知卡顿"""
        self._log("info", "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        self._log("info", "开始初始化系统模块...")
        self._log("info", "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

        # 步骤1：消息解析器
        self._log("info", "[1/6] 正在初始化消息解析器...")
        stable_frames = self.wechat_config.get("stable_frames", 1)
        context_size = self.wechat_config.get("context_size", 20)
        self.parser = MessageParser(stable_frames=stable_frames, context_size=context_size)
        self._log("info", f"  ✔ 消息解析器就绪 (稳定帧={stable_frames}, 上下文={context_size})")

        # 步骤2：LLM客户端
        self._log("info", "[2/6] 正在初始化LLM客户端...")
        api_key = self.llm_config.get("api_key", "")
        if api_key and api_key not in ("", "your-api-key-here"):
            self.llm_client = LLMClient(
                self.llm_config,
                style_preset=self.role_manager.config.get("reply_style_preset") or {},
            )
            self._log("info", f"  ✔ LLM客户端就绪: {self.llm_config.get('model', '?')}")
            # 把 LLM 客户端注入 Obsidian（初始化阶段它还没建，这里补注入以启用 AI 赋能）
            if getattr(self, "obsidian", None) is not None:
                self.obsidian.llm_client = self.llm_client
                adv = (self.role_manager.config.get("obsidian", {}) or {}).get("advanced", {}) or {}
                self.obsidian.ai_enabled = True
                self.obsidian.ai_summary = adv.get("ai_summary", True)
                self.obsidian.ai_profile = adv.get("ai_profile", True)
                self.obsidian.ai_relationship = adv.get("ai_relationship", True)
                self.obsidian.ai_relationship_depth = int(adv.get("ai_relationship_depth", 60))
                self._log("info", "  ✔ Obsidian AI赋能已注入（摘要/画像/关系图谱）")
        else:
            self._log("info", "  ✔ 未配置API Key，跳过LLM客户端")

        # 步骤3：信息提取引擎
        self._log("info", "[3/6] 正在初始化信息提取引擎...")
        self.extractor = InfoExtractor(self.extraction_config, self.llm_config)
        self._log("info", "  ✔ 信息提取引擎就绪")

        # 步骤4：数据存储
        self._log("info", "[4/6] 正在初始化数据存储...")
        self.storage = MessageStorage(self.storage_config)
        self._log("info", f"  ✔ 数据存储就绪: {self.storage_config.get('type', 'sqlite')}")

        # 步骤5：联系人扫描器 + 红点监控
        self._log("info", "[5/6] 正在初始化联系人监控...")
        if self.scanner_config.get("enabled"):
            import window_manager as wm_module
            self.scanner = ContactScanner(self.scanner_config, wm_module)
            self._log("info", "  ✔ 多联系人扫描器已启用")
        else:
            self._log("info", "  ✔ 多联系人扫描器未启用")

        red_dot_config = self.role_manager.config.get("red_dot_monitor", {})
        if red_dot_config.get("enabled"):
            from red_dot_monitor import RedDotMonitor
            self.red_dot_monitor = RedDotMonitor(red_dot_config)
            # 注入黑名单（含通配符*）：让红点诊断与待处理计数排除公众号/文件传输助手等常驻红点
            _bl = self.role_manager.config.get("contacts_filter", {}).get("blacklist", [])
            self.red_dot_monitor.blacklist = list(_bl)
            self._log("info", "  ✔ 红点监控器已启用（自动检测未读红点）")
        else:
            self._log("info", "  ✔ 红点监控器未启用")

        # 步骤6：AI训练引擎
        self._log("info", "[6/6] 正在初始化AI训练引擎...")
        ai_threshold = self.wechat_config.get("ai_training_threshold", 10)
        self.ai_trainer = AITrainer(self.llm_config, training_threshold=ai_threshold)
        progress = self.ai_trainer.get_progress()
        self._log("info", f"  ✔ AI训练引擎就绪 (学习进度 {progress['current']}/{ai_threshold})")

        # 步骤7：RAG 本地知识库检索（向量嵌入 + 历史检索增强回复）
        self._rag_retriever = None
        rag_cfg = self.role_manager.config.get("rag", {})
        if rag_cfg.get("enabled", True):
            try:
                from rag_retriever import get_rag_retriever
                self._rag_retriever = get_rag_retriever(rag_cfg)
                if self._rag_retriever.enabled:
                    # 注入到 LLM 客户端
                    if self.llm_client is not None:
                        self.llm_client.set_rag_retriever(self._rag_retriever)
                    self._log("info", "  ✔ RAG 本地知识库检索已启用")
                else:
                    self._log("info", "  ✔ RAG 检索已跳过（依赖未安装或禁用）")
            except Exception as e:
                self._log("info", f"  ✔ RAG 初始化跳过: {e}")

        # 步骤8：情感分析 + 紧急度分级
        self._sentiment_analyzer = None
        sent_cfg = self.role_manager.config.get("sentiment", {})
        if sent_cfg.get("enabled", True):
            try:
                from sentiment_analyzer import get_sentiment_analyzer
                self._sentiment_analyzer = get_sentiment_analyzer(sent_cfg)
                self._log("info", "  ✔ 情感分析+紧急度分级已启用")
            except Exception as e:
                self._log("info", f"  ✔ 情感分析初始化跳过: {e}")

        # 步骤9：Windows UI Automation 无渲染未读监控（最小化/屏幕外均可用）
        self._uia_monitor = None
        uia_cfg = self.role_manager.config.get("uia_monitor", {})
        if uia_cfg.get("enabled", True):
            try:
                from uia_monitor import get_uia_monitor
                self._uia_monitor = get_uia_monitor(uia_cfg)
                if self._uia_monitor.enabled:
                    self._log("info", "  ✔ UIA 无渲染未读监控已启用（最小化/屏幕外均可用）")
                else:
                    self._log("info", "  ✔ UIA 监控跳过（uiautomation 未安装）")
            except Exception as e:
                self._log("info", f"  ✔ UIA 初始化跳过: {e}")

        # 步骤10：自适应点击优化器（像素验证 + 智能偏移 + 直方图变化检测）
        self._click_optimizer = None
        click_opt_cfg = self.role_manager.config.get("click_optimizer", {})
        if click_opt_cfg.get("enabled", True):
            try:
                from click_optimizer import get_click_optimizer
                self._click_optimizer = get_click_optimizer(click_opt_cfg)
                self._log("info", "  ✔ 自适应点击优化器已启用（像素验证+智能偏移）")
            except Exception as e:
                self._log("info", f"  ✔ 点击优化器初始化跳过: {e}")

        self._log("info", "===== 全部模块初始化完成，准备开始监控 =====")

        # 根据微信状态自适应
        if self.minimize_mode == "offscreen" and self.window is not None:
            try:
                from window_manager import is_window_minimized, move_window_offscreen
                if is_window_minimized(self.window):
                    if move_window_offscreen(self.window):
                        self._log("info", "[屏幕外] 微信已最小化 -> 移到屏幕外后台监控")
                    else:
                        self._log("warning", "[屏幕外] 移出屏幕失败")
                else:
                    self._log("info", "[监控] 微信在桌面，保持原位监控")
            except Exception as e:
                self._log("warning", f"[屏幕外] 初始化移屏异常: {e}")

        return True

    def update_classification(self, categories):
        """UI 分类规则编辑后，实时更新 extractor 的分类优先级（无需重启）"""
        if self.extractor and hasattr(self.extractor, "set_classification"):
            self.extractor.set_classification(categories)
            self._log("info", f"[分类] 已更新分类规则，共 {len(categories)} 类")

    def _physical_click(self, click_x, click_y):
        """物理鼠标点击（可见模式专用，带延时模拟真实点击）"""
        import win32api
        win32api.SetCursorPos((int(click_x), int(click_y)))
        time.sleep(0.1)
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
        time.sleep(0.05)
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)

    # 工具自身窗口标题/状态栏特征，OCR 误把工具自己当成微信时会匹配到这些 → 必须挡在库外
    _SELF_TITLE_MARKERS = ("NOYA", "微信AI助手", "微信 AI 助手", "助手 v2.0", "AI 助手 v2.0")
    _STATUS_NOISE_RE = re.compile(
        r"(消息|提取|重要|回复|OCR)\s*[:：]\s*\d|引擎已初始化|已初始化：|"
        r"NOYA|微信AI助手|核心引擎|监控预览|识别当前"
    )
    # 存储兜底：即便上游 red_dot 匹配漏过，contact 看起来像消息预览/垃圾时拒存
    # 防止 "[草稿] 哈哈，这么!" / "[18条]南阳本地宝：" 这种垃圾被当成联系人名存进库
    # ★ 注意：不能用裸长度>20 判断预览 —— 微信长联系人名/群名（如
    #   "Hero 学长(求职规划报名中)10～23 点"）完全合法，会被误杀导致存储丢失。
    #   只在「名里混进消息体特征」时才判为预览：方括号[N条]/[草稿]、含消息体标点、
    #   或 含冒号分隔的"标题：正文"结构（预览常把最新一条消息正文拼进名字）。
    _PREVIEW_PUNCT_RE = re.compile(r'[。！!？?,，;；、…：][^"]{2,}|：[^\s]')  # 名里带句末标点或冒号接正文
    _PREVIEW_KEYWORDS = ("草稿", "撤回", "拍了拍", "收到红包", "添加了", "邀请你", "新消息")
    _PREVIEW_BRACKET_RE = re.compile(r'^\[[\d条]+]|\[草稿\]|\[N条\]')  # 方括号未读条数/草稿标记

    # 微信系统提示/非对话消息标记（出现在聊天区、不应作为真实对话消息存储/回复）。
    # 常见于：通话状态、群操作、撤回、拍一拍等。三处(new_messages/flush/红点)统一引用。
    SYSTEM_MSG_MARKERS = (
        "以下是新消息", "以上是打招呼", "收到红包", "撤回了一条消息", "拍了拍",
        "添加了", "邀请你", "你已退出群聊", "你被", "已在其它设备接听",
        "对方正在输入", "你邀请", "你撤回了", "你已成为", "你通过", "对方已",
        "该消息已撤回", "通话时长", "语音通话", "视频通话", "本次通话",
    )

    @classmethod
    def _looks_like_preview(cls, name):
        if not name:
            return False
        n = name.strip()
        # 仅当名字本身带着「未读条数/草稿」方括号标记才算预览（不再用裸长度误杀）
        if cls._PREVIEW_BRACKET_RE.search(n):
            return True
        if cls._PREVIEW_PUNCT_RE.search(n):
            return True
        for kw in cls._PREVIEW_KEYWORDS:
            if n.startswith(kw) or f"[{kw}]" in n:
                return True
        return False

    @classmethod
    def _strip_timestamp_prefix(cls, text):
        """剥离微信消息正文里被 OCR 混进来的时间戳前缀。

        微信每行消息左侧有时间戳列（如 '9:57' '20:08' '星期三07:50' '7/18'），
        OCR 常把相邻行的时间戳错并入正文，导致 '9:57\\n20:08'、'0:08\\n有点大' 这类脏文本。
        剥离每行的 HH:MM / M/D HH:MM / 星期X HH:MM 前缀，保留干净正文给 LLM。
        """
        import re as _re
        if not text:
            return text
        _lines = text.split("\n")
        _cleaned = []
        for _ln in _lines:
            _ln = _ln.strip()
            # 移除开头的：纯时间戳（9:57）、M/D+时间（7/18 9:57）、星期X+时间（星期三07:50）
            _ln = _re.sub(r'^\s*(星期[一二三四五六日天]|周[一二三四五六日天])?\s*\d{1,2}[/:]\d{1,2}\s*',
                          '', _ln)
            _ln = _re.sub(r'^\s*\d{1,2}[/:]\d{1,2}\s*', '', _ln)
            _ln = _re.sub(r'^\s*\d{1,2}[/:]\d{1,2}\s*', '', _ln)  # 二次（M/D HH:MM 两截）
            # 整行若是纯时间戳/空/纯符号 → 丢弃（这是 OCR 把时间戳列错当正文，非真实消息）
            if not _ln:
                continue
            if _re.match(r'^[\d\s/:：（）()?]+$', _ln):
                continue
            _cleaned.append(_ln)
        return "\n".join(_cleaned).strip()

    def _store_extracted(self, extracted):
        """
        统一的存储入口（替代散落的 `if self.storage: self.storage.save(extracted)`）：
        1. 过滤工具自身窗口被误识别为微信的垃圾（contact 是工具标题/状态栏文字）
        2. 过滤工具状态栏/日志噪声文本
        3. 空联系人兜底为“未命名会话”，避免真实消息丢失在空 contact 下
        """
        if not self.storage:
            return
        contact = (extracted.get("contact") or "").strip()
        text = (extracted.get("raw_text") or "").strip()
        # 4. 剥离 OCR 混进正文的时间戳前缀（9:57 / 星期三07:50 / 7/18 等）
        if text:
            _clean = self._strip_timestamp_prefix(text)
            if _clean != text:
                extracted = dict(extracted)
                extracted["raw_text"] = _clean
                text = _clean
        # 1. 工具自身窗口
        for m in self._SELF_TITLE_MARKERS:
            if m in contact:
                self._log("debug", f"[存储] 跳过工具自身窗口误识别: contact='{contact}'")
                return
        # 2. 状态栏/日志噪声
        if self._STATUS_NOISE_RE.search(text):
            self._log("debug", f"[存储] 跳过状态栏噪声: {text[:30]!r}")
            return
        # 3. 空联系人兜底
        if not contact:
            try:
                from window_manager import get_contact_name
                _name = get_contact_name(self.window)
                if _name and _name not in ("微信", ""):
                    contact = _name
            except Exception:
                pass
            if not contact:
                contact = "未命名会话"
            extracted = dict(extracted)
            extracted["contact"] = contact
        # 兜底：contact 看起来像消息预览 → 拒存，避免"[草稿] 哈哈，这么!" / "[18条]南阳本地宝：" 这类垃圾联系人
        if self._looks_like_preview(contact):
            self._log("warning", f"[存储] 拒绝疑似预览的 contact: '{contact}'")
            return
        self.storage.save(extracted)

        # RAG 索引：将消息加入向量库（异步，不阻塞主流程）
        if self._rag_retriever is not None:
            try:
                self._rag_retriever.index_message(
                    contact=contact,
                    sender=extracted.get("sender", "other"),
                    content=text,
                    timestamp=extracted.get("timestamp", ""),
                    is_important=extracted.get("is_important", False),
                    keywords=extracted.get("matched_keywords"),
                )
            except Exception:
                pass

    def _maybe_auto_reply(self, contact, content, sender, context=None, is_group=False):
        """
        自动回复调度入口（异步线程执行，不阻塞监控主循环）：
        LLM生成+发送耗时较长，若同步执行会阻塞截图/OCR/新消息检测，
        导致监控实时性下降。改为后台线程执行，互不阻塞。

        P0 防重发：轮询路径(1840)与红点路径(2436)都会调用本方法。
        同一(contact, content)可能被两条路径各识别一次 → 双发。
        这里在同步入口做指纹去重（线程spawn之前），确保同一对方消息只回一次。
        """
        import threading
        if not getattr(self, '_auto_reply_lock', None):
            self._auto_reply_lock = threading.Lock()
        if not getattr(self, '_reply_fingerprints', None):
            self._reply_fingerprints = {}

        # ★ 指纹去重：同一联系人+同一对方消息内容 → 只回复一次
        _norm = (str(content or "")).strip()
        if contact and _norm:
            import hashlib
            _fp = hashlib.md5(f"{contact}|{_norm}".encode("utf-8")).hexdigest()
            _fp_set = self._reply_fingerprints.setdefault(contact, set())
            if _fp in _fp_set:
                self._log("debug", f"[自动回复] 指纹去重跳过(已回复过): {contact}: {_norm[:30]}")
                return
            # 先占位（防止同条消息并发双路径都通过校验），发送成功后保留，失败则移除
            _fp_set.add(_fp)
            if len(_fp_set) > 200:
                self._reply_fingerprints[contact] = set(list(_fp_set)[-100:])

        if not self._auto_reply_lock.acquire(blocking=False):
            self._log("debug", "[自动回复] 上一条回复仍在生成/发送中，跳过本条")
            return

        # ★ 聚合回复模式：把消息塞进 buffer，由聚合线程统一 flush（避免识别一条回一条）
        if self._reply_agg_enabled:
            with self._reply_buffer_lock:
                buf = self._reply_buffer.setdefault(contact, [])
                buf.append({"content": content, "sender": sender,
                            "ts": time.time(), "is_group": is_group})
                _cnt = len(buf)
            self._log("info", f"[聚合回复] {contact} 缓冲第 {_cnt} 条 (max_msgs={self._reply_agg_max_msgs}, "
                               f"max_wait={self._reply_agg_max_wait}s)")
            self._auto_reply_lock.release()
            return

        # 把群聊标记塞进 context，供 _auto_reply_impl 安全护栏读取
        if is_group:
            if context is None:
                context = {}
            if isinstance(context, dict):
                context = dict(context)
                context["__is_group__"] = True
        # V4 P1: 读取 Obsidian 知识库作为 AI 回复上下文（个人记忆）
        if self.obsidian and self.obsidian.enabled and self.obsidian.enable_read:
            try:
                kb_ctx = self.obsidian.read_contact_context(contact)
                if kb_ctx:
                    if context is None:
                        context = {}
                    if isinstance(context, dict):
                        context = dict(context)
                        context["__obsidian_kb__"] = kb_ctx
            except Exception as _e:
                self._log("debug", f"[Obsidian] 读取知识库失败: {_e}")
        _t = threading.Thread(target=self._run_auto_reply_async,
                              args=(contact, content, sender, context),
                              daemon=True, name="auto-reply")
        _t.start()
        # V3.4 屏障：红点循环需在"切回原窗口"前 join 这些线程，
        # 否则回复粘贴时窗口已被切回原联系人 → 回复粘贴错人/识别乱。
        try:
            if not hasattr(self, "_pending_reply_threads"):
                self._pending_reply_threads = []
            self._pending_reply_threads.append(_t)
        except Exception:
            pass

    def _run_auto_reply_async(self, contact, content, sender, context=None):
        try:
            self._auto_reply_impl(contact, content, sender, context)
        except Exception as e:
            self._log("error", f"[自动回复] 线程异常: {e}")
        finally:
            self._auto_reply_lock.release()

    # ================ 聚合回复：按会话累积多条后一次性综合回复 ================
    def _start_reply_aggregator(self):
        """后台线程：定期检查各联系人 buffer，超时或满条数则 flush 一次综合回复"""
        if self._reply_agg_thread and self._reply_agg_thread.is_alive():
            return
        self._reply_agg_stop.clear()
        self._reply_agg_thread = threading.Thread(
            target=self._reply_agg_loop, daemon=True, name="reply-aggregator")
        self._reply_agg_thread.start()
        self._log("info", f"[聚合回复] 已启动 (max_wait={self._reply_agg_max_wait}s, "
                          f"max_msgs={self._reply_agg_max_msgs})")

    def _stop_reply_aggregator(self):
        self._reply_agg_stop.set()

    def _reply_agg_loop(self):
        while not self._reply_agg_stop.is_set():
            try:
                with self._reply_buffer_lock:
                    due = []
                    for contact, buf in self._reply_buffer.items():
                        if not buf:
                            continue
                        _elapsed = time.time() - buf[0]["ts"]
                        if len(buf) >= self._reply_agg_max_msgs or _elapsed >= self._reply_agg_max_wait:
                            due.append(contact)
                for contact in due:
                    self._flush_reply_buffer(contact)
            except Exception as e:
                self._log("error", f"[聚合回复] 循环异常: {e}")
            self._reply_agg_stop.wait(timeout=1.0)

    def _flush_reply_buffer(self, contact):
        """把某联系人缓冲的多条消息合并成一次综合回复（而非逐条回复）。"""
        with self._reply_buffer_lock:
            buf = self._reply_buffer.get(contact)
            if not buf:
                return
            # 取出对方消息（自己的消息不计入回复触发，但可当上下文）
            _others = [m for m in buf if m.get("sender") != "me"]
            if not _others:
                self._reply_buffer[contact] = []
                return
            _is_group = any(m.get("is_group") for m in buf)
            # 合并上下文：按顺序拼成"对方1: ... 对方2: ..."供 LLM 综合理解
            _ctx_msgs = []
            for m in buf:
                _side = "我" if m.get("sender") in ("me", "self", "mine") else "对方"
                _c = str(m.get("content", "")).strip()
                if _c:
                    _ctx_msgs.append(f"{_side}：{_c}")
            # 清空 buffer（已取走），指纹由 _auto_reply_impl 内部按单条去重保证不重复
            self._reply_buffer[contact] = []
        # 用最后一条对方消息作为"触发消息"，整段上下文作为综合素材
        _last = _others[-1]
        _combined_context = _ctx_msgs
        self._log("info", f"[聚合回复] {contact} flush {len(_others)} 条 → 生成综合回复")
        # 复用 _maybe_auto_reply 的同步入口（非聚合分支）触发一次 LLM 综合回复
        # 临时关闭聚合，避免递归缓冲；用合并上下文，让 LLM 看到全部消息
        _prev_agg = self._reply_agg_enabled
        self._reply_agg_enabled = False
        try:
            self._maybe_auto_reply(
                contact, _last["content"], _last["sender"],
                context=_combined_context, is_group=_is_group)
        finally:
            self._reply_agg_enabled = _prev_agg


    def _remove_reply_fingerprint(self, contact, content):
        """发送失败/异常时移除指纹，允许该消息后续重试（不永久屏蔽）"""
        try:
            _norm = (str(content or "")).strip()
            if contact and _norm and getattr(self, '_reply_fingerprints', None):
                import hashlib
                _fp = hashlib.md5(f"{contact}|{_norm}".encode("utf-8")).hexdigest()
                _s = self._reply_fingerprints.get(contact)
                if _s and _fp in _s:
                    _s.discard(_fp)
        except Exception:
            pass

    def _auto_reply_impl(self, contact, content, sender, context=None):
        """自动回复（仅对方消息触发；自己的消息绝不回复）。
        主循环轮询路径与红点路径共用——此前红点路径无回复逻辑，最小化模式下消息从不回复。

        修复内容：
        1. LLM客户端初始化检查和重试
        2. API Key有效性验证
        3. 多种发送模式自动切换
        4. 增强错误处理和日志
        """
        # 增强调试日志
        self._log("info", f"[自动回复检查] 联系人: {contact}, 发送者: {sender}, 内容: {content[:30]}...")
        self._log("info", f"[自动回复检查] auto_reply_enabled: {self.auto_reply_enabled}, llm_client: {self.llm_client is not None}")

        # 基础检查
        if sender == "me":
            self._log("debug", "[自动回复跳过] 发送者是本人")
            return
        if not self.auto_reply_enabled:
            self._log("debug", "[自动回复跳过] 自动回复未启用")
            return

        # ★ 群聊安全护栏：默认不在群里自动发言（避免对全体群成员刷屏/误发）
        #   需在 config.yaml 显式开启 auto_reply.allow_group_reply: true 才允许群聊自动回复
        _is_group = bool(context.get("__is_group__", False)) if isinstance(context, dict) else False
        if _is_group and not self.auto_reply_config.get("allow_group_reply", False):
            self._log("info", f"[自动回复跳过] 群聊『{contact}』默认不自动回复（allow_group_reply=false）")
            return

        # LLM客户端检查和初始化
        if not self.llm_client:
            try:
                self._log("warning", "[自动回复] LLM客户端未初始化，尝试重新初始化...")
                self.llm_client = LLMClient(
                self.llm_config,
                style_preset=self.role_manager.config.get("reply_style_preset") or {},
            )

                # 测试调用
                test_role = {"name": "测试", "system_prompt": "测试", "reply_style": "简洁"}
                test_reply = self.llm_client.generate_reply("测试", "你好", test_role, [])
                if not test_reply:
                    self._log("error", "[自动回复] LLM客户端测试失败，请检查API Key配置")
                    return
                self._log("info", "[自动回复] LLM客户端重新初始化成功")
            except Exception as e:
                self._log("error", f"[自动回复] LLM客户端初始化失败: {e}")
                return

        try:
            role = self.role_manager.get_role_for(contact)
            self._log("info", f"[角色] {role['name']} ({role['reply_style']})")

            # ===== 人格化回复：注入 Obsidian AI 画像(关系/风格/亲密度) + 项目上下文 =====
            role = dict(role)  # 浅拷贝，避免污染全局 role 配置
            _persona_bits = []
            if self.obsidian and self.obsidian.enabled:
                try:
                    _prof = self.obsidian.read_contact_profile(contact)
                    if _prof:
                        _persona_bits.append(_prof)
                    _proj = self.obsidian.read_project_context(contact)
                    if _proj:
                        _persona_bits.append(_proj)
                except Exception as _e:
                    self._log("debug", f"[人格化] 读取上下文失败(忽略): {_e}")
            if _persona_bits:
                _extra = "\n\n".join(_persona_bits)
                _base_sp = role.get("system_prompt", "你是一个友好的聊天助手。")
                role["system_prompt"] = (
                    f"{_base_sp}\n\n"
                    f"【与对方的关系与沟通建议（来自历史记忆，请自然融入，不要生硬提及）】\n{_extra}"
                )
                self._log("debug", f"[人格化] 已注入画像/项目上下文 ({len(_extra)} 字)")

            # ===== P0: 优先取 UI 维护的对话上下文（最近 N 轮） =====
            if self._fetch_context_fn is not None and contact:
                try:
                    rich_ctx = self._fetch_context_fn(contact) or []
                    if rich_ctx:
                        context_dicts = []
                        for m in rich_ctx[-self._context_turns*2:]:
                            # rich_ctx 可能是 dict（含 sender/content）或纯字符串
                            if isinstance(m, dict):
                                role = "assistant" if m.get("sender") in ("me", "self", "mine") else "user"
                                c = str(m.get("content", "")).strip()
                            else:
                                c = str(m).strip()
                                role = "user"
                            if c:
                                context_dicts.append({"role": role, "content": c})
                        if context_dicts:
                            context = context_dicts
                            self._log("debug", f"[上下文] 使用UI上下文 {len(context_dicts)} 条")
                except Exception as _e:
                    self._log("debug", f"[上下文] 取UI上下文失败: {_e}")
            if not context:
                context = []

            # 停止检查
            if self._stop_flag.is_set():
                return

            # 生成回复
            self._log("info", f"[自动回复] 正在生成回复...")
            reply = self.llm_client.generate_reply(contact, content, role, context)
            if not reply:
                self._log("warning", "[自动回复] LLM未生成回复")
                return

            self._log("info", f"[生成回复] {reply[:80]}...")

            # 停止检查：LLM生成完后如果已停止，不再发送
            if self._stop_flag.is_set():
                return

            # 预览确认模式：粘贴到输入框但不发送，等待用户手动确认
            preview_mode = self.auto_reply_config.get("preview_mode", False)
            if preview_mode:
                self._log("info", f"[自动回复·预览] 待确认: {reply[:50]}")
                if self._on_reply:
                    # V3 P0-5: 回调扩展为 dict payload（含状态/send_method/时间/上下文）
                    try:
                        self._on_reply({
                            "contact": contact,
                            "reply": reply,
                            "status": "preview",
                            "method": "preview_clipboard",
                            "sent_ok": False,
                            "timestamp": datetime.now().strftime("%H:%M"),
                            "role": role or "",
                        })
                    except TypeError:
                        # 兼容老签名：cb(contact, reply)
                        self._on_reply(contact, reply)
                # 粘贴到微信输入框但不发送
                try:
                    from window_manager import focus_window
                    from sender import click_input_box, clear_input_box, paste_text
                    focus_window(self.window)
                    time.sleep(0.2)
                    click_input_box(self.window)
                    time.sleep(0.1)
                    clear_input_box()
                    paste_text(reply)
                    self._log("info", "[自动回复·预览] 已粘贴到输入框，请手动确认发送")
                except Exception as e:
                    self._log("error", f"[自动回复·预览] 粘贴失败: {e}")
                # 标记已处理，防止重复触发LLM
                self.parser.add_to_context("assistant", reply)
                self.parser.mark_reply_sent(reply)
                return

            # 发送延迟
            send_delay = self.auto_reply_config.get("send_delay", 1.0)
            self._log("info", f"[自动回复] 等待 {send_delay} 秒后发送...")
            time.sleep(send_delay)

            # 记录本次回复目标联系人（供发送前归属校验使用）
            self._last_reply_contact = contact

            # 智能发送：尝试多种发送模式
            self._log("info", f"[自动回复] 开始智能发送...")
            success = self._smart_send_reply(reply)
            last_method = getattr(self, "_last_send_method", "unknown")
            if success:
                self.stats["replies_sent"] += 1
                self.parser.add_to_context("assistant", reply)
                self.parser.mark_reply_sent(reply)
                self._log("info", f"[已回复] {reply[:50]}")
                # 已读闭环：发送后重截聊天区底部，确认我方气泡真的出现
                self._verify_reply_sent(contact, reply, last_method)
                if self._on_reply:
                    # V3 P0-5: 发送成功回执（UI气泡+Obsidian同步）
                    try:
                        self._on_reply({
                            "contact": contact,
                            "reply": reply,
                            "status": "sent",
                            "method": last_method,
                            "sent_ok": True,
                            "timestamp": datetime.now().strftime("%H:%M"),
                            "role": role or "",
                        })
                    except TypeError:
                        self._on_reply(contact, reply)
            else:
                self._log("error", "[发送失败] 所有发送方法均失败")
                self._remove_reply_fingerprint(contact, content)
                if self._on_reply:
                    try:
                        self._on_reply({
                            "contact": contact,
                            "reply": reply,
                            "status": "failed",
                            "method": "all_failed",
                            "sent_ok": False,
                            "timestamp": datetime.now().strftime("%H:%M"),
                            "role": role or "",
                        })
                    except TypeError:
                        pass

        except Exception as e:
            self._log("error", f"[自动回复] 异常: {e}")
            self._remove_reply_fingerprint(contact, content)
            import traceback
            self._log("error", traceback.format_exc()[-200:])

    def _smart_send_reply(self, reply: str) -> bool:
        """智能发送回复 - 自动切换发送模式"""
        if not reply or not reply.strip():
            self._log("warning", "[智能发送] 回复内容为空")
            return False

        # 定义发送方法优先级
        send_methods = [
            ("clipboard", self._send_by_clipboard),
            ("typing", self._send_by_typing),
            ("offscreen", self._send_offscreen),
        ]

        methods_tried = []
        for method_name, send_func in send_methods:
            methods_tried.append(method_name)
            self._log("info", f"[智能发送] 尝试方法: {method_name}")

            try:
                success = send_func(reply)
                if success:
                    self._last_send_method = method_name
                    self._log("info", f"[智能发送] 成功，使用方法: {method_name}")
                    return True
                else:
                    self._log("warning", f"[智能发送] 方法 {method_name} 失败")
            except Exception as e:
                self._log("error", f"[智能发送] 方法 {method_name} 异常: {e}")

        self._log("error", f"[智能发送] 所有方法均失败，已尝试: {methods_tried}")
        return False

    def _send_by_clipboard(self, reply: str) -> bool:
        """剪贴板方式发送"""
        try:
            from window_manager import focus_window
            from sender import send_text

            # ★ 发送前校验前台焦点确为微信，防乱发
            if not self._verify_wechat_focus():
                return False
            # ★ 发送前归属校验：确认当前会话确实是目标联系人，否则先切回再发，
            #   避免回复粘贴/发送到错误的人（兜底 last 屏障之上再加一道硬护栏）。
            if not self._ensure_target_contact(self._last_reply_contact):
                self._log("warning", "[剪贴板发送] 归属校验失败(无法切到目标会话)，取消发送")
                return False
            focus_window(self.window)
            time.sleep(0.2)
            return send_text(self.window, reply)
        except Exception as e:
            self._log("error", f"[剪贴板发送] 失败: {e}")
            return False

    def _send_by_typing(self, reply: str) -> bool:
        """打字方式发送（更慢但更可靠）"""
        try:
            from window_manager import focus_window
            from sender import send_text_type_mode

            if not self._verify_wechat_focus():
                return False
            focus_window(self.window)
            time.sleep(0.2)
            return send_text_type_mode(self.window, reply)
        except Exception as e:
            self._log("error", f"[打字发送] 失败: {e}")
            return False

    def _send_offscreen(self, reply: str) -> bool:
        """屏幕外发送"""
        try:
            from window_manager import is_window_offscreen
            from sender import send_text_offscreen

            if is_window_offscreen(self.window):
                return send_text_offscreen(self.window, reply)
            else:
                self._log("warning", "[屏幕外发送] 窗口不在屏幕外模式")
                return False
        except Exception as e:
            self._log("error", f"[屏幕外发送] 失败: {e}")
            return False

    def _verify_contact_switched(self, contact):
        """点击后验证闭环：重扫侧栏确认目标联系人红点已消失（会话切入成功且已读）。

        返回 True=验证成功（红点消失）；False=红点仍在 / 截图失败或黑图 / 异常。
        这是根治『误点已读』的核心：点击后不再乐观认为成功，而是实测红点是否真的消失。
        """
        try:
            time.sleep(0.6)  # 等微信切换会话 + 红点消失动画
            _sidebar = self.red_dot_monitor.capture_sidebar(self.window)
            if _sidebar is None or float(_sidebar.mean()) < 5:
                self._log("warning", f"[验证] {contact} 侧栏截图失败/黑图 → 验证不通过")
                return False
            _unread = self.red_dot_monitor.get_unread_contacts(self.window)
            _names = {u.get("contact") for u in _unread}
            if contact in _names:
                self._log("warning", f"[验证] {contact} 红点仍在 → 点击可能未生效")
                return False
            self._log("info", f"[验证] {contact} 红点已消失 → 切换成功")
            return True
        except Exception as e:
            self._log("warning", f"[验证] {contact} 异常: {e}")
            return False

    def _verify_contact_name(self, expected_contact):
        """点击后【标题栏 OCR 双重验证】：截聊天区顶部标题栏，OCR 出当前会话名，
        与目标联系人比对 —— 确保真的切到了正确的人（而非切错/没切）。

        返回 True=名称匹配（或无法判定时不拦）；False=明显切错人。
        这是根治『识别乱/回复错人』的最后一关：红点消失只能证明"点到了某个会话"，
        但无法证明"点到了目标会话"，标题栏 OCR 能直接确认。
        """
        try:
            _hwnd = getattr(self.window, "_hWnd", None)
            if not _hwnd:
                return True  # 无句柄，不拦
            from screenshot import capture_via_printwindow, crop_title_bar_img, is_image_blank
            if self.minimize_mode == "offscreen":
                _full = capture_via_printwindow(_hwnd)
                if _full is None or _full.mean() < 5 or _full.shape[1] < 300:
                    return True
                _bar = crop_title_bar_img(_full)
            else:
                from screenshot import capture_chat_area
                _full = capture_chat_area(self.window)
                if _full is None or is_image_blank(_full):
                    return True
                _bar = crop_title_bar_img(_full)
            if _bar is None or is_image_blank(_bar):
                return True
            from ocr_engine import recognize, _is_valid_contact_name
            _ocr = recognize(_bar, scale=1.0, min_confidence=0.35, merge_bubble=False, denoise=False)
            if not _ocr:
                return True
            _exp = (expected_contact or "").strip()
            if not _exp:
                return True
            for r in _ocr:
                _t = (r.get("text", "") or "").strip()
                if not _t:
                    continue
                # 直接包含目标名（群名带括号数字也 OK）
                if _exp in _t or _t in _exp:
                    self._log("info", f"[验证] 标题栏OCR确认切到: {_exp} (命中'{_t}')")
                    return True
            # 没匹配到任何包含目标名的文本 → 可能切错人
            _got = " / ".join((r.get("text", "") or "")[:15] for r in _ocr[:3])
            self._log("warning", f"[验证] 标题栏OCR未命中目标'{_exp}'，当前标题: {_got} → 疑似切错人")
            return False
        except Exception as e:
            self._log("debug", f"[验证] 标题栏OCR异常(不拦): {e}")
            return True

    def _verify_wechat_focus(self):
        """发送前校验：微信窗口必须是当前前台窗口，否则取消发送。

        防『乱发』：若用户切到了别的程序、或焦点意外落到非微信窗口，
        粘贴+Enter 会把回复发到错误的地方。此函数要求前台窗口就是
        我们正在监控的微信窗口（标题/类匹配），不匹配则视为不安全。
        """
        try:
            import win32gui
            fg = win32gui.GetForegroundWindow()
            if not fg:
                return False
            hwnd = getattr(self.window, "_hWnd", None)
            # 情况1：前台窗口正好是目标微信窗
            if hwnd and fg == hwnd:
                return True
            # 情况2：前台窗口是目标窗的子窗口（输入框聚焦时常见）
            if hwnd:
                cur = fg
                for _ in range(6):
                    if cur == hwnd:
                        return True
                    cur = win32gui.GetParent(cur)
                    if not cur:
                        break
            # 情况3：前台窗口标题/类名属微信（兜底，允许同进程其它微信窗）
            try:
                title = win32gui.GetWindowText(fg)
                cls = win32gui.GetClassName(fg)
            except Exception:
                title, cls = "", ""
            if "微信" in title or cls in ("WeChatMainWndForPC", "Qt51514QWindowIcon"):
                return True
            self._log("warning", f"[发送校验] 前台窗口非微信(title={title!r},cls={cls!r})，取消发送防乱发")
            return False
        except Exception as e:
            self._log("error", f"[发送校验] 异常: {e}，保守取消发送")
            return False

    def _ensure_target_contact(self, expected_contact):
        """发送前【归属校验】：确认当前微信会话就是目标联系人。

        若标题栏 OCR 出当前会话名≠目标 → 尝试切回目标联系人（侧栏点击）；
        切回后仍不匹配则放弃（返回 False），交由上层取消发送，绝不发错人。
        目标为空（无法判定）时放行（不阻断正常发送）。
        """
        if not expected_contact:
            return True
        try:
            _ok = self._verify_contact_name(expected_contact)
            if _ok:
                return True
            # 切错/未切到目标 → 尝试切回目标联系人
            self._log("warning", f"[归属校验] 当前会话非'{expected_contact}'，尝试切回")
            _hwnd = getattr(self.window, "_hWnd", None)
            if not _hwnd:
                return False
            _sidebar = self.red_dot_monitor.capture_sidebar(self.window)
            if _sidebar is None:
                return False
            from ocr_engine import recognize
            _ocr = recognize(_sidebar, scale=1.0, min_confidence=0.40,
                             merge_bubble=False, denoise=False)
            _target_y = None
            for r in _ocr:
                _t = (r.get("text", "") or "").strip()
                if expected_contact in _t or _t in expected_contact:
                    _target_y = r.get("y_center", 0)
                    break
            if _target_y is None:
                self._log("warning", f"[归属校验] 侧栏未找到'{expected_contact}'，取消发送")
                return False
            from window_manager import simulate_click_window
            _cx, _cy = self.red_dot_monitor.get_click_client_position(
                self.window, int(_target_y), None)
            simulate_click_window(self.window, _cx, _cy)
            time.sleep(0.8)
            return self._verify_contact_name(expected_contact)
        except Exception as e:
            self._log("debug", f"[归属校验] 异常(保守取消): {e}")
            return False

    def _verify_reply_sent(self, contact, reply, method):
        """已读闭环（best-effort）：发送后重截聊天区底部，确认我方气泡真的出现。

        仅做日志级校验，不阻塞流程（确实点了发送，只是确认渲染/落库）。
        若检测不到我方最新气泡，记 warning + 移除指纹允许重试，便于发现『发送假成功』。
        """
        try:
            time.sleep(0.8)  # 等微信把消息渲染进聊天区
            _hwnd = getattr(self.window, "_hWnd", None)
            if not _hwnd:
                return
            from screenshot import capture_via_printwindow, capture_chat_bottom, is_image_blank
            if self.minimize_mode == "offscreen":
                _full = capture_via_printwindow(_hwnd)
                if _full is not None and _full.mean() >= 5 and _full.shape[1] >= 300:
                    _h, _w = _full.shape[:2]
                    _bh = int(_h * 0.35)
                    _img = _full[_h - _bh:, :]
                else:
                    _img = capture_chat_bottom(self.window, ratio=0.35)
            else:
                _img = capture_chat_bottom(self.window, ratio=0.35)
            if _img is None or is_image_blank(_img):
                self._log("warning", f"[已读校验] {contact} 底部截图失败/黑图，无法确认是否发出")
                return
            from ocr_engine import recognize, identify_senders_v4
            _ocr = recognize(_img, scale=1.0, min_confidence=0.30, merge_bubble=False, denoise=False)
            if not _ocr:
                self._log("warning", f"[已读校验] {contact} 底部OCR无结果，无法确认是否发出")
                return
            _ocr = identify_senders_v4(_ocr, _img)
            # 取最近3条，看是否有 me 发送且文本包含 reply 片段
            _recent = _ocr[-3:]
            _needle = (reply or "").strip()[:10]
            _found = False
            for r in _recent:
                if r.get("sender") == "me" and _needle and _needle in (r.get("text", "") or ""):
                    _found = True
                    break
            if _found:
                self._log("info", f"[已读校验] {contact} 我方气泡已出现 → 发送确认({method})")
            else:
                self._log("warning", f"[已读校验] {contact} 底部未检测到我方最新气泡(可能延迟/OCR误差)，"
                                     f"标记为可能未发出 → 允许重试")
                self._remove_reply_fingerprint(contact, reply)
        except Exception as e:
            self._log("debug", f"[已读校验] {contact} 异常(忽略): {e}")

    def _dedup_ocr_by_position(self, ocr_results):
        """位置桶去抖：同位置桶+文本相似度>=0.9 的 OCR 行合并为一条（保留置信高者）。
        抑制同一消息每帧 OCR 抖动产生的近似重复；不同文本同桶（滚动后新内容）保留。"""
        if not ocr_results or len(ocr_results) < 2:
            return ocr_results
        from difflib import SequenceMatcher
        final = []
        for r in ocr_results:
            bx = int(r.get("x_center", 0) // 60)
            by = int(r.get("y_center", 0) // 60)
            # 统一打桶标：替换进 final 时自动携带桶号，后续同桶比对才能命中
            r["_bk_x"], r["_bk_y"] = bx, by
            merged = False
            for i, kept in enumerate(final):
                if kept.get("_bk_x") == bx and kept.get("_bk_y") == by:
                    t1 = str(kept.get("text", ""))
                    t2 = str(r.get("text", ""))
                    if t1 and t2 and SequenceMatcher(None, t1, t2).ratio() >= 0.90:
                        if (r.get("confidence", 0) or 0) > (kept.get("confidence", 0) or 0):
                            final[i] = r
                        merged = True
                        break
            if not merged:
                final.append(r)
        return final

    def _load_reddot_seen(self):
        """加载红点跨轮去重历史（重启不丢）"""
        try:
            if os.path.exists(self._reddot_seen_path):
                with open(self._reddot_seen_path, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                return {k: set(v) for k, v in raw.items() if isinstance(v, list)}
        except Exception as e:
            self._log("warning", f"[红点] 加载去重历史失败: {e}")
        return {}

    def _save_reddot_seen(self):
        """保存红点跨轮去重历史"""
        try:
            with open(self._reddot_seen_path, "w", encoding="utf-8") as f:
                json.dump(
                    {k: sorted(v) for k, v in self._reddot_msg_seen.items()},
                    f, ensure_ascii=False, indent=1)
        except Exception as e:
            self._log("warning", f"[红点] 保存去重历史失败: {e}")

    def clear_reddot_seen(self, contacts=None):
        """删除历史/会话时同步清跨轮去重缓存，让被删消息能重新被识别为新消息。
        contacts=None 清空全部；否则只清指定联系人（规范化+原样）。
        同时清 monitor 的会话级嫌疑黑名单，使被删会话可重新被检测。
        """
        import re
        if contacts is None:
            self._reddot_msg_seen = {}
            self._log("info", "[红点] 已清空全部跨轮去重缓存")
        else:
            for c in contacts:
                self._reddot_msg_seen.pop(c, None)
                self._reddot_msg_seen.pop(re.sub(r'\s+', '', c), None)
            self._log("info", f"[红点] 已清空 {len(contacts)} 个联系人的跨轮去重缓存")
        try:
            self._save_reddot_seen()
        except Exception:
            pass
        # 同步清嫌疑黑名单（被删会话不应再被当"误点已读"永久屏蔽）
        if getattr(self, "red_dot_monitor", None):
            try:
                self.red_dot_monitor.clear_suspect(contacts)
            except Exception:
                pass

    def _get_valid_hwnd(self):
        """获取有效的微信窗口句柄（统一入口）"""
        import win32gui
        hwnd = getattr(self.window, '_hWnd', None)
        if hwnd and win32gui.IsWindow(hwnd):
            return hwnd
        # 重新查找
        if self.window and self.window.title:
            hwnd = win32gui.FindWindow(None, self.window.title)
            if hwnd and win32gui.IsWindow(hwnd):
                self.window._hWnd = hwnd
                self._log("info", f"[句柄] 重新获取窗口句柄: {hwnd}")
                return hwnd
        return None

    def find_window(self):
        """查找微信窗口"""
        self._log("info", "正在定位微信窗口...")
        time.sleep(0.3)
        self.window = find_wechat_window()
        if self.window is None:
            self._log("error", "未找到微信窗口，请确保微信已打开")
            return False

        self._log("info", f"✔ 找到微信窗口: {self.window.title}")
        time.sleep(0.2)
        self._log("info", f"  位置: left={self.window.left}, top={self.window.top}")
        self._log("info", f"  大小: {self.window.width}x{self.window.height}")
        time.sleep(0.2)
        # DPI诊断
        try:
            import ctypes
            hdc = ctypes.windll.user32.GetDC(0)
            dpi = ctypes.windll.gdi32.GetDeviceCaps(hdc, 88)  # LOGPIXELSX
            ctypes.windll.user32.ReleaseDC(0, hdc)
            scale = round(dpi / 96 * 100)
            self._log("info", f"DPI缩放: {scale}% (DPI={dpi})")
        except Exception:
            self._log("info", "DPI缩放: 检测失败")

        # 检查窗口最小化状态
        try:
            from window_manager import is_window_minimized
            if is_window_minimized(self.window):
                if self.minimize_mode == "minimized":
                    self._log("info", "[最小化监控] 微信已最小化，启用后台截图模式")
                    self._log("info", "[最小化监控] PrintWindow + 屏幕外恢复方案已就绪")
                else:
                    self._log("warning", f"[最小化监控] 微信已最小化，minimize_mode={self.minimize_mode}（建议用offscreen模式）")
        except Exception:
            pass

        return True

    def start(self):
        """启动监控（在后台线程中运行，支持窗口等待）"""
        if self._running:
            return

        self._running = True
        self._stop_flag.clear()
        self._on_status("running")
        self._log("info", "━━━ 正在启动监控，请稍候 ━━━")

        self._thread = threading.Thread(target=self._monitor_thread, daemon=True)
        self._thread.start()

    def _monitor_thread(self):
        """监控线程：等待窗口 → 初始化 → 主循环（含自动重连）"""
        try:
            # 阶段1：等待微信窗口出现（支持最小化）
            if not self._wait_for_window():
                self._running = False
                self._on_status("stopped")
                return

            # 阶段2：初始化模块
            if not self.initialize():
                self._running = False
                self._on_status("error")
                return

            # 阶段3：主循环（含窗口丢失自动重连）
            self._main_loop()
        except Exception as e:
            import traceback as _tb
            self._running = False
            self._log("error", f"[启动] 线程异常导致初始化中断: {e}")
            self._log("error", "完整堆栈：\n" + _tb.format_exc()[-1800:])
            self._on_status("error")

    def _wait_for_window(self):
        """
        等待微信窗口出现，持续监控。
        支持最小化窗口，找不到时每3秒重试。
        """
        retry_count = 0
        while not self._stop_flag.is_set():
            self.window = find_wechat_window()
            if self.window:
                self._log("info", f"✔ 找到微信窗口: {self.window.title}")
                self._log("info", f"  位置: left={self.window.left}, top={self.window.top}")
                self._log("info", f"  大小: {self.window.width}x{self.window.height}")

                # DPI诊断
                try:
                    import ctypes
                    hdc = ctypes.windll.user32.GetDC(0)
                    dpi = ctypes.windll.gdi32.GetDeviceCaps(hdc, 88)
                    ctypes.windll.user32.ReleaseDC(0, hdc)
                    scale = round(dpi / 96 * 100)
                    self._log("info", f"  DPI缩放: {scale}% (DPI={dpi})")
                except Exception:
                    pass

                # 检查最小化状态
                try:
                    from window_manager import is_window_minimized
                    if is_window_minimized(self.window):
                        if self.minimize_mode == "minimized":
                            self._log("info", "[最小化监控] 微信已最小化，启用后台截图模式")
                            self._log("info", "[最小化监控] PrintWindow + 屏幕外恢复方案已就绪")
                        else:
                            self._log("warning", f"[最小化监控] 微信已最小化，minimize_mode={self.minimize_mode}（建议用offscreen模式）")
                except Exception:
                    pass

                return True

            retry_count += 1
            if retry_count == 1:
                self._log("warning", "未找到微信窗口，开始持续等待...")
                self._log("info", "请打开微信并登录，程序将自动检测")
            elif retry_count % 10 == 0:
                self._log("info", f"[窗口等待] 已等待 {retry_count * 3} 秒，仍在监控微信窗口...")

            time.sleep(3)

        return False

    def stop(self):
        """停止监控"""
        self._stop_flag.set()
        self._running = False
        self._on_status("stopped")
        self._log("info", "正在停止监控...")
        # 停止聚合回复线程
        try:
            if getattr(self, "_reply_agg_enabled", False):
                self._stop_reply_aggregator()
        except Exception:
            pass

        # 屏幕外模式：恢复窗口到原始可见位置
        try:
            from window_manager import is_window_offscreen, bring_window_back
            if self.window and is_window_offscreen(self.window):
                bring_window_back(self.window)
                self._log("info", "[屏幕外] 停止监控：窗口已恢复到原始位置")
        except Exception:
            pass

        if self.storage and self.storage_config.get("export_on_exit"):
            try:
                self.storage.export_csv()
                self._log("info", "数据已自动导出CSV")
            except Exception as e:
                self._log("error", f"导出CSV失败: {e}")

        # V4: 退出前强制 flush Obsidian 写缓冲（避免丢消息）
        if self.obsidian and self.obsidian.enabled:
            try:
                self.obsidian.flush_all()
                self._log("info", "[Obsidian] 写缓冲已强制落盘")
            except Exception as e:
                self._log("error", f"[Obsidian] flush失败: {e}")
            # 退出前生成每日简报（AI 汇总当日要事；无消息则跳过）
            try:
                if self.obsidian.write_daily_brief():
                    self._log("info", "[Obsidian] 每日简报已生成")
            except Exception as e:
                self._log("error", f"[Obsidian] 每日简报失败: {e}")

    def is_running(self):
        return self._running

    def _pick_title_name(self, ocr_results):
        """从顶部标题栏 OCR 结果里挑出当前会话名（群名/联系人名）。

        过滤掉微信UI控件词、纯数字、微信号、单字符噪声等，优先取最上方、
        且更像“会话名”的一行（多字符优先，单字/标点噪声坚决拒）。
        """
        if not ocr_results:
            return ""
        _ban = {"微信", "草稿", "搜索", "添加", "通讯录", "文件传输助手",
                "订阅号", "微信团队", "服务通知", "设置", "收藏", "朋友圈",
                "?"}
        _cands = []
        for r in ocr_results:
            if not isinstance(r, dict):
                continue
            t = (r.get("text") or "").strip()
            if not t or t in _ban:
                continue
            if len(t) < 2:            # ★ 单字符（?、一、单个汉字）绝不当会话名
                continue
            if len(t) > 24:           # 群名/联系人名通常不会太长
                continue
            if "@" in t or t.lower().startswith("wxid"):   # 微信号
                continue
            if re.match(r'^[\d\s()（）+\-:：?]+$', t):      # 纯数字/符号/问号（如成员数、?)
                continue
            if re.match(r'^[\?\.\，\。\！\？\!\-\_]+$', t):  # 纯标点噪声
                continue
            _bbox = r.get("bbox")
            _y = _bbox.get("y", 0) if isinstance(_bbox, dict) else r.get("y", 0)
            # 越长越像真名 → 加权（相同y时优先长名）
            _len_w = len(t)
            _cands.append((_y, -_len_w, r.get("confidence", 0.0), t))
        if not _cands:
            return ""
        # 取最上方（y最小）、同高取“最长且最像名”的行
        _cands.sort(key=lambda c: (c[0], c[1], -c[2]))
        return _cands[0][3]

    def _resolve_contact_name(self, hwnd, fallback_name=""):
        """解析当前会话真实名称（群名/联系人名）。

        优先级：① 窗口标题（旧版微信含联系人名）② 顶部标题栏OCR
        （微信4.x 唯一可靠来源）③ 红点匹配名兜底。
        微信4.x 窗口标题恒为'微信'，必须靠②拿到真实名，否则群聊/私聊
        全被误存为'微信'。
        """
        # ① 窗口标题（非微信4.x 的版本可能含真实名）
        try:
            _title_name = get_contact_name(self.window)
        except Exception:
            _title_name = ""
        if _title_name and _title_name != "微信" and _title_name.strip():
            return _title_name.strip()
        # ② 顶部标题栏 OCR
        if hwnd:
            try:
                from screenshot import capture_via_printwindow, crop_title_bar_img
                _img = capture_via_printwindow(hwnd)
                if _img is not None and float(_img.mean()) >= 3:
                    _bar = crop_title_bar_img(_img)
                    if _bar is not None:
                        from ocr_engine import recognize
                        _res = recognize(_bar, scale=3.0, min_confidence=0.35)
                        _name = self._pick_title_name(_res)
                        if _name:
                            return _name
            except Exception:
                pass
        # ③ 红点匹配名兜底
        if fallback_name and fallback_name != "微信":
            return fallback_name
        # ④ 最近一次有效名兜底（防 OCR 瞬时失败闪回"微信"/空）
        #    仅当本次完全取不到名、且上次名合法时使用；同名持续有效
        _last = getattr(self, "_last_contact_name", "") or ""
        if _last and _last != "微信":
            self._log("debug", f"[联系人名] OCR/标题均无结果，沿用上次有效名: {_last}")
            return _last
        return ""

    def _remember_contact_name(self, name):
        """记录最近一次成功解析到的有效联系人名（供瞬时OCR失败兜底）。"""
        if name and name != "微信" and not self._looks_like_preview(name):
            self._last_contact_name = name

    def _flush_stable_candidates(self, contact_name):
        """
        确认稳定帧超时的候选新消息。
        场景：新消息出现→第1帧OCR进入候选池→画面静止→增量检测跳过OCR→
        候选永远凑不够stable_frames帧。此处按超时强制确认并通知UI/提取。
        """
        try:
            candidates = self.parser.flush_stable_timeout()
        except Exception:
            return
        for msg in candidates:
            content = str(msg.get("content", "")).strip()
            # 剥离 OCR 混进正文的时间戳前缀（9:57 / 星期三07:50 / 7/18 等）
            content = self._strip_timestamp_prefix(content)
            if not content or msg.get("sender") == "me":
                continue
            if any(skip in content for skip in ["助手v2.0", "AI助手", "微信 AI", "信息提取", "自动回复", "数据查看", "设置", "红点", "屏幕外", "保活", "截图", "预览", "诊断", "增量", "窗口坐标"]):
                continue
            if len(content) < 2:
                continue
            import re
            if re.match(r'^\d{1,2}[:：]\d{2}$', content) or re.match(r'^\d+$', content):
                continue
            if re.match(r'^(昨天|今天|明天|后天|星期[一二三四五六日天]|周[一二三四五六日天])$', content):
                continue
            if any(sw in content for sw in self.SYSTEM_MSG_MARKERS):
                continue
            self.stats["messages_detected"] += 1
            self._log("info", f"[新消息·确认] {contact_name}: {content[:80]}")
            _ts = datetime.now().strftime("%H:%M:%S")
            _sender = msg.get("sender", "other")
            # 先发基础卡（快速反馈），提取完成后用同一msg_key原位更新
            self._send_ui_card(contact_name, _sender, content, _ts)
            # 信息提取（与主流程一致）
            if self.extractor:
                try:
                    extracted = self.extractor.extract(
                        text=content,
                        sender=_sender,
                        contact_name=contact_name,
                        timestamp=datetime.now(),
                    )
                    self.stats["extracted"] += 1
                    if extracted.get("is_important"):
                        self.stats["important"] += 1
                        self._log("warning", f"[重要] {contact_name}: {extracted.get('importance_reason', '')}")
                    # 富信息更新卡：关键词/摘要/分类，原位刷新不重复
                    self._send_ui_card(
                        contact_name, _sender, content, _ts,
                        extracted=extracted,
                        is_important=extracted.get("is_important", False),
                        importance_reason=extracted.get("importance_reason", ""),
                        is_update=True,
                    )
                    self._store_extracted(extracted)
                    self._on_extract(extracted)
                except Exception:
                    pass

    def _main_loop(self):
        """主循环"""
        poll_interval = self.wechat_config.get("poll_interval", 1.0)
        ocr_scale = self.wechat_config.get("ocr_scale", 0.85)
        ocr_min_conf = self.wechat_config.get("ocr_min_confidence", 0.60)
        ocr_merge = self.wechat_config.get("ocr_merge_bubble", True)
        ocr_denoise = self.wechat_config.get("ocr_denoise", True)
        capture_ratio = self.wechat_config.get("capture_ratio", 0.35)
        send_delay = self.auto_reply_config.get("send_delay", 0.5)

        contact_name = self._resolve_contact_name(getattr(self.window, "_hWnd", None))
        self._remember_contact_name(contact_name)
        # V2: 初始群聊/私聊判断（基于窗口标题），后续每轮会结合OCR再精细裁决
        ctx = analyze_chat_context(self.window)
        contact_name = ctx["contact"] or contact_name
        self._remember_contact_name(contact_name)
        current_chat_kind = "group" if ctx["is_group"] is True else \
            ("personal" if ctx["is_group"] is False else "unknown")
        current_group_members = set()
        self._log("info", f"当前聊天联系人: {contact_name} (标题判定类型: {current_chat_kind}, 来源: {ctx.get('source')})")

        while not self._stop_flag.is_set():
            try:
                loop_start = time.time()

                if not is_window_visible(self.window):
                    # 窗口不可见/丢失，尝试重新查找
                    hwnd = getattr(self.window, '_hWnd', None)
                    if hwnd:
                        try:
                            import win32gui
                            if not win32gui.IsWindow(hwnd):
                                self._log("warning", "微信窗口已关闭，尝试重新连接...")
                                if not self._wait_for_window():
                                    break
                                contact_name = get_contact_name(self.window)
                                self._log("info", f"重新连接成功: {contact_name}")
                                continue
                        except Exception:
                            pass
                    time.sleep(poll_interval)
                    continue

                # === 屏幕外保活：最小化→立即恢复+移屏幕外，保持渲染 ===
                if self.minimize_mode == "offscreen":
                    try:
                        from window_manager import keep_alive_offscreen
                        keep_alive_offscreen(self.window)
                        # 给Qt渲染时间：移到屏幕外后立即截图可能截到空白
                        time.sleep(0.3)
                    except Exception as e:
                        self._log("warning", f"[保活] 异常: {e}")

                # 勿扰模式：只暂停红点自动切换，不堵住当前窗口监控
                dnd_active = self._check_do_not_disturb()
                if dnd_active:
                    # 用户正在操作，跳过红点扫描但继续监控当前窗口
                    pass

                # 快速模式：只控制红点扫描频率，不堵住当前窗口监控
                fast_triggered = False
                if self.fast_mode and self.red_dot_monitor and self.red_dot_monitor.enabled:
                    fast_triggered = self._check_fast_mode()

                # === 红点监控：检测左侧栏未读消息 ===
                # 勿扰模式时跳过红点扫描；快速模式触发时立即扫描，否则按正常3秒间隔扫描
                if not dnd_active:
                    unread = None
                    _scan_method = ""

                    # ★ UIA 快速路径：最小化/屏幕外时优先用 UIA（无渲染，0.05s）
                    if self._uia_monitor is not None and self._uia_monitor.enabled:
                        try:
                            from window_manager import is_window_minimized
                            _is_min = is_window_minimized(self.window)
                            if _is_min or self.minimize_mode == "offscreen":
                                unread = self._uia_monitor.get_unread_contacts(self.window)
                                if unread is not None:
                                    _scan_method = "uia"
                                    self._log("info", "[UIA] 无渲染扫描完成: "
                                              f"{len(unread) if unread else 0} 个未读")
                        except Exception:
                            pass

                    # ★ 截图路径：UIA 不可用或无结果时回退
                    if (unread is None and
                            self.red_dot_monitor and self.red_dot_monitor.enabled):
                        check_interval = 1 if fast_triggered else 3
                        if fast_triggered or self.red_dot_monitor.should_check(interval=check_interval):
                            if fast_triggered:
                                self._log("info", "[快速] 检测到窗口标题未读数，触发红点扫描")
                            self._log("info", "[红点] 正在扫描左侧栏未读消息...")
                            unread = self.red_dot_monitor.get_unread_contacts(self.window)
                            _scan_method = "screenshot"
                            # ★ 红点扫描期间也推送预览（侧边栏截图），避免预览一直"等待截图"
                            try:
                                _sb_img = getattr(self.red_dot_monitor, '_last_sidebar_img', None)
                                if _sb_img is not None:
                                    self._on_capture(_sb_img)
                            except Exception:
                                pass
                            debug_info = getattr(self.red_dot_monitor, '_last_debug', '')
                            if debug_info:
                                self._log("info", f"[红点] 诊断: {debug_info}")

                    if unread:
                        self._log("info", f"[红点] 检测到 {len(unread)} 个联系人有未读消息"
                                  f" (方式:{_scan_method}): {[u['contact'] for u in unread]}")
                        # ★ V3.4 前台不抢窗口：用户正在用微信时不主动切换会话，
                        #   避免监控抢走用户正在看的窗口（扫描照常，仅跳过切换处理）。
                        _respect_focus = self.role_manager.config.get(
                            "monitor", {}).get("respect_user_focus", False)
                        if _respect_focus and self._is_wechat_foreground():
                            self._log("info",
                                f"[红点] 前台使用中且已开启 respect_user_focus → 本轮跳过切换"
                                f"（待处理: {[u['contact'] for u in unread]}），避免打断用户")
                        else:
                            self._handle_unread_contacts(unread, contact_name)
                            continue  # 处理完未读后跳过本轮正常截图
                    elif unread is not None:
                        self._log("info", f"[红点] 未检测到未读消息 (方式:{_scan_method})，继续监控当前窗口")

                # 0.5 每轮重新解析当前窗口联系人名（红点切换/切回后窗口已变，
                #     必须刷新 contact_name，否则消息会被存进空 contact "" 幽灵会话）
                _now_cn = time.time()
                if _now_cn - getattr(self, "_last_contact_resolve", 0) > 1.5:
                    self._last_contact_resolve = _now_cn
                    try:
                        _cn = self._resolve_contact_name(getattr(self.window, "_hWnd", None))
                        if _cn:
                            contact_name = _cn
                            self._remember_contact_name(contact_name)
                        else:
                            # OCR/标题均无结果：沿用上次有效名（_resolve 内部已兜底，这里防空串污染）
                            _last = getattr(self, "_last_contact_name", "") or ""
                            if _last and _last != "微信":
                                contact_name = _last
                    except Exception:
                        pass

                # 1. 截图
                try:
                    from screenshot import capture_via_printwindow_stable, capture_chat_bottom, capture_minimized_window
                    from window_manager import is_window_minimized
                    hwnd = getattr(self.window, '_hWnd', None)
                    image = None

                    if self.minimize_mode == "offscreen":
                        # 屏幕外模式：先自愈（防最小化隐形窗口），再三级回退截图
                        if not hwnd:
                            hwnd = self._get_valid_hwnd()
                        if hwnd:
                            # 关键：截图前确保窗口"恢复态"，否则截到237x56隐形窗口
                            from screenshot import ensure_window_rendering
                            win_rect = ensure_window_rendering(hwnd, self.window)

                            if win_rect is None:
                                self._log("error", "[截图] 自愈失败：窗口无法恢复渲染")
                                image = None
                            else:
                                rl, rt, rw, rh = win_rect
                                # 诊断：截图前窗口坐标（限频5秒）
                                _now_cr = time.time()
                                if _now_cr - getattr(self, "_last_coord_log", 0) > 5:
                                    self._last_coord_log = _now_cr
                                    self._log("info", f"[截图] 窗口坐标=({rl},{rt}) {rw}x{rh}, "
                                               f"屏幕外={rl<=-1000}")
                                # offscreen模式：无论窗口在哪都用PrintWindow（避免mss截到程序UI）
                                if self.minimize_mode == "offscreen":
                                    full_img = capture_via_printwindow_stable(hwnd)
                                    if full_img is None or (hasattr(full_img, 'mean') and full_img.mean() < 5):
                                        self._log("info", "[截图] PrintWindow无效，尝试BitBlt...")
                                        from screenshot import capture_via_bitblt
                                        full_img = capture_via_bitblt(hwnd)
                                elif rl > -1000:
                                    # 非offscreen模式：窗口在可见区域 → mss直接截
                                    from screenshot import capture_region
                                    full_img = capture_region(win_rect)
                                else:
                                    # 非offscreen模式：屏幕外 → PrintWindow
                                    full_img = capture_via_printwindow_stable(hwnd)

                                # 回退1已合并到上面（offscreen模式自动尝试BitBlt）

                                # 回退2已移除：不再闪现窗口（用户要求后台监控不闪窗）
                                # PrintWindow/BitBlt都失败时，直接报告失败，保持窗口在屏幕外
                                if full_img is None or (hasattr(full_img, 'mean') and full_img.mean() < 5) or (full_img is not None and full_img.shape[1] < 300):
                                    if rl <= -1000:
                                        self._log("warning", "[截图] 屏幕外PrintWindow/BitBlt均失败，"
                                                   "窗口保持在屏幕外不闪现（后台模式不干扰用户）")

                                if full_img is not None and full_img.mean() >= 5 and full_img.shape[1] >= 300:
                                    from screenshot import crop_chat_region_img
                                    image = crop_chat_region_img(full_img, bottom_ratio=capture_ratio)
                                    self._last_full_capture = full_img  # 预览用全窗图

                                    # 定期保存诊断图（每15秒最多1张）
                                    now_ts = time.time()
                                    if now_ts - getattr(self, "_last_diag_save", 0) > 15:
                                        self._last_diag_save = now_ts
                                        from screenshot import save_debug_image
                                        save_debug_image(full_img, prefix="capture")
                                else:
                                    shape_info = f"{full_img.shape[1]}x{full_img.shape[0]}, mean={full_img.mean():.1f}" if full_img is not None else "None"
                                    self._log("warning", f"[截图] 截图无效({shape_info})，存fail图")
                                    if full_img is not None:
                                        from screenshot import save_debug_image
                                        save_debug_image(full_img, prefix="fail")
                        else:
                            self._log("error", "[截图] 无法获取有效窗口句柄")

                    elif self.minimize_mode == "minimized":
                        # 最小化模式：恢复窗口到可见位置截图（旧方案，闪窗）
                        if not hwnd:
                            self._log("warning", "[截图] 无窗口句柄，尝试重新获取")
                            hwnd = self._get_valid_hwnd()
                        if hwnd:
                            flash_pos = self.wechat_config.get("flash_position", "corner")
                            image = capture_minimized_window(
                                self.window, hwnd=hwnd,
                                skip_printwindow=True, flash_position=flash_pos)
                            if image is not None:
                                from screenshot import crop_chat_region_img
                                image = crop_chat_region_img(image, bottom_ratio=capture_ratio)
                        else:
                            self._log("error", "[截图] 无法获取有效窗口句柄")
                    else:
                        # 正常模式：先试 PrintWindow（窗口被遮挡时有效），再用 mss
                        if hwnd:
                            full_img = capture_via_printwindow(hwnd)
                            if full_img is not None and full_img.mean() > 5:
                                from screenshot import crop_chat_region_img
                                image = crop_chat_region_img(full_img, bottom_ratio=capture_ratio)
                                self._log("info", f"[截图] PrintWindow成功: {full_img.shape[1]}x{full_img.shape[0]} → 聊天区底部{capture_ratio}")

                        if image is None:
                            if is_window_minimized(self.window):
                                self._log("info", "[截图] 窗口已最小化，尝试屏幕外恢复")
                                image = capture_minimized_window(self.window, skip_printwindow=True)
                                if image is not None:
                                    from screenshot import crop_chat_region_img
                                    image = crop_chat_region_img(image, bottom_ratio=capture_ratio)
                            else:
                                image = capture_chat_bottom(self.window, ratio=capture_ratio)

                except Exception as e:
                    self._log("error", f"截图异常: {e}")
                    import traceback
                    self._log("error", traceback.format_exc())
                    image = None

                self.stats["frames_captured"] += 1

                if image is None or is_image_blank(image):
                    if image is None:
                        self._log("warning", "截图失败，可能窗口被遮挡或最小化")
                    else:
                        self._log("warning", "截图为空，可能窗口被遮挡")
                    
                    # 即使截图失败，也发送上一张成功的预览图（避免预览冻结）
                    last_good = getattr(self, "_last_good_preview", None)
                    if last_good is not None:
                        try:
                            self._on_capture(last_good)
                        except Exception:
                            pass
                    
                    time.sleep(poll_interval)
                    continue
                
                # 保存当前截图为上一张成功的预览图
                self._last_good_preview = image

                # ★ ③ 截图质量评分：模糊帧（渲染中/过渡动画）跳过本轮OCR，防误识别
                try:
                    import cv2 as _cv2
                    _gray = _cv2.cvtColor(image, _cv2.COLOR_BGR2GRAY)
                    _blur_var = _cv2.Laplacian(_gray, _cv2.CV_64F).var()
                    _blur_min = self.wechat_config.get("blur_min_var", 25)
                    if _blur_var < _blur_min:
                        self._log("info", f"[质量] 截图模糊(Laplacian={_blur_var:.0f}<{_blur_min})，跳过本轮OCR")
                        time.sleep(poll_interval)
                        continue
                except Exception:
                    pass

                # === 截图预览：实时更新预览窗口（立即发送，不等OCR） ===
                try:
                    # 优先使用全窗图（offscreen模式），否则使用OCR裁剪图
                    preview_img = getattr(self, "_last_full_capture", None)
                    if preview_img is None:
                        preview_img = image
                    
                    # 发送预览
                    self._on_capture(preview_img)
                    self._last_full_capture = None  # 用完即清
                    
                    # 限频日志：确认预览链路在工作（每5秒最多1条）
                    _now = time.time()
                    if _now - getattr(self, "_last_preview_log", 0) > 5:
                        self._last_preview_log = _now
                        _pw = (preview_img.shape[1], preview_img.shape[0]) if hasattr(preview_img, "shape") else (0, 0)
                        _mean = float(preview_img.mean()) if hasattr(preview_img, "mean") else 0
                        self._log("info", f"[预览] ✅ 已发送截图 {_pw[0]}x{_pw[1]} (均值={_mean:.1f})")
                except Exception as e:
                    # 限频错误日志（每30秒最多1条）
                    _now = time.time()
                    if _now - getattr(self, "_last_preview_err_log", 0) > 30:
                        self._last_preview_err_log = _now
                        self._log("warning", f"[预览] ❌ 发送失败: {e}")

                # 检查是否最小化模式
                from window_manager import is_window_minimized
                _is_min = is_window_minimized(self.window)

                # 2. 帧变化检测：判断是否需要做OCR
                # ★ 最小化/后台模式同样启用帧变化检测（省CPU）：
                #   画面无变化 -> 跳过整轮OCR；有变化 -> 仍走全量OCR（防漏，不用增量区域）
                if self.smart_monitor:
                    need_ocr, diff_regions = self.smart_monitor.should_run_ocr(image)
                    if not need_ocr:
                        # 画面无变化：先确认稳定帧超时候选（防新消息漏报），再跳过
                        self._flush_stable_candidates(contact_name)
                        time.sleep(poll_interval)
                        continue

                    if _is_min:
                        diff_regions = []  # 最小化：变化只用于"要不要OCR"，区域仍全量（防漏）
                        self._log("info", "[帧检测] 画面有变化 -> 全量OCR")
                    else:
                        self._log("info", f"[增量] 检测到画面变化，区域数: {len(diff_regions)}")

                    # V3 增量OCR：先用 recognize 逐区域做，再在全局做群聊增强 + senderV4
                    def _ocr_func(img):
                        return recognize(
                            img,
                            scale=ocr_scale,
                            min_confidence=ocr_min_conf,
                            merge_bubble=ocr_merge,
                            denoise=ocr_denoise,
                        )

                    ocr_results_raw = self.smart_monitor.incremental_ocr(image, diff_regions, _ocr_func)
                    self.stats["ocr_calls"] += 1
                    # 增量模式下做 sender 强化 + 群聊轻量增强（不做多策略完整识别）
                    if ocr_results_raw:
                        ocr_results_raw = identify_senders_v4(ocr_results_raw, image)
                    # 最终：包装成统一格式
                    from ocr_engine import (
                        infer_chat_kind_by_title, _detect_chat_kind_hints,
                        _group_detect_and_strip_member_name,
                    )
                    ih, iw = (0, 0) if image is None else image.shape[:2]
                    title_kind = infer_chat_kind_by_title(contact_name)
                    hint_res = _detect_chat_kind_hints(ocr_results_raw, ih, iw)
                    ocr_results, has_member, members = _group_detect_and_strip_member_name(
                        ocr_results_raw, image_h=ih,
                    )
                    # 简单融合：标题或结构命中 group 就算群聊
                    vote_g = 0
                    if title_kind == "group":
                        vote_g += 0.85
                    if hint_res["kind"] == "group":
                        vote_g += hint_res["confidence"]
                    if has_member:
                        vote_g += min(0.95, 0.55 + 0.08 * len(members))
                    final_kind = "group" if vote_g >= 0.5 else \
                        ("personal" if title_kind == "personal" else "unknown")
                    group_members = list(members)
                    for r in ocr_results:
                        r["chat_kind"] = final_kind
                    # P1 修复：增量（前台）路径算出的群聊类型必须回写全局状态，
                    # 否则 current_chat_kind 永远是初始标题判定值，前台模式群聊判定不生效
                    if final_kind == "group":
                        current_chat_kind = "group"
                        current_group_members.update(group_members)
                    elif final_kind == "personal":
                        current_chat_kind = "personal"
                else:
                    # 最小化模式 或 无增量检测：使用V3完整识别（含群聊/成员+senderV4）
                    if _is_min:
                        self._log("info", "[最小化] 跳过增量检测，直接全量OCR(V3群聊增强)")
                    enhanced = recognize_with_group_enhance(
                        image,
                        contact_title=contact_name,
                        scale=ocr_scale,
                        min_confidence=ocr_min_conf,
                        denoise=ocr_denoise,
                    )
                    self.stats["ocr_calls"] += 1
                    ocr_results = enhanced["lines"]
                    final_kind = enhanced["chat_kind"]
                    group_members = list(enhanced.get("group_members", []))
                    # 日志：群聊判定过程
                    if final_kind != getattr(self, "_last_reported_kind", None) or \
                       (group_members and abs(time.time() - getattr(self, "_last_member_log_ts", 0)) > 30):
                        self._last_reported_kind = final_kind
                        self._last_member_log_ts = time.time()
                        self._log("info", f"[V3] 会话类型={final_kind} "
                                  f"(置信度={enhanced.get('chat_kind_confidence','?')}) "
                                  f"成员数={len(group_members)}  成员样本={group_members[:6]}")
                    if final_kind == "group":
                        current_chat_kind = "group"
                        current_group_members.update(group_members)
                    elif final_kind == "personal":
                        current_chat_kind = "personal"

                if not ocr_results:
                    self._flush_stable_candidates(contact_name)
                    self._log("warning", "OCR未识别到文字（截图尺寸=%dx%d, 均值=%.1f）" % (
                        image.shape[1], image.shape[0], image.mean()) if image is not None else "OCR无结果")
                    time.sleep(poll_interval)
                    continue

                self._log("info", f"OCR识别到 {len(ocr_results)} 条文本")
                for i, r in enumerate(ocr_results[:5]):
                    txt = r.get("text", "")[:30] if isinstance(r, dict) else str(r)[:30]
                    self._log("info", f"  [{i}] {txt}")

                # === 诊断：OCR后sender分布（限频10秒）+ 群聊字段 ===
                _now_diag = time.time()
                if _now_diag - getattr(self, "_last_ocr_diag", 0) > 10:
                    self._last_ocr_diag = _now_diag
                    _me_cnt = sum(1 for r in ocr_results if isinstance(r, dict) and r.get("sender") == "me")
                    _oth_cnt = len(ocr_results) - _me_cnt
                    _gm_cnt = sum(1 for r in ocr_results if isinstance(r, dict) and r.get("group_member"))
                    self._log("info", f"[诊断] OCR分布: me={_me_cnt}, other={_oth_cnt}, "
                              f"带群成员字段={_gm_cnt}, 会话类型={current_chat_kind}, "
                              f"图尺寸={image.shape[1]}x{image.shape[0]}")

                # 3. 识别发送者 — 注意：V3 recognize_with_group_enhance / 增量分支 已跑过
                # identify_senders_v4，且在 ocr_results 里带了 sender_confidence。
                # 这里只在 sender 字段缺失时做兜底补一次 V3 判断。
                need_backfill = any(r.get("sender") is None for r in ocr_results if isinstance(r, dict))
                if need_backfill:
                    try:
                        ocr_results = identify_senders(ocr_results, image)
                    except Exception:
                        pass

                # ★ 保守兜底：与红点路径一致，用气泡水平位置强制修正发送者，
                #   避免自己发的右侧消息被误判为对方（微信自己消息在右侧绿色气泡）。
                #   极端靠右(>70%)→me；极端靠左(<30%)→other；其余保留 OCR 判断。
                try:
                    _img_w = image.shape[1] if image is not None else 800
                    for _r in ocr_results:
                        if not isinstance(_r, dict):
                            continue
                        _xc = _r.get("x_center", 0)
                        if _xc > _img_w * 0.70:
                            _r["sender"] = "me"
                        elif _xc < _img_w * 0.30:
                            _r["sender"] = "other"
                except Exception:
                    pass

                # ★ ② 位置桶去抖：同位置桶+文本相似度>=0.9 的 OCR 行合并（保留置信高者），
                #   抑制"同一消息每帧OCR抖动->近似重复"（dedup 的补充，按位置维度更准）
                ocr_results = self._dedup_ocr_by_position(ocr_results)

                # 4. 解析新消息
                result = self.parser.feed(ocr_results)
                new_messages = result["new_messages"]
                # === 诊断：parser结果 ===
                if not new_messages and ocr_results:
                    _last_grp = self.parser.candidate_pool
                    _proc = len(self.parser.processed_hashes)
                    _grp_cnt = 0
                    try:
                        groups = self.parser._group_by_bubble(ocr_results)
                        _grp_cnt = len(groups)
                        if groups:
                            _ls = self.parser._get_bubble_sender(groups[-1])
                            _lt = self.parser._get_bubble_text(groups[-1])[:30]
                        else:
                            _ls, _lt = "N/A", "N/A"
                    except Exception:
                        _ls, _lt = "err", "err"
                    self._log("info", f"[parser] OCR有{len(ocr_results)}条但新消息0条 "
                               f"(候选池={len(_last_grp)}, 已处理={_proc}, "
                               f"分组={_grp_cnt}, 末组发送者={_ls}, 末组文本={_lt})")
                # pHash去重（比MD5更鲁棒）—— V2: 包含群成员一起做 key
                if self.smart_monitor:
                    deduped = []
                    for msg in new_messages:
                        gm = msg.get("group_member") or ""
                        dedup_key = f"{msg.get('sender','?')}|{gm}|{msg.get('content','')}"
                        if not self.smart_monitor.deduplicate(dedup_key):
                            deduped.append(msg)
                    new_messages = deduped
                context = result["context"]

                for msg in new_messages:
                    content = str(msg.get("content", "")).strip()
                    # 剥离 OCR 混进正文的时间戳前缀（9:57 / 星期三07:50 / 7/18 等）
                    content = self._strip_timestamp_prefix(content)
                    if not content:
                        continue
                    _sender = msg.get("sender", "other")
                    _group_member = msg.get("group_member") or None
                    _conf = msg.get("confidence")
                    _sender_conf = msg.get("sender_confidence")

                    # V2: 自己发的消息也要识别（进入 UI / 存储 / Obsidian），
                    #    但是不触发自动回复。不再使用 "if sender==me: continue"。
                    # 过滤自身UI窗口的OCR误识别内容（只过滤对方：自己的内容是真实消息）
                    if _sender == "other" and any(skip in content for skip in
                            ["助手v2.0", "AI助手", "微信 AI", "信息提取", "自动回复",
                             "数据查看", "设置", "红点", "屏幕外", "保活", "截图",
                             "预览", "诊断", "增量", "窗口坐标"]):
                        continue
                    # 过滤过短的无效内容（对方<2字跳过；自己的单字"嗯""哦"要保留）
                    import re
                    if _sender == "other" and len(content) < 2:
                        continue
                    # 过滤纯时间戳/系统提示
                    if re.match(r'^\d{1,2}[:：]\d{2}$', content):
                        continue
                    if re.match(r'^(昨天|今天|明天|后天|星期[一二三四五六日天]|周[一二三四五六日天])$', content):
                        continue
                    if _sender == "other" and re.match(r'^\d+$', content):
                        continue
                    if any(sw in content for sw in self.SYSTEM_MSG_MARKERS):
                        continue
                    if re.match(r'^[\s\.\,\，\。\！\？\!\?\-\_\(\)\(\)]+$', content):
                        continue

                    # 群聊场景：如果 group_member 为空，但会话类型是 group，
                    # 则尝试 fallback：私聊 contact 作为 group_member（避免混淆）
                    if current_chat_kind == "group" and not _group_member and _sender == "other":
                        # 保持为空 — UI上显示为"未知成员"
                        pass

                    self.stats["messages_detected"] += 1
                    if current_chat_kind == "group" and _group_member:
                        self._log("info",
                            f"[新消息][群聊] {contact_name} > {_group_member}({_sender}): {content[:80]}")
                    else:
                        who = "我" if _sender == "me" else contact_name
                        self._log("info", f"[新消息] {who}({_sender}): {content[:80]}")

                    # 实时通知UI：V3 多传 chat_kind / group_member / 置信度
                    _ts = datetime.now().strftime("%H:%M:%S")
                    _mk = hashlib.md5(
                        f"{contact_name}|{_group_member or ''}|{_sender}|{content}".encode("utf-8")
                    ).hexdigest()[:14]
                    self._send_ui_card(
                        contact_name, _sender, content, _ts,
                        msg_key=_mk,
                        is_group=(current_chat_kind == "group"),
                        group_member=_group_member,
                        confidence=_conf,
                        sender_confidence=_sender_conf,
                    )
                    self._log("info", f"[主循环->UI] 发送: {contact_name} | sender={_sender}"
                              f" | member={_group_member} | {content[:40]}")

                    # === 信息提取（对方消息做"提取+重要判定"；自己消息也存但不做AI回复） ===
                    if self.extractor:
                        extracted = self.extractor.extract(
                            text=content,
                            sender=_sender,
                            contact_name=contact_name,
                            timestamp=datetime.now(),
                            extra={"chat_kind": current_chat_kind,
                                   "group_member": _group_member},
                        )
                        # 覆盖/补充提取器没有赋值的结构化字段
                        extracted["chat_kind"] = current_chat_kind
                        extracted["group_member"] = _group_member
                        extracted["sender_confidence"] = _sender_conf
                        extracted["ocr_confidence"] = _conf

                        self.stats["extracted"] += 1

                        if extracted.get("is_important"):
                            self.stats["important"] += 1
                            self._log("warning",
                                f"[重要] {contact_name}: {extracted.get('importance_reason', '')}")

                        # 富信息更新卡：关键词/摘要/分类，原位刷新不重复
                        self._send_ui_card(
                            contact_name, _sender, content, _ts,
                            extracted=extracted,
                            is_important=extracted.get("is_important", False),
                            importance_reason=extracted.get("importance_reason", ""),
                            is_group=(current_chat_kind == "group"),
                            group_member=_group_member,
                            confidence=_conf,
                            sender_confidence=_sender_conf,
                            is_update=True,
                            msg_key=_mk,
                        )

                        self._store_extracted(extracted)

                        # Obsidian同步（V4：结构化 Frontmatter + 双向链接 + 缓冲写入）
                        if self.obsidian and self.obsidian.enabled:
                            _llm = extracted.get("llm_analysis", {}) or {}
                            _llm = _llm if isinstance(_llm, dict) else {}
                            sync_data = {
                                "contact": contact_name,
                                "sender": extracted.get("sender", _sender),
                                "content": extracted.get("raw_text", content),
                                "timestamp": extracted.get("timestamp", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
                                "is_important": extracted.get("is_important", False),
                                "importance_reason": extracted.get("importance_reason", ""),
                                "keywords": extracted.get("matched_keywords", []) or extracted.get("keywords", []),
                                "summary": _llm.get("summary", ""),
                                "is_group": (current_chat_kind == "group"),
                                "group_member": _group_member,
                                "chat_kind": current_chat_kind,
                                "emotion": _llm.get("emotion", ""),
                                "category": _llm.get("category", "") or extracted.get("classification", ""),
                                "urgency": _llm.get("urgency", 0),
                                "extracted_tasks": _llm.get("tasks", []) or extracted.get("extracted_tasks", []),
                                "extracted_fields": {
                                    "emotion": _llm.get("emotion", ""),
                                    "category": _llm.get("category", "") or extracted.get("classification", ""),
                                    "urgency": _llm.get("urgency", 0),
                                    "is_ad": _llm.get("is_ad", False),
                                    "negative_emotion": _llm.get("negative_emotion", False),
                                    "tags": _llm.get("tags", []),
                                },
                                "ocr_confidence": _conf,
                                "sender_confidence": _sender_conf,
                            }
                            self.obsidian.sync_message(sync_data)

                        self._on_extract(extracted)

                    # === 自动回复（只对方消息触发；自己的绝对不回复，避免 AI 复读自己） ===
                    # ★ OCR 乱码护栏：对方消息判为乱码时不触发自动回复（仍存库/上UI卡）
                    _garbled = False
                    try:
                        from ocr_engine import looks_garbled
                        _garbled = looks_garbled(content)
                    except Exception:
                        _garbled = False
                    if _garbled and _sender == "other":
                        self._log("warning",
                            f"[轮询][乱码护栏] {contact_name}: 疑似OCR乱码'{content[:20]}'，"
                            f"只记录不触发自动回复")
                    if not _garbled:
                        self._maybe_auto_reply(contact_name, content, _sender, context,
                                               is_group=(current_chat_kind == "group"))

                self._on_stats()

                # 5. 多联系人扫描（轮询模式：识别完一个就切下一个）
                if self.scanner and self.scanner.should_scan():
                    self._log("info", f"[轮询] 切换到下一个联系人 (第{self.scanner._current_index + 1}个)...")
                    new_contact, new_window = self.scanner.scan_next(self.window)
                    if new_contact and new_window:
                        # 排除UI窗口
                        title = new_window.title or ""
                        if "AI" in title or "助手" in title:
                            self._log("warning", "[轮询] 跳过UI窗口，尝试下一个")
                            continue
                        self.window = new_window
                        contact_name = new_contact
                        self.parser = MessageParser(
                            stable_frames=self.wechat_config.get("stable_frames", 1),
                            context_size=self.wechat_config.get("context_size", 20),
                        )
                        self._log("info", f"[轮询] 已切换到: {contact_name}")
                        # 切换后立即截图提取一次
                        time.sleep(0.5)
                        # 主循环扫描也优先PrintWindow（避免mss截到程序UI）
                        _scan_hwnd = getattr(self.window, "_hWnd", None)
                        if _scan_hwnd and self.minimize_mode == "offscreen":
                            from screenshot import capture_via_printwindow as _cpw2
                            _scan_full = _cpw2(_scan_hwnd)
                            if _scan_full is not None and _scan_full.mean() >= 5 and _scan_full.shape[1] >= 300:
                                _sh, _sw = _scan_full.shape[:2]
                                _sbh = int(_sh * capture_ratio)
                                scan_image = _scan_full[_sh - _sbh:, :]
                            else:
                                scan_image = capture_chat_bottom(self.window, ratio=capture_ratio)
                        else:
                            scan_image = capture_chat_bottom(self.window, ratio=capture_ratio)
                        if scan_image is not None and not is_image_blank(scan_image):
                            scan_ocr = recognize(scan_image, scale=ocr_scale,
                                                  min_confidence=ocr_min_conf,
                                                  merge_bubble=False, denoise=False)
                            if scan_ocr:
                                scan_ocr = identify_senders_v4(scan_ocr, scan_image)
                                scan_result = self.parser.feed(scan_ocr)
                                scan_msgs = scan_result["new_messages"]
                                self._log("info", f"[轮询] {contact_name}: 识别到 {len(scan_msgs)} 条新消息")
                                for msg in scan_msgs:
                                    self.stats["messages_detected"] += 1
                                    if self.extractor:
                                        extracted = self.extractor.extract(
                                            text=msg["content"],
                                            sender=msg.get("sender", "other"),
                                            contact_name=contact_name,
                                            timestamp=datetime.now(),
                                        )
                                        self.stats["extracted"] += 1
                                        if extracted.get("is_important"):
                                            self.stats["important"] += 1
                                        self._store_extracted(extracted)
                                        self._on_extract(extracted)
                    else:
                        self._log("warning", "[轮询] 切换失败，可能已到列表底部")

                # 6. 控制循环频率
                elapsed = time.time() - loop_start
                sleep_time = max(0.1, poll_interval - elapsed)
                time.sleep(sleep_time)

            except Exception as e:
                self._log("error", f"主循环异常: {e}")
                time.sleep(poll_interval)

        self._log("info", "监控已停止")
        runtime = datetime.now() - self.stats["start_time"]
        self._log("info", f"运行时间: {runtime}")
        self._log("info", f"截图: {self.stats['frames_captured']}, "
                          f"OCR: {self.stats['ocr_calls']}, "
                          f"消息: {self.stats['messages_detected']}, "
                          f"提取: {self.stats['extracted']}, "
                          f"重要: {self.stats['important']}, "
                          f"回复: {self.stats['replies_sent']}")
        if self.smart_monitor:
            sm_stats = self.smart_monitor.get_stats()
            self._log("info", f"增量检测: 检查{sm_stats['frames_checked']}帧, "
                       f"跳过{sm_stats['frames_skipped_no_change'] + sm_stats['frames_skipped_bottom_same']}帧({sm_stats['skip_rate']}), "
                       f"增量OCR:{sm_stats['ocr_calls_incremental']}, 缓存命中:{sm_stats['ocr_calls_saved']}, "
                       f"去重:{sm_stats['messages_deduplicated']}条")

    def _check_fast_mode(self):
        """快速模式：检测窗口标题变化判断是否有新消息"""
        import re
        now = time.time()
        if now - self._last_title_check < 1.0:
            return False

        self._last_title_check = now
        try:
            title = self.window.title or ""
            # 检测标题中的未读数：微信(3) 或 微信（3）
            match = re.search(r'[(\（](\d+)[)\）]', title)
            if match:
                unread_count = int(match.group(1))
                if unread_count > 0:
                    self._log("info", f"[快速] 检测到 {unread_count} 条未读消息")
                    return True
        except Exception:
            pass
        return False

    def _check_do_not_disturb(self):
        """勿扰模式：只在用户正在输入时暂停红点切换（不堵住OCR）"""
        if not self.dnd_enabled:
            return False

        try:
            import pyautogui
            current_pos = pyautogui.position()

            # 只检测鼠标是否在输入框区域（微信底部20%）
            wx = self.window
            input_zone_top = wx.top + int(wx.height * 0.8)
            if (wx.left <= current_pos[0] <= wx.left + wx.width and
                input_zone_top <= current_pos[1] <= wx.top + wx.height):
                # 鼠标在输入框区域，可能正在打字
                self._last_keyboard_time = time.time()
                return True

            # 5秒内有过输入操作
            if time.time() - self._last_keyboard_time < 5:
                return True
        except Exception:
            pass

        return False

    def _is_wechat_foreground(self):
        """判断微信主窗口当前是否处于前台（用户正在使用）。"""
        try:
            import win32gui
            _hwnd = getattr(self.window, "_hWnd", None)
            if not _hwnd:
                return False
            _fg = win32gui.GetForegroundWindow()
            return _fg == _hwnd
        except Exception:
            return False

    def _handle_unread_contacts(self, unread_contacts, original_contact):
        """处理有未读消息的联系人：切换 -> 截图 -> OCR -> 提取 -> 标记已处理"""
        import pyautogui
        from message_parser import MessageParser

        if not hasattr(self, '_click_retry_counts'):
            self._click_retry_counts = {}
        if not hasattr(self, '_last_click_ts'):
            self._last_click_ts = 0.0
        # V3.4 屏障：本轮回合内产生的自动回复线程（粘贴前需先 join，防止切回原窗口后粘贴错人）
        self._pending_reply_threads = []
        MAX_CLICK_RETRIES = 3
        # 行为限速：两次成功点击之间保留拟人间隔，避免机械秒点（防风控+体验）
        MIN_CLICK_GAP = 1.5

        _was_offscreen = False
        if self.minimize_mode == "offscreen":
            try:
                from window_manager import is_window_offscreen
                if is_window_offscreen(self.window):
                    _was_offscreen = True
                    self._log("info", "[屏幕外] 后台模式处理未读，不闪现窗口")
            except Exception:
                pass

        # ★ 最小化时点击未读"不准确"根因：最小化窗口 GPU 停渲染 → PrintWindow 全黑
        #   → 即使 SendMessageW 命中也看不出是否切到了正确联系人。
        #   修复：offscreen 模式下，处理未读前先把窗口自愈到"屏幕外可见"可渲染后台态，
        #   然后重新判断 _was_offscreen（后续点击分支据此选 SendMessageW 或物理点击）。
        if self.minimize_mode == "offscreen":
            hwnd_pre = getattr(self.window, "_hWnd", None)
            if hwnd_pre:
                try:
                    from window_manager import (
                        _acquire_interaction, _release_interaction,
                        is_window_offscreen,
                    )
                    from screenshot import ensure_window_rendering
                    _acquire_interaction()
                    _rect = ensure_window_rendering(hwnd_pre, self.window)
                    if _rect is not None:
                        self.window.left, self.window.top = _rect[0], _rect[1]
                        self.window.width, self.window.height = _rect[2], _rect[3]
                    _was_offscreen = is_window_offscreen(self.window)
                    if _was_offscreen:
                        self._log("info", "[红点] 入口自愈完成，窗口在屏幕外 → 后台点击")
                    else:
                        self._log("warning", "[红点] 入口自愈后窗口仍在屏幕内")
                except Exception as e:
                    self._log("warning", f"[红点] 入口窗口自愈异常: {e}")
                finally:
                    try:
                        _release_interaction(self.window)
                    except Exception:
                        pass

        _processed_count = 0  # 实际处理（非跳过）的联系人数，用于日志口径
        for item in unread_contacts:
            if self._stop_flag.is_set():
                break

            contact = item["contact"]

            # 白名单/黑名单过滤
            whitelist = self.role_manager.config.get("contacts_filter", {}).get("whitelist", [])
            blacklist = self.role_manager.config.get("contacts_filter", {}).get("blacklist", [])

            all_blacklist = list(blacklist)

            # 检查黑名单（支持通配符*）
            skip = False
            for bl in all_blacklist:
                if "*" in bl:
                    pattern = bl.replace("*", "")
                    if pattern in contact:
                        skip = True
                        break
                elif bl in contact:
                    skip = True
                    break
            if skip:
                self._log("info", f"[红点] 跳过黑名单联系人: {contact}")
                self.red_dot_monitor.mark_processed(contact)
                continue

            # 如果有白名单，只处理白名单中的联系人
            if whitelist:
                in_whitelist = False
                for wl in whitelist:
                    if "*" in wl:
                        pattern = wl.replace("*", "")
                        if pattern in contact:
                            in_whitelist = True
                            break
                    elif wl in contact:
                        in_whitelist = True
                        break
                if not in_whitelist:
                    self._log("info", f"[红点] 不在白名单，跳过: {contact}")
                    self.red_dot_monitor.mark_processed(contact)
                    continue

            unread_count = item.get("unread_count", "?")
            method = item.get("method", "hsv")
            self._log("info", f"[红点] 切换到 {contact} (未读:{unread_count}, 方式:{method})")

            # ★ 点击前快照：保存侧边栏 + 聊天区截图供像素级验证
            _before_sidebar = None
            _before_chat = None
            _red_dot_info = {
                "center_x": item.get("red_dot_x", 0),
                "center_y": item.get("red_dot_y", 0),
                "w": item.get("red_dot_w", 10),
                "h": item.get("red_dot_h", 10),
            }
            if self._click_optimizer is not None and self.red_dot_monitor is not None:
                try:
                    _before_sidebar = self.red_dot_monitor.capture_sidebar(self.window)
                    # 截取当前聊天区供直方图对比
                    from screenshot import capture_via_printwindow_stable, crop_chat_region_img
                    _hwnd = getattr(self.window, "_hWnd", None)
                    if _hwnd:
                        _full = capture_via_printwindow_stable(_hwnd)
                        if _full is not None and _full.mean() > 5:
                            _before_chat = crop_chat_region_img(
                                _full, bottom_ratio=self.wechat_config.get("capture_ratio_full", 0.85))
                except Exception:
                    pass

            hwnd = getattr(self.window, "_hWnd", None)
            # 点击前节流：距上次成功点击过近则补足间隔并加随机抖动
            _gap = time.time() - self._last_click_ts
            if _gap < MIN_CLICK_GAP:
                time.sleep(MIN_CLICK_GAP - _gap + random.uniform(0.0, 0.9))

            # ★ 自适应点击：失败时自动微调 Y 偏移而非固定重试
            _click_attempt = self._click_retry_counts.get(contact, 0)
            try:
                if hwnd and _was_offscreen:
                    from window_manager import edge_click_window
                    # 自适应 Y：优先红点Y，失败时自动偏移
                    _base_y = item.get("red_dot_y", item.get("name_y"))
                    if self._click_optimizer is not None:
                        _click_y = self._click_optimizer.get_adaptive_click_y(
                            _base_y, item.get("name_y", _base_y), _click_attempt)
                    else:
                        _click_y = _base_y
                    click_cx, click_cy = self.red_dot_monitor.get_click_client_position(
                        self.window, _click_y, None
                    )
                    self._log("info", f"[红点] 边缘点击: ({click_cx}, {click_cy})"
                              f" (尝试#{_click_attempt+1}, Y={_click_y})")
                    ok = edge_click_window(self.window, click_cx, click_cy)
                    if not ok:
                        self._click_retry_counts[contact] = _click_attempt + 1
                        retries = self._click_retry_counts[contact]
                        if retries >= MAX_CLICK_RETRIES:
                            self._log("warning", f"[红点] {contact} 点击重试{retries}次失败，强制标记已处理")
                            self.red_dot_monitor.mark_processed(contact)
                            self._click_retry_counts[contact] = 0
                            continue
                        self._log("warning", f"[红点] 点击失败 (重试{retries}/{MAX_CLICK_RETRIES})")
                        continue
                    self._log("info", "[红点] 后台点击完成（待验证切换）")
                else:
                    _base_y = item.get("red_dot_y", item.get("name_y"))
                    if self._click_optimizer is not None:
                        _click_y = self._click_optimizer.get_adaptive_click_y(
                            _base_y, item.get("name_y", _base_y), _click_attempt)
                    else:
                        _click_y = _base_y
                    click_cx, click_cy = self.red_dot_monitor.get_click_client_position(
                        self.window, _click_y, None
                    )
                    self._log("info", f"[红点] 模拟点击(无鼠标移动)坐标: ({click_cx}, {click_cy})"
                              f" (尝试#{_click_attempt+1}, Y={_click_y})")
                    from window_manager import simulate_click_window
                    ok = simulate_click_window(self.window, click_cx, click_cy)
                    if not ok:
                        self._log("warning", "[红点] SendMessageW 模拟点击失败，退回物理点击")
                        click_x, click_y = self.red_dot_monitor.get_click_position(
                            self.window, _click_y, None
                        )
                        self._physical_click(click_x, click_y)
                    self._log("info", "[红点] 模拟点击完成（待验证切换）")
            except Exception as e:
                self._log("error", f"[红点] 点击失败: {e}")
                self._click_retry_counts[contact] = self._click_retry_counts.get(contact, 0) + 1
                retries = self._click_retry_counts.get(contact, 0)
                if retries >= MAX_CLICK_RETRIES:
                    self.red_dot_monitor.mark_processed(contact)
                    self._click_retry_counts[contact] = 0
                    self._log("warning", f"[红点] {contact} 点击重试{retries}次失败，强制跳过")
                continue

            # === P0 点击后验证闭环（三阶段快速验证） ===
            time.sleep(0.6)  # 等微信切换会话 + 红点消失动画

            _verified = False
            _verify_stage = "full_ocr"

            # ★ 阶段1+2：像素级验证 + 直方图变化检测（0.3s，快于全OCR重扫）
            if (self._click_optimizer is not None and
                    _before_sidebar is not None and
                    self.red_dot_monitor is not None):
                try:
                    _after_sidebar = self.red_dot_monitor.capture_sidebar(self.window)
                    # 阶段1：像素级红点验证
                    _pixel_ok, _pixel_conf = self._click_optimizer.verify_red_dot_gone(
                        _before_sidebar, _after_sidebar, _red_dot_info)
                    if _pixel_ok and _pixel_conf > 0.6:
                        _verified = True
                        _verify_stage = "pixel"
                        self._log("info", f"[验证] {contact} 像素验证通过(置信度={_pixel_conf:.2f})")
                    elif _before_chat is not None:
                        # 阶段2：聊天区直方图变化检测
                        _hwnd = getattr(self.window, "_hWnd", None)
                        if _hwnd:
                            from screenshot import capture_via_printwindow_stable, crop_chat_region_img
                            _full = capture_via_printwindow_stable(_hwnd)
                            if _full is not None and _full.mean() > 5:
                                _after_chat = crop_chat_region_img(
                                    _full, bottom_ratio=self.wechat_config.get("capture_ratio_full", 0.85))
                                _chat_ok, _chat_sim = self._click_optimizer.verify_chat_area_changed(
                                    _before_chat, _after_chat)
                                if _chat_ok:
                                    _verified = True
                                    _verify_stage = "histogram"
                                    self._log("info", f"[验证] {contact} 直方图验证通过(相似度={_chat_sim:.2f})")
                except Exception as e:
                    self._log("debug", f"[验证] 快速验证异常: {e}")

            # 阶段3：全OCR验证（兜底）
            if not _verified:
                _verified = self._verify_contact_switched(contact)
                _verify_stage = "full_ocr"

            if not _verified:
                self._click_retry_counts[contact] = self._click_retry_counts.get(contact, 0) + 1
                _retries = self._click_retry_counts[contact]
                if _retries >= MAX_CLICK_RETRIES:
                    self._log("warning", f"[红点] {contact} 点击验证失败{_retries}次(阶段:{_verify_stage})，强制放弃")
                    self.red_dot_monitor.mark_processed(contact)
                    self._click_retry_counts[contact] = 0
                else:
                    self._log("warning", f"[红点] {contact} 点击验证失败(阶段:{_verify_stage}) "
                              f"(重试{_retries}/{MAX_CLICK_RETRIES})")
                continue

            # === P0+ 标题栏 OCR 双重验证：确认真的切到了目标会话（而非切错人） ===
            _name_ok = self._verify_contact_name(contact)
            if not _name_ok:
                # 切错人：立即放弃本轮，不处理消息、不标记，并把联系人标记为嫌疑
                # （连点 2 次都切错 → 进冷却，本会话内不再点，根治反复误点已读/误识别）
                self._click_retry_counts[contact] = self._click_retry_counts.get(contact, 0) + 1
                _bad = self._click_retry_counts.get(contact, 0)
                if _bad >= 2:
                    self._log("warning", f"[红点] {contact} 连续{_bad}次切错人，标记嫌疑(本会话不再点)")
                    if hasattr(self.red_dot_monitor, "mark_suspect"):
                        self.red_dot_monitor.mark_suspect(contact)
                    self._click_retry_counts[contact] = 0
                # 注：本函数末尾(2861段)会统一切回原窗口，这里 continue 后由主循环收尾处理
                continue

            # 验证成功 → 记录本次成功点击时间戳（供下一次点击节流用）
            self._last_click_ts = time.time()

            time.sleep(random.uniform(2.2, 4.5))  # 拟人窗口加载等待（随机区间，避免机械定长）

            # 重新获取当前会话真实名称：窗口标题(微信4.x恒为"微信")→顶部标题栏OCR→红点匹配名
            # ★ 关键修复：红点匹配名(item["contact"])是点击前就从侧边栏确定的真实名，
            #   远比切换后顶部OCR可靠（顶部OCR常只截到群名首字/把图标误识为?）。
            #   若顶部OCR取到空/噪声，必须回退到 item["contact"]，绝不能用 ? 覆盖。
            _reddot_name = item.get("contact", "")
            new_contact = self._resolve_contact_name(
                getattr(self.window, "_hWnd", None), _reddot_name)
            # 顶部OCR若返回空或单字噪声(如 ?/一)，回退红点匹配名
            if not new_contact or len(new_contact) < 2 or new_contact == "?":
                new_contact = _reddot_name
            self._remember_contact_name(new_contact)
            if not new_contact:
                new_contact = _reddot_name or "未命名会话"
            self._log("info", f"[红点] 已切换到: {new_contact}")

            if self.smart_monitor:
                self.smart_monitor.reset()
            # 不再使用MessageParser（stable_frames机制不适合单次截图场景）

            # 截图：优先用 PrintWindow（避免mss截到程序UI），flags=3对Qt微信有效
            full_ratio = self.wechat_config.get("capture_ratio_full", 0.85)
            hwnd = getattr(self.window, "_hWnd", None)
            if hwnd:
                from screenshot import capture_via_printwindow_stable
                _full_img = capture_via_printwindow_stable(hwnd)
                if _full_img is not None and _full_img.mean() >= 5 and _full_img.shape[1] >= 300:
                    from screenshot import crop_chat_region_img
                    image = crop_chat_region_img(_full_img, bottom_ratio=full_ratio)
                    self._log("info", f"[红点] {new_contact} PrintWindow截图成功: {_full_img.shape[1]}x{_full_img.shape[0]} → 聊天区")
                else:
                    self._log("warning", f"[红点] {new_contact} PrintWindow无效，回退mss")
                    image = capture_chat_bottom(self.window, ratio=full_ratio)
            else:
                image = capture_chat_bottom(self.window, ratio=full_ratio)
            if image is None or is_image_blank(image):
                self._log("warning", f"[红点] {new_contact} 截图为空，疑似切换未生效 → 重试点击")
                self._click_retry_counts[contact] = self._click_retry_counts.get(contact, 0) + 1
                retries = self._click_retry_counts.get(contact, 0)
                if retries >= MAX_CLICK_RETRIES:
                    self._log("warning", f"[红点] {contact} 切换重试{retries}次仍为空，强制标记已处理")
                    self.red_dot_monitor.mark_processed(contact)
                    self._click_retry_counts[contact] = 0
                continue

            # ★ 识别期间推送预览（当前聊天截图），避免预览一直"等待截图"
            try:
                self._on_capture(image)
            except Exception:
                pass

            # OCR识别
            ocr_scale = self.wechat_config.get("ocr_scale", 1.0)
            ocr_min_conf = self.wechat_config.get("ocr_min_confidence", 0.40)
            # ★ 多行气泡合并：红点路径原 hard-coded merge_bubble=False，
            #   导致对面发的 5~6 行长消息被切成多条 → LLM 拿到碎片 → 回复乱。
            #   改为读配置 ocr_merge_bubble（默认 True），与轮询路径一致。
            ocr_merge = self.wechat_config.get("ocr_merge_bubble", True)
            ocr_results = recognize(
                image,
                scale=ocr_scale,
                min_confidence=ocr_min_conf,
                merge_bubble=ocr_merge,
                denoise=False,
            )

            # 如果第一屏没识别到，尝试滚动向上截取更多
            scroll_attempts = 0
            max_scroll = 3
            all_ocr_results = list(ocr_results) if ocr_results else []

            while scroll_attempts < max_scroll:
                if not ocr_results or len(ocr_results) < 3:
                    # 向上滚动
                    try:
                        hwnd_scroll = getattr(self.window, "_hWnd", None)
                        if hwnd_scroll and _was_offscreen:
                            from window_manager import edge_scroll_window
                            win_w = self.window.width
                            win_h = self.window.height
                            scroll_cx = int(win_w * 0.55)
                            scroll_cy = int(win_h * 0.55)
                            edge_scroll_window(self.window, scroll_cx, scroll_cy, delta=3)
                        else:
                            import pyautogui
                            win_x = self.window.left
                            win_y = self.window.top
                            win_w = self.window.width
                            win_h = self.window.height
                            chat_cx = win_x + int(win_w * 0.55)
                            chat_cy = win_y + int(win_h * 0.55)
                            pyautogui.scroll(5, chat_cx, chat_cy)  # 向上滚动
                        time.sleep(0.5)
                        scroll_attempts += 1
                        self._log("info", f"[红点] {new_contact} 向上滚动({scroll_attempts}/{max_scroll})")
                        # 滚动后截图也优先PrintWindow
                        _hwnd2 = getattr(self.window, "_hWnd", None)
                        if _hwnd2:
                            from screenshot import capture_via_printwindow as _cpw
                            _full2 = _cpw(_hwnd2)
                            if _full2 is not None and _full2.mean() >= 5:
                                _h2, _w2 = _full2.shape[:2]
                                _bh2 = int(_h2 * full_ratio)
                                image2 = _full2[_h2 - _bh2:, :]
                            else:
                                image2 = capture_chat_bottom(self.window, ratio=full_ratio)
                        else:
                            image2 = capture_chat_bottom(self.window, ratio=full_ratio)
                        if image2 is not None and not is_image_blank(image2):
                            ocr_results2 = recognize(
                                image2, scale=ocr_scale, min_confidence=ocr_min_conf,
                                merge_bubble=ocr_merge, denoise=False,
                            )
                            if ocr_results2:
                                all_ocr_results.extend(ocr_results2)
                    except Exception:
                        break
                else:
                    break

            if not all_ocr_results:
                self._log("warning", f"[红点] {new_contact} 未识别到文字，疑似切换未生效 → 重试点击")
                self._click_retry_counts[contact] = self._click_retry_counts.get(contact, 0) + 1
                retries = self._click_retry_counts.get(contact, 0)
                if retries >= MAX_CLICK_RETRIES:
                    self._log("warning", f"[红点] {contact} 重试{retries}次仍无文字，强制标记已处理")
                    self.red_dot_monitor.mark_processed(contact)
                    self._click_retry_counts[contact] = 0
                continue

            # 发送者判断（P2：升级为 V4，强化自己消息识别，避免自己发的消息被当成对方新消息）
            all_ocr_results = identify_senders_v4(all_ocr_results, image)

            # 保守兜底：只在极端位置强制修正（屏幕外截图比例正常）
            img_w = image.shape[1] if image is not None else 800
            for r in all_ocr_results:
                x_center = r.get("x_center", 0)
                # 极端靠右 (>70%) 强制为 me
                if x_center > img_w * 0.70:
                    r["sender"] = "me"
                # 极端靠左 (<30%) 强制为 other
                elif x_center < img_w * 0.30:
                    r["sender"] = "other"
                # 其他情况保留 identify_senders 的判断

            # 去重（按文本内容）
            seen_texts = set()
            unique_results = []
            for r in all_ocr_results:
                t = str(r.get("text", "")).strip()
                if t and t not in seen_texts:
                    seen_texts.add(t)
                    unique_results.append(r)

            self._log("info", f"[红点] {new_contact}: 共识别 {len(unique_results)} 条文本")

            # 统计本轮"新消息/跨轮去重"条数：若点击后没有任何新消息(全部跨轮去重)，
            # 说明红点大概率假性命中或早已读过 → 标记嫌疑，本会话内不再点击（根治误点已读）。
            _new_count = 0
            _dup_count = 0

            # AI训练引擎辅助识别新消息
             # 二次黑名单检查：侧边栏OCR可能只提取部分联系人名，用聊天内容再检查
            all_text = " ".join(str(r.get("text", "")) for r in unique_results)
            for bl in all_blacklist:
                if bl in all_text:
                    self._log("info", f"[红点] 二次黑名单命中 '{bl}'，跳过: {new_contact}")
                    self.red_dot_monitor.mark_processed(contact)
                    skip = True
                    break
            if skip:
                continue
            # 前10次：AI看截图+OCR判断哪些是新消息；10次后：纯规则判断
            if self.ai_trainer:
                ai_result = self.ai_trainer.analyze(
                    ocr_results=unique_results,
                    screenshot=image,
                    context="chat"
                )
                new_messages = ai_result.get("new_messages", [])
                # 转换格式
                new_messages = [{"sender": m.get("sender", "other"), "content": str(m.get("text", ""))}
                                for m in new_messages if m.get("text")]
                progress = self.ai_trainer.get_progress()
                self._log("info", f"[红点] {new_contact}: AI/规则识别 {len(new_messages)} 条新消息 "
                          f"(模式: {progress['phase']}, 训练: {progress['current']}/{progress['threshold']})")
            else:
                # 没有AI训练引擎时，直接处理OCR结果
                new_messages = []
                for r in unique_results:
                    text = str(r.get("text", "")).strip()
                    if not text or len(text) < 1:
                        continue
                    sender = r.get("sender", "other")
                    # 自己消息也上报（与主循环一致：进UI/存储，但不触发自动回复）
                    new_messages.append({"sender": sender, "content": text})
                self._log("info", f"[红点] {new_contact}: {len(new_messages)} 条新消息")

            for msg in new_messages:
                content = str(msg.get("content", "")).strip()
                # 剥离 OCR 混进正文的时间戳前缀（9:57 / 星期三07:50 / 7/18 等）
                content = self._strip_timestamp_prefix(content)
                if not content:
                    continue
                _sender = msg.get("sender", "other")
                # 过滤自身UI窗口的OCR误识别内容
                if any(skip in content for skip in ["助手v2.0", "AI助手", "微信 AI", "信息提取", "自动回复", "数据查看", "设置", "红点", "屏幕外", "保活", "截图", "预览", "诊断", "增量", "窗口坐标"]):
                    continue
                # 过滤过短的无效内容（对方<2字跳过；自己的单字保留，与主循环一致）
                import re
                if _sender == "other" and len(content) < 2:
                    continue
                # 过滤纯时间戳（如 12:30, 14:32, 昨天, 星期六等）
                if re.match(r'^\d{1,2}[:：]\d{2}$', content):
                    continue
                if re.match(r'^(昨天|今天|明天|后天|星期[一二三四五六日天]|周[一二三四五六日天])$', content):
                    continue
                # 过滤纯数字（仅对方）
                if _sender == "other" and re.match(r'^\d+$', content):
                    continue
                # 过滤系统提示
                if any(sw in content for sw in self.SYSTEM_MSG_MARKERS):
                    continue
                # 过滤无意义OCR碎片（纯标点或特殊字符）
                if re.match(r'^[\s\.\,\，\。\！\？\!\?\-\_\(\)\(\)]+$', content):
                    continue

                # ★ 跨轮去重：红点路径每次点击重扫会把历史消息再报一遍 → 按 联系人|发送者|内容 去重
                try:
                    _rk = hashlib.md5(
                        f"{new_contact}|{_sender}|{content}".encode("utf-8")
                    ).hexdigest()
                    _seen_set = self._reddot_msg_seen.setdefault(new_contact, set())
                    if _rk in _seen_set:
                        self._log("info", f"[红点] 跨轮去重: {new_contact}: {content[:30]}")
                        _dup_count += 1
                        continue
                    _seen_set.add(_rk)
                    if len(_seen_set) > 200:
                        self._reddot_msg_seen[new_contact] = set(list(_seen_set)[-100:])
                except Exception:
                    _rk = f"{new_contact}_{_sender}_{content}"

                self.stats["messages_detected"] += 1
                _new_count += 1

                # 实时通知UI：先发基础卡（快速反馈），提取完成后用同一msg_key原位更新
                _ts = datetime.now().strftime("%H:%M:%S")
                self._send_ui_card(new_contact, _sender, content, _ts, msg_key=_rk)
                self._log("info", f"[红点->UI] 发送: {new_contact}: {content[:40]}")

                # 信息提取
                if self.extractor:
                    extracted = self.extractor.extract(
                        text=content,
                        sender=_sender,
                        contact_name=new_contact,
                        timestamp=datetime.now(),
                    )
                    self.stats["extracted"] += 1

                    if extracted.get("is_important"):
                        self.stats["important"] += 1
                        self._log("warning",
                            f"[红点][重要] {new_contact}: {extracted.get('importance_reason', '')}")

                    # 富信息更新卡：关键词/摘要/分类，原位刷新不重复
                    self._send_ui_card(
                        new_contact, _sender, content, _ts,
                        extracted=extracted,
                        is_important=extracted.get("is_important", False),
                        importance_reason=extracted.get("importance_reason", ""),
                        is_update=True,
                        msg_key=_rk,
                    )

                    self._store_extracted(extracted)

                    # ★ OCR 乱码护栏：对方消息若被判定为乱码（如"厕所没汰看"），
                    #   仍存库/上UI卡，但不触发自动回复——避免基于垃圾文本生成回复。
                    _garbled = False
                    try:
                        from ocr_engine import looks_garbled
                        _garbled = looks_garbled(content)
                    except Exception:
                        _garbled = False
                    if _garbled and _sender == "other":
                        self._log("warning",
                            f"[红点][乱码护栏] {new_contact}: 疑似OCR乱码'{content[:20]}'，"
                            f"只记录不触发自动回复")

                    # 自动回复（红点路径原本缺失 → 最小化模式下消息从不回复；己方消息上层自动拦截）
                    try:
                        from ocr_engine import infer_chat_kind_by_title
                        _rk_kind = infer_chat_kind_by_title(new_contact)
                    except Exception:
                        _rk_kind = "unknown"
                    if not _garbled:
                        self._maybe_auto_reply(new_contact, content, _sender,
                                               is_group=(_rk_kind == "group"))

                    # Obsidian同步（V4）
                    if self.obsidian and self.obsidian.enabled:
                        _llm = extracted.get("llm_analysis", {}) or {}
                        _llm = _llm if isinstance(_llm, dict) else {}
                        sync_data = {
                            "contact": new_contact,
                            "sender": extracted.get("sender", "other"),
                            "content": extracted.get("raw_text", ""),
                            "timestamp": extracted.get("timestamp", ""),
                            "is_important": extracted.get("is_important", False),
                            "importance_reason": extracted.get("importance_reason", ""),
                            "keywords": extracted.get("matched_keywords", []),
                            "summary": _llm.get("summary", ""),
                            "emotion": _llm.get("emotion", ""),
                            "category": _llm.get("category", "") or extracted.get("classification", ""),
                            "urgency": _llm.get("urgency", 0),
                            "extracted_tasks": _llm.get("tasks", []) or extracted.get("extracted_tasks", []),
                            "extracted_fields": {
                                "emotion": _llm.get("emotion", ""),
                                "category": _llm.get("category", "") or extracted.get("classification", ""),
                                "urgency": _llm.get("urgency", 0),
                                "is_ad": _llm.get("is_ad", False),
                                "negative_emotion": _llm.get("negative_emotion", False),
                                "tags": _llm.get("tags", []),
                            },
                        }
                        self.obsidian.sync_message(sync_data)

                    self._on_extract(extracted)

            # 标记已处理（真正处理成功才进冷却，并清零重试计数）
            self.red_dot_monitor.mark_processed(contact)
            self._click_retry_counts[contact] = 0

            # ★ 误点已读防护：本轮点击后若没有任何新消息(全部跨轮去重)，
            # 说明红点假性命中(红头像/红色UI)或早已读过 → 标记嫌疑，本会话内不再点击。
            # 仅当确实出现过消息(dup>0)才判嫌疑，避免把"OCR黑图/半渲染导致0条"误屏蔽。
            if _dup_count > 0 and _new_count == 0:
                self._log("warning",
                    f"[红点] {new_contact} 点击后 0 条新消息(全部跨轮去重{_dup_count}条) "
                    f"→ 疑似误点已读，本会话内不再点击")
                try:
                    self.red_dot_monitor.mark_suspect(new_contact)
                except Exception:
                    pass

        # ★ V3.4 屏障：切回原窗口前，先等本轮回合内所有自动回复线程完成粘贴。
        #   否则红点已切回原联系人，而回复线程还在粘贴 → 回复落错窗口（贴进原联系人输入框）。
        try:
            _pending = getattr(self, "_pending_reply_threads", []) or []
            for _t in _pending:
                try:
                    _t.join(timeout=30)
                except Exception:
                    pass
            self._pending_reply_threads = []
            self._log("info", f"[红点] 已等待 {len(_pending)} 个自动回复线程完成粘贴")
        except Exception:
            pass

        # 切回原窗口（点击侧边栏第一个或原始联系人）
        try:
            # 若 original_contact 本身是噪声(?/空/单字/未命名)，不强行在侧边栏找它
            # （会导致"未找到原联系人 ?"死循环），直接保持当前窗口即可。
            _orig_ok = (original_contact and len(original_contact) >= 2
                        and original_contact not in ("?", "微信", "未命名会话"))
            if original_contact and _orig_ok:
                self._log("info", f"[红点] 切回原窗口: {original_contact}")
                sidebar_img = self.red_dot_monitor.capture_sidebar(self.window)
                if sidebar_img is not None:
                    # 归一化匹配：去空格、转小写，兼容 OCR 把"亚磊"识成"亚磊 "等
                    _norm = lambda s: "".join(str(s).lower().split())
                    _orig_n = _norm(original_contact)
                    target_y = None
                    for _sc in (1.0, 2.0):  # 先 1.0，匹配不到再放大 2.0 重试
                        ocr_results = recognize(
                            sidebar_img,
                            scale=_sc,
                            min_confidence=0.40,
                            merge_bubble=False,
                            denoise=False,
                        )
                        for r in ocr_results:
                            text = _norm(r.get("text", ""))
                            if _orig_n and (_orig_n in text or text in _orig_n):
                                target_y = r.get("y_center", 0)
                                break
                        if target_y:
                            break
                    if target_y:
                        _hwnd3 = getattr(self.window, "_hWnd", None)
                        if _hwnd3 and _was_offscreen:
                            from window_manager import edge_click_window
                            click_cx, click_cy = self.red_dot_monitor.get_click_client_position(
                                self.window, int(target_y), None
                            )
                            edge_click_window(self.window, click_cx, click_cy)
                        else:
                            click_cx, click_cy = self.red_dot_monitor.get_click_client_position(
                                self.window, int(target_y), None
                            )
                            from window_manager import simulate_click_window
                            if not simulate_click_window(self.window, click_cx, click_cy):
                                self._log("warning", "[红点] SendMessageW 模拟点击失败，退回物理点击")
                                click_x, click_y = self.red_dot_monitor.get_click_position(
                                    self.window, int(target_y), None
                                )
                                self._physical_click(click_x, click_y)
                        time.sleep(1.0)
                        self._log("info", f"[红点] 已切回: {original_contact}")
                    else:
                        # 侧栏未命中：不刷屏，静默保持当前窗口（已处理完，不影响已发出的回复）
                        self._log("debug", f"[红点] 侧栏未匹配到 {original_contact}，保持当前窗口")
        except Exception as e:
            self._log("warning", f"[红点] 切回原窗口失败: {e}")

        _processed_count += 1  # 本轮联系人已处理完（跳过黑名单的不计入）

        self._on_stats()
        self._log("info", f"[红点] 处理完成，实际处理 {_processed_count} 个联系人"
                  f"（含跳过黑名单/白名单 {len(unread_contacts) - _processed_count} 个）")

        # === 屏幕外模式：处理完无需移回（本来就在屏幕外） ===
        if _was_offscreen:
            self._log("info", "[屏幕外] 后台监控完成，窗口保持屏幕外")

        # 持久化红点去重历史（本轮新增的 key 落盘，重启后不重复上报）
        try:
            self._save_reddot_seen()
        except Exception:
            pass

    def reset_ai_training(self):
        """重置AI训练（微信更新UI后重新学习）"""
        if self.ai_trainer:
            self.ai_trainer.reset_training()
            self._log("info", "AI训练已重置，重新进入AI学习期")

    def set_auto_reply(self, enabled):
        """动态开关自动回复"""
        self.auto_reply_enabled = enabled
        if enabled and self._reply_agg_enabled:
            self._start_reply_aggregator()
        self._log("info", f"自动回复已{'开启' if enabled else '关闭'}")

    def get_stats(self):
        return dict(self.stats)

    def get_storage(self):
        return self.storage


def setup_logging(config):
    """配置日志（文件自动轮转：1MB x 3 份，避免长期运行日志无限膨胀）"""
    log_config = config.get("logging", {})
    level = getattr(logging, log_config.get("level", "INFO"), logging.INFO)
    log_file = log_config.get("file", "wechat_ai_reply.log")
    from logging.handlers import RotatingFileHandler
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            RotatingFileHandler(log_file, maxBytes=1024 * 1024, backupCount=3, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )
    return logging.getLogger("main")


def main_cli():
    """命令行模式入口"""
    if sys.stdout.encoding != "utf-8":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

    parser = argparse.ArgumentParser(description="微信 AI 助手")
    parser.add_argument("--test", action="store_true", help="测试模式")
    parser.add_argument("--setup", action="store_true", help="仅定位微信窗口")
    parser.add_argument("--config", default="config.yaml", help="配置文件路径")
    parser.add_argument("--no-ui", action="store_true", help="不启动UI，纯命令行模式")
    parser.add_argument("--auto-reply", action="store_true", help="强制开启自动回复")
    args = parser.parse_args()

    if args.no_ui or args.test or args.setup:
        _run_cli(args)
    else:
        _run_ui(args)


def _run_cli(args):
    """纯命令行模式"""
    print(r"""
  ========================================
    微信 AI 助手 v2.0 (命令行模式)
  ========================================
  """)

    engine = WeChatEngine(args.config)

    if args.auto_reply:
        engine.auto_reply_enabled = True

    if args.setup:
        if engine.find_window():
            print("  窗口定位完成")
        return

    if args.test:
        engine.wechat_config["poll_interval"] = 2.0
        print("  [TEST] 测试模式：只截图+OCR+提取，不发送回复")
        engine.auto_reply_enabled = False

    engine.start()

    try:
        while engine.is_running():
            time.sleep(1)
    except KeyboardInterrupt:
        engine.stop()


def _run_ui(args):
    """启动UI模式"""
    try:
        from ui_app import run_ui
        run_ui(args.config)
    except ImportError as e:
        print(f"  UI模块加载失败: {e}")
        print("  请安装 customtkinter: pip install customtkinter")
        print("  或使用 --no-ui 参数运行命令行模式")
        sys.exit(1)


if __name__ == "__main__":
    signal.signal(signal.SIGINT, lambda s, f: sys.exit(0))
    main_cli()