import ctypes
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass
"""
微信 AI 助手 — 核心引擎
========================
整合截图+OCR+信息提取+自动回复，可被UI调用或命令行运行。
自动回复默认关闭，可通过config或UI开启。
"""
import sys
import io
import time
import signal
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
        self._on_capture_cb = callbacks.get("on_capture")  # 截图预览回调
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

        # Obsidian同步
        obsidian_cfg = self.role_manager.config.get("obsidian", {})
        self.obsidian = ObsidianSync(obsidian_cfg)
        if self.obsidian.enabled:
            self._log("info", "[Obsidian] 同步已启用")

        self.window = None
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
            except Exception:
                pass

    def _on_extract(self, result):
        if self.callbacks.get("on_extract"):
            try:
                self.callbacks["on_extract"](result)
            except Exception:
                pass

    def _on_reply(self, contact, reply):
        if self.callbacks.get("on_reply"):
            try:
                self.callbacks["on_reply"](contact, reply)
            except Exception:
                pass

    def _on_stats(self):
        if self.callbacks.get("on_stats"):
            try:
                self.callbacks["on_stats"](dict(self.stats))
            except Exception:
                pass

    def _on_status(self, status):
        if self.callbacks.get("on_status"):
            try:
                self.callbacks["on_status"](status)
            except Exception:
                pass

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

        try:
            self._on_new_message(payload)
        except Exception as e:
            logger.warning(f"[UI卡片] 发送失败: {e}")

    def initialize(self):
        """初始化各模块（在启动前调用）— 分步加载，逐步输出日志，避免用户感知卡顿"""
        self._log("info", "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        self._log("info", "开始初始化系统模块...")
        self._log("info", "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        time.sleep(0.4)

        # 步骤1：消息解析器
        self._log("info", "[1/6] 正在初始化消息解析器...")
        time.sleep(0.3)
        stable_frames = self.wechat_config.get("stable_frames", 1)
        context_size = self.wechat_config.get("context_size", 20)
        self.parser = MessageParser(stable_frames=stable_frames, context_size=context_size)
        self._log("info", f"  ✔ 消息解析器就绪 (稳定帧={stable_frames}, 上下文={context_size})")
        time.sleep(0.4)

        # 步骤2：LLM客户端
        self._log("info", "[2/6] 正在初始化LLM客户端...")
        time.sleep(0.3)
        api_key = self.llm_config.get("api_key", "")
        if api_key and api_key not in ("", "your-api-key-here"):
            self.llm_client = LLMClient(self.llm_config)
            self._log("info", f"  ✔ LLM客户端就绪: {self.llm_config.get('model', '?')}")
        else:
            self._log("info", "  ✔ 未配置API Key，跳过LLM客户端")
        time.sleep(0.4)

        # 步骤3：信息提取引擎
        self._log("info", "[3/6] 正在初始化信息提取引擎...")
        time.sleep(0.3)
        self.extractor = InfoExtractor(self.extraction_config, self.llm_config)
        self._log("info", "  ✔ 信息提取引擎就绪")
        time.sleep(0.4)

        # 步骤4：数据存储
        self._log("info", "[4/6] 正在初始化数据存储...")
        time.sleep(0.3)
        self.storage = MessageStorage(self.storage_config)
        self._log("info", f"  ✔ 数据存储就绪: {self.storage_config.get('type', 'sqlite')}")
        time.sleep(0.4)

        # 步骤5：联系人扫描器 + 红点监控
        self._log("info", "[5/6] 正在初始化联系人监控...")
        time.sleep(0.3)
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
            self._log("info", "  ✔ 红点监控器已启用（自动检测未读红点）")
        else:
            self._log("info", "  ✔ 红点监控器未启用")
        time.sleep(0.4)

        # 步骤6：AI训练引擎（前10次AI辅助学习，之后纯规则模式）
        self._log("info", "[6/6] 正在初始化AI训练引擎...")
        time.sleep(0.3)
        ai_threshold = self.wechat_config.get("ai_training_threshold", 10)
        self.ai_trainer = AITrainer(self.llm_config, training_threshold=ai_threshold)
        progress = self.ai_trainer.get_progress()
        self._log("info", f"  ✔ AI训练引擎就绪 (学习进度 {progress['current']}/{ai_threshold})")
        time.sleep(0.4)

        self._log("info", "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        self._log("info", "✔ 全部模块初始化完成，准备开始监控")
        self._log("info", "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

        # ★ 根据微信状态自适应（尊重用户选择）：
        #   - 微信在桌面(非最小化) → 保持原位，监控用PrintWindow后台截图，不打扰用户
        #   - 微信已最小化 → 移到屏幕外，后台监控（桌面消失，点任务栏图标可恢复）
        if self.minimize_mode == "offscreen" and self.window is not None:
            try:
                from window_manager import is_window_minimized, move_window_offscreen
                if is_window_minimized(self.window):
                    if move_window_offscreen(self.window):
                        self._log("info", "[屏幕外] 微信已最小化 → 移到屏幕外后台监控（点任务栏图标可恢复）")
                    else:
                        self._log("warning", "[屏幕外] 移出屏幕失败，微信保持当前状态")
                else:
                    self._log("info", "[监控] 微信在桌面(未最小化)，保持原位监控，不移动窗口")
            except Exception as e:
                self._log("warning", f"[屏幕外] 初始化移屏异常: {e}")

        return True

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

        # 屏幕外模式：恢复窗口到原始可见位置
        try:
            from window_manager import is_window_offscreen, bring_window_back
            if self.window and is_window_offscreen(self.window):
                bring_window_back(self.window)
                self._log("info", "[屏幕外] 停止监控：窗口已恢复到原始位置")
        except Exception:
            pass

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3)

        if self.storage and self.storage_config.get("export_on_exit"):
            try:
                self.storage.export_csv()
                self._log("info", "数据已自动导出CSV")
            except Exception as e:
                self._log("error", f"导出CSV失败: {e}")

    def is_running(self):
        return self._running

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
            system_words = ["以下是新消息", "收到红包", "撤回了一条消息", "拍了拍", "以上是打招呼", "添加了", "邀请你"]
            if any(sw in content for sw in system_words):
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
                    if self.storage:
                        self.storage.save(extracted)
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

        contact_name = get_contact_name(self.window)
        # V2: 初始群聊/私聊判断（基于窗口标题），后续每轮会结合OCR再精细裁决
        ctx = analyze_chat_context(self.window)
        contact_name = ctx["contact"] or contact_name
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
                if self.red_dot_monitor and self.red_dot_monitor.enabled and not dnd_active:
                    # 快速模式触发时跳过间隔检查，立即扫描
                    check_interval = 1 if fast_triggered else 3
                    if fast_triggered or self.red_dot_monitor.should_check(interval=check_interval):
                        if fast_triggered:
                            self._log("info", "[快速] 检测到窗口标题未读数，触发红点扫描")
                        self._log("info", "[红点] 正在扫描左侧栏未读消息...")
                        unread = self.red_dot_monitor.get_unread_contacts(self.window)
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
                            self._log("info", f"[红点] 检测到 {len(unread)} 个联系人有未读消息: {[u['contact'] for u in unread]}")
                            self._handle_unread_contacts(unread, contact_name)
                            continue  # 处理完未读后跳过本轮正常截图
                        else:
                            self._log("info", "[红点] 未检测到未读消息，继续监控当前窗口")

                # 1. 截图
                try:
                    from screenshot import capture_via_printwindow, capture_chat_bottom, capture_minimized_window
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
                                    full_img = capture_via_printwindow(hwnd)
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
                                    full_img = capture_via_printwindow(hwnd)

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

                # 检查是否最小化模式（跳过增量检测，直接做全量OCR）
                from window_manager import is_window_minimized
                _is_min = is_window_minimized(self.window)

                # 2. 增量检测：判断是否需要做OCR
                # 最小化模式下跳过增量检测（因为PrintWindow截图可能不稳定）
                if self.smart_monitor and not _is_min:
                    need_ocr, diff_regions = self.smart_monitor.should_run_ocr(image)
                    if not need_ocr:
                        # 画面无变化：先确认稳定帧超时候选（防新消息漏报），再跳过
                        self._flush_stable_candidates(contact_name)
                        time.sleep(poll_interval)
                        continue

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
                    system_words = ["以下是新消息", "收到红包", "撤回了一条消息",
                                    "拍了拍", "以上是打招呼", "添加了", "邀请你"]
                    if any(sw in content for sw in system_words):
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
                    self._send_ui_card(
                        contact_name, _sender, content, _ts,
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
                        )

                        if self.storage:
                            self.storage.save(extracted)

                        # Obsidian同步（V3：最新消息在顶部 + 微信气泡Callout风格）
                        if self.obsidian and self.obsidian.enabled:
                            sync_data = {
                                "contact": contact_name,
                                "sender": extracted.get("sender", _sender),
                                "content": extracted.get("raw_text", content),
                                "timestamp": extracted.get("timestamp", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
                                "is_important": extracted.get("is_important", False),
                                "importance_reason": extracted.get("importance_reason", ""),
                                "keywords": extracted.get("matched_keywords", []) or extracted.get("keywords", []),
                                "summary": (extracted.get("llm_analysis", {}) or {}).get("summary", "") if isinstance(extracted.get("llm_analysis"), dict) else "",
                                "is_group": (current_chat_kind == "group"),
                                "group_member": _group_member,
                                "chat_kind": current_chat_kind,
                                "extracted_fields": extracted.get("extracted_fields") or extracted.get("fields"),
                                "ocr_confidence": _conf,
                                "sender_confidence": _sender_conf,
                            }
                            self.obsidian.sync_message(sync_data)

                        self._on_extract(extracted)

                    # === 自动回复（只对方消息触发；自己的绝对不回复，避免 AI 复读自己） ===
                    if _sender != "me" and self.auto_reply_enabled and self.llm_client:
                        role = self.role_manager.get_role_for(contact_name)
                        self._log("info", f"[角色] {role['name']} ({role['reply_style']})")

                        reply = self.llm_client.generate_reply(
                            contact_name, content, role, context
                        )
                        if reply:
                            time.sleep(send_delay)

                            from window_manager import is_window_offscreen
                            _is_offscreen = is_window_offscreen(self.window)
                            if _is_offscreen:
                                from sender import send_text_offscreen
                                success = send_text_offscreen(self.window, reply)
                            else:
                                focus_window(self.window)
                                time.sleep(0.2)
                                success = send_text(self.window, reply)
                            if success:
                                self.stats["replies_sent"] += 1
                                self.parser.add_to_context("assistant", reply)
                                self.parser.mark_reply_sent(reply)
                                self._log("info", f"[已回复] {reply[:50]}")
                                self._on_reply(contact_name, reply)
                            else:
                                self._log("error", "[发送失败]")

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
                                scan_ocr = identify_senders(scan_ocr, scan_image)
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
                                        if self.storage:
                                            self.storage.save(extracted)
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

    def _handle_unread_contacts(self, unread_contacts, original_contact):
        """处理有未读消息的联系人：切换 -> 截图 -> OCR -> 提取 -> 标记已处理"""
        import pyautogui
        from message_parser import MessageParser

        if not hasattr(self, '_click_retry_counts'):
            self._click_retry_counts = {}
        MAX_CLICK_RETRIES = 3

        _was_offscreen = False
        if self.minimize_mode == "offscreen":
            try:
                from window_manager import is_window_offscreen
                if is_window_offscreen(self.window):
                    _was_offscreen = True
                    self._log("info", "[屏幕外] 后台模式处理未读，不闪现窗口")
            except Exception:
                pass

        for item in unread_contacts:
            if self._stop_flag.is_set():
                break

            contact = item["contact"]

            # 白名单/黑名单过滤
            whitelist = self.role_manager.config.get("contacts_filter", {}).get("whitelist", [])
            blacklist = self.role_manager.config.get("contacts_filter", {}).get("blacklist", [])

            # 默认黑名单：服务号、订阅号等
            default_blacklist = ["拼多多", "瑞幸咖啡", "美团", "饿了么", "滴滴", "抖音", "快手", "京东", "淘宝", "天猫"]
            all_blacklist = list(set(blacklist + default_blacklist))

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

            hwnd = getattr(self.window, "_hWnd", None)
            try:
                if hwnd and _was_offscreen:
                    from window_manager import edge_click_window
                    click_cx, click_cy = self.red_dot_monitor.get_click_client_position(
                        self.window, item["red_dot_y"], None
                    )
                    # 边缘点击方案：窗口移到屏幕边缘(露1px)→激活→SendInput物理点击→移回
                    self._log("info", f"[红点] 边缘点击: ({click_cx}, {click_cy})")
                    ok = edge_click_window(self.window, click_cx, click_cy)
                    if not ok:
                        self._click_retry_counts[contact] = self._click_retry_counts.get(contact, 0) + 1
                        retries = self._click_retry_counts.get(contact, 0)
                        if retries >= MAX_CLICK_RETRIES:
                            self._log("warning", f"[红点] {contact} 点击重试{retries}次失败，强制标记已处理")
                            self.red_dot_monitor.mark_processed(contact)
                            self._click_retry_counts[contact] = 0
                            continue
                        self._log("warning", f"[红点] 点击失败 (重试{retries}/{MAX_CLICK_RETRIES})")
                        continue
                    self._log("info", "[红点] 后台点击完成（待验证切换）")
                else:
                    click_x, click_y = self.red_dot_monitor.get_click_position(
                        self.window, item["red_dot_y"], None
                    )
                    self._log("info", f"[红点] 物理点击坐标: ({click_x}, {click_y})")
                    import win32api
                    win32api.SetCursorPos((int(click_x), int(click_y)))
                    time.sleep(0.1)
                    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
                    time.sleep(0.05)
                    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
                    self._log("info", f"[红点] 物理点击完成: ({int(click_x)}, {int(click_y)})")
            except Exception as e:
                self._log("error", f"[红点] 点击失败: {e}")
                self._click_retry_counts[contact] = self._click_retry_counts.get(contact, 0) + 1
                retries = self._click_retry_counts.get(contact, 0)
                if retries >= MAX_CLICK_RETRIES:
                    self.red_dot_monitor.mark_processed(contact)
                    self._click_retry_counts[contact] = 0
                    self._log("warning", f"[红点] {contact} 点击重试{retries}次失败，强制跳过")
                continue

            time.sleep(2.5)  # 等待聊天窗口加载完成（增加等待让页面充分加载）

            # 重新获取窗口标题（联系人名可能变了）
            new_contact = get_contact_name(self.window)
            # 微信4.x窗口标题只有"微信"，用红点匹配到的联系人名作为备选
            if not new_contact or new_contact == "微信" or new_contact.strip() == "":
                new_contact = item.get("contact", "")
                self._log("info", f"[红点] 窗口标题无联系人名，使用红点匹配名: {new_contact}")
            self._log("info", f"[红点] 已切换到: {new_contact}")

            if self.smart_monitor:
                self.smart_monitor.reset()
            # 不再使用MessageParser（stable_frames机制不适合单次截图场景）

            # 截图：优先用 PrintWindow（避免mss截到程序UI），flags=3对Qt微信有效
            full_ratio = self.wechat_config.get("capture_ratio_full", 0.85)
            hwnd = getattr(self.window, "_hWnd", None)
            if hwnd:
                from screenshot import capture_via_printwindow
                _full_img = capture_via_printwindow(hwnd)
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
            ocr_results = recognize(
                image,
                scale=ocr_scale,
                min_confidence=ocr_min_conf,
                merge_bubble=False,
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
                                merge_bubble=False, denoise=False,
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

            # 发送者判断（identify_senders 已做位置+颜色综合判断）
            all_ocr_results = identify_senders(all_ocr_results, image)

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

            # AI训练引擎辅助识别新消息
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
                    if sender == "me":
                        continue
                    new_messages.append({"sender": sender, "content": text})
                self._log("info", f"[红点] {new_contact}: {len(new_messages)} 条新消息")

            for msg in new_messages:
                content = str(msg.get("content", "")).strip()
                if not content:
                    continue
                # 过滤自身UI窗口的OCR误识别内容
                if any(skip in content for skip in ["助手v2.0", "AI助手", "微信 AI", "信息提取", "自动回复", "数据查看", "设置", "红点", "屏幕外", "保活", "截图", "预览", "诊断", "增量", "窗口坐标"]):
                    continue
                # 过滤过短的无效内容（单个字符或纯标点）
                if len(content) < 2:
                    continue
                # 过滤纯时间戳（如 12:30, 14:32, 昨天, 星期六等）
                import re
                if re.match(r'^\d{1,2}[:：]\d{2}$', content):
                    continue
                if re.match(r'^(昨天|今天|明天|后天|星期[一二三四五六日天]|周[一二三四五六日天])$', content):
                    continue
                # 过滤纯数字或单个词
                if re.match(r'^\d+$', content):
                    continue
                # 过滤系统提示
                system_words = ["以下是新消息", "收到红包", "撤回了一条消息", "拍了拍", "以上是打招呼", "添加了", "邀请你"]
                if any(sw in content for sw in system_words):
                    continue
                # 过滤无意义OCR碎片（纯标点或特殊字符）
                if re.match(r'^[\s\.\,\，\。\！\？\!\?\-\_\(\)\(\)]+$', content):
                    continue
                self.stats["messages_detected"] += 1

                # 实时通知UI：先发基础卡（快速反馈），提取完成后用同一msg_key原位更新
                _ts = datetime.now().strftime("%H:%M:%S")
                _sender = msg.get("sender", "other")
                self._send_ui_card(new_contact, _sender, content, _ts)
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
                    )

                    if self.storage:
                        self.storage.save(extracted)

                        # Obsidian同步
                        if self.obsidian and self.obsidian.enabled:
                            sync_data = {
                                "contact": new_contact,
                                "sender": extracted.get("sender", "other"),
                                "content": extracted.get("raw_text", ""),
                                "timestamp": extracted.get("timestamp", ""),
                                "is_important": extracted.get("is_important", False),
                                "importance_reason": extracted.get("importance_reason", ""),
                                "keywords": extracted.get("matched_keywords", []),
                                "summary": (extracted.get("llm_analysis", {}) or {}).get("summary", "") if isinstance(extracted.get("llm_analysis"), dict) else "",
                            }
                            self.obsidian.sync_message(sync_data)

                    self._on_extract(extracted)

            # 标记已处理（真正处理成功才进冷却，并清零重试计数）
            self.red_dot_monitor.mark_processed(contact)
            self._click_retry_counts[contact] = 0

        # 切回原窗口（点击侧边栏第一个或原始联系人）
        try:
            if original_contact:
                self._log("info", f"[红点] 切回原窗口: {original_contact}")
                sidebar_img = self.red_dot_monitor.capture_sidebar(self.window)
                if sidebar_img is not None:
                    ocr_results = recognize(
                        sidebar_img,
                        scale=1.0,
                        min_confidence=0.40,
                        merge_bubble=False,
                        denoise=False,
                    )
                    target_y = None
                    for r in ocr_results:
                        text = str(r.get("text", "")).strip()
                        if original_contact in text or text in original_contact:
                            target_y = r.get("y_center", 0)
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
                            click_x, click_y = self.red_dot_monitor.get_click_position(
                                self.window, int(target_y), None
                            )
                            import win32api
                            win32api.SetCursorPos((int(click_x), int(click_y)))
                            time.sleep(0.1)
                            win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
                            time.sleep(0.05)
                            win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
                        time.sleep(1.0)
                        self._log("info", f"[红点] 已切回: {original_contact}")
                    else:
                        self._log("warning", f"[红点] 未找到原联系人 {original_contact}，保持当前窗口")
        except Exception as e:
            self._log("warning", f"[红点] 切回原窗口失败: {e}")

        self._on_stats()
        self._log("info", f"[红点] 处理完成，共 {len(unread_contacts)} 个联系人")

        # === 屏幕外模式：处理完无需移回（本来就在屏幕外） ===
        if _was_offscreen:
            self._log("info", "[屏幕外] 后台监控完成，窗口保持屏幕外")

    def reset_ai_training(self):
        """重置AI训练（微信更新UI后重新学习）"""
        if self.ai_trainer:
            self.ai_trainer.reset_training()
            self._log("info", "AI训练已重置，重新进入AI学习期")

    def set_auto_reply(self, enabled):
        """动态开关自动回复"""
        self.auto_reply_enabled = enabled
        self._log("info", f"自动回复已{'开启' if enabled else '关闭'}")

    def get_stats(self):
        return dict(self.stats)

    def get_storage(self):
        return self.storage


def setup_logging(config):
    """配置日志"""
    log_config = config.get("logging", {})
    level = getattr(logging, log_config.get("level", "INFO"), logging.INFO)
    log_file = log_config.get("file", "wechat_ai_reply.log")
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
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
