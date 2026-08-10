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

from window_manager import find_wechat_window, setup_window_guide, focus_window, is_window_visible, get_contact_name
from screenshot import capture_chat_bottom, is_image_blank
from ocr_engine import recognize, identify_senders
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

    def initialize(self):
        """初始化各模块（在启动前调用）— 分步加载，逐步输出日志，避免用户感知卡顿"""
        self._log("info", "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        self._log("info", "开始初始化系统模块...")
        self._log("info", "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        time.sleep(0.4)

        # 步骤1：消息解析器
        self._log("info", "[1/6] 正在初始化消息解析器...")
        time.sleep(0.3)
        stable_frames = self.wechat_config.get("stable_frames", 2)
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
        return True

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
        return True

    def start(self):
        """启动监控（在后台线程中运行）"""
        if self._running:
            return

        if not self.find_window():
            self._on_status("error")
            return

        if not self.initialize():
            self._on_status("error")
            return

        self._running = True
        self._stop_flag.clear()
        self._on_status("running")
        self._log("info", "开始监听微信消息...")

        self._thread = threading.Thread(target=self._main_loop, daemon=True)
        self._thread.start()

    def stop(self):
        """停止监控"""
        self._stop_flag.set()
        self._running = False
        self._on_status("stopped")
        self._log("info", "正在停止监控...")

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
        self._log("info", f"当前聊天联系人: {contact_name}")

        while not self._stop_flag.is_set():
            try:
                loop_start = time.time()

                if not is_window_visible(self.window):
                    time.sleep(poll_interval)
                    continue

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
                image = capture_chat_bottom(self.window, ratio=capture_ratio)
                self.stats["frames_captured"] += 1

                if is_image_blank(image):
                    self._log("warning", "截图为空，可能窗口被遮挡")
                    time.sleep(poll_interval)
                    continue

                # 2. 增量检测：判断是否需要做OCR
                if self.smart_monitor:
                    need_ocr, diff_regions = self.smart_monitor.should_run_ocr(image)
                    if not need_ocr:
                        # 画面无变化，静默跳过（不打日志避免刷屏）
                        time.sleep(poll_interval)
                        continue

                    self._log("info", f"[增量] 检测到画面变化，区域数: {len(diff_regions)}")

                    # 增量OCR：只对变化区域做OCR
                    def _ocr_func(img):
                        return recognize(
                            img,
                            scale=ocr_scale,
                            min_confidence=ocr_min_conf,
                            merge_bubble=ocr_merge,
                            denoise=ocr_denoise,
                        )

                    ocr_results = self.smart_monitor.incremental_ocr(image, diff_regions, _ocr_func)
                    self.stats["ocr_calls"] += 1
                else:
                    # 回退：全量OCR
                    ocr_results = recognize(
                        image,
                        scale=ocr_scale,
                        min_confidence=ocr_min_conf,
                        merge_bubble=ocr_merge,
                        denoise=ocr_denoise,
                    )
                    self.stats["ocr_calls"] += 1

                if not ocr_results:
                    self._log("info", "OCR未识别到文字")
                    time.sleep(poll_interval)
                    continue

                self._log("info", f"OCR识别到 {len(ocr_results)} 条文本")

                # 3. 识别发送者
                ocr_results = identify_senders(ocr_results, image)

                # 4. 解析新消息
                result = self.parser.feed(ocr_results)
                new_messages = result["new_messages"]
                # pHash去重（比MD5更鲁棒）
                if self.smart_monitor:
                    deduped = []
                    for msg in new_messages:
                        if not self.smart_monitor.deduplicate(str(msg["content"])):
                            deduped.append(msg)
                    new_messages = deduped
                context = result["context"]

                for msg in new_messages:
                    content = str(msg.get("content", "")).strip()
                    if not content:
                        continue
                    # 跳过自己发的消息
                    if msg.get("sender") == "me":
                        continue
                    # 过滤自身UI窗口的OCR误识别内容
                    if any(skip in content for skip in ["助手v2.0", "AI助手", "微信 AI", "信息提取", "自动回复", "数据查看", "设置"]):
                        continue
                    # 过滤过短的无效内容（单个字符或纯标点）
                    if len(content) < 2:
                        continue
                    # 过滤纯时间戳
                    import re
                    if re.match(r'^\d{1,2}[:：]\d{2}$', content):
                        continue
                    if re.match(r'^(昨天|今天|明天|后天|星期[一二三四五六日天]|周[一二三四五六日天])$', content):
                        continue
                    if re.match(r'^\d+$', content):
                        continue
                    system_words = ["以下是新消息", "收到红包", "撤回了一条消息", "拍了拍", "以上是打招呼", "添加了", "邀请你"]
                    if any(sw in content for sw in system_words):
                        continue
                    if re.match(r'^[\s\.\,\，\。\！\？\!\?\-\_\(\)\(\)]+$', content):
                        continue
                    self.stats["messages_detected"] += 1
                    self._log("info", f"[新消息] {contact_name}: {content[:80]}")

                    # 实时通知UI
                    if self._on_new_message:
                        try:
                            self._on_new_message({
                                "contact": contact_name,
                                "sender": msg.get("sender", "other"),
                                "content": content,
                                "timestamp": datetime.now().strftime("%H:%M:%S"),
                                "is_important": False,
                            })
                        except Exception:
                            pass

                    # === 信息提取 ===
                    if self.extractor:
                        extracted = self.extractor.extract(
                            text=content,
                            sender=msg.get("sender", "other"),
                            contact_name=contact_name,
                            timestamp=datetime.now(),
                        )
                        self.stats["extracted"] += 1

                        if extracted.get("is_important"):
                            self.stats["important"] += 1
                            self._log("warning",
                                f"[重要] {contact_name}: {extracted.get('importance_reason', '')}")
                            # 实时通知UI（重要消息）
                            if self._on_new_message:
                                try:
                                    self._on_new_message({
                                        "contact": contact_name,
                                        "sender": msg.get("sender", "other"),
                                        "content": content,
                                        "timestamp": datetime.now().strftime("%H:%M:%S"),
                                        "is_important": True,
                                        "importance_reason": extracted.get("importance_reason", ""),
                                    })
                                except Exception:
                                    pass

                        if self.storage:
                            self.storage.save(extracted)

                        # Obsidian同步
                        if self.obsidian and self.obsidian.enabled:
                            sync_data = {
                                "contact": contact_name,
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

                    # === 自动回复（可选） ===
                    if self.auto_reply_enabled and self.llm_client:
                        role = self.role_manager.get_role_for(contact_name)
                        self._log("info", f"[角色] {role['name']} ({role['reply_style']})")

                        reply = self.llm_client.generate_reply(
                            contact_name, content, role, context
                        )
                        if reply:
                            time.sleep(send_delay)
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
                            stable_frames=self.wechat_config.get("stable_frames", 2),
                            context_size=self.wechat_config.get("context_size", 20),
                        )
                        self._log("info", f"[轮询] 已切换到: {contact_name}")
                        # 切换后立即截图提取一次
                        time.sleep(0.5)
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

            # 点击切换到该联系人
            # 点击联系人名称位置（侧边栏中央，而非红点位置）
            click_x, click_y = self.red_dot_monitor.get_click_position(
                self.window, item["red_dot_y"], None  # None=用侧边栏中央X
            )
            self._log("info", f"[红点] 点击坐标: ({click_x}, {click_y})")
            try:
                # 使用win32api点击（DPI安全，物理坐标）
                import win32api
                win32api.SetCursorPos((int(click_x), int(click_y)))
                time.sleep(0.1)
                win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
                time.sleep(0.05)
                win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
                self._log("info", f"[红点] 点击完成: ({int(click_x)}, {int(click_y)})")
            except Exception as e:
                self._log("error", f"[红点] 点击失败: {e}")
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

            # 截图完整聊天区域（不是只截底部35%）
            full_ratio = self.wechat_config.get("capture_ratio_full", 0.85)
            image = capture_chat_bottom(self.window, ratio=full_ratio)
            if image is None or is_image_blank(image):
                self._log("warning", f"[红点] {new_contact} 截图为空，跳过")
                self.red_dot_monitor.mark_processed(contact)
                continue

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
                        import pyautogui
                        win_x = self.window.left
                        win_y = self.window.top
                        win_w = self.window.width
                        win_h = self.window.height
                        # 聊天区域中心
                        chat_cx = win_x + int(win_w * 0.55)
                        chat_cy = win_y + int(win_h * 0.55)
                        pyautogui.scroll(5, chat_cx, chat_cy)  # 向上滚动
                        time.sleep(0.5)
                        scroll_attempts += 1
                        self._log("info", f"[红点] {new_contact} 向上滚动({scroll_attempts}/{max_scroll})")
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
                self._log("warning", f"[红点] {new_contact} 未识别到文字")
                self.red_dot_monitor.mark_processed(contact)
                continue

            # 发送者判断
            all_ocr_results = identify_senders(all_ocr_results, image)

            # 增强发送者判断：右侧(x>55%)是"我"，左侧(x<45%)是"对方"，中间不确定
            img_w = image.shape[1] if image is not None else 800
            for r in all_ocr_results:
                x_center = r.get("x_center", 0)
                if x_center > img_w * 0.55:
                    r["sender"] = "me"
                elif x_center < img_w * 0.45:
                    r["sender"] = "other"
                # 中间区域保持原判断

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
                if any(skip in content for skip in ["助手v2.0", "AI助手", "微信 AI", "信息提取", "自动回复", "数据查看", "设置"]):
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

                # 实时通知UI
                if self._on_new_message:
                    try:
                        self._on_new_message({
                            "contact": new_contact,
                            "sender": msg.get("sender", "other"),
                            "content": content,
                            "timestamp": datetime.now().strftime("%H:%M:%S"),
                            "is_important": False,  # 后面更新
                        })
                    except Exception:
                        pass

                # 信息提取
                if self.extractor:
                    extracted = self.extractor.extract(
                        text=content,
                        sender=msg.get("sender", "other"),
                        contact_name=new_contact,
                        timestamp=datetime.now(),
                    )
                    self.stats["extracted"] += 1

                    if extracted.get("is_important"):
                        self.stats["important"] += 1
                        self._log("warning",
                            f"[红点][重要] {new_contact}: {extracted.get('importance_reason', '')}")

                        # 更新UI通知重要性
                        if self._on_new_message and extracted.get("is_important"):
                            try:
                                self._on_new_message({
                                    "contact": new_contact,
                                    "sender": msg.get("sender", "other"),
                                    "content": content,
                                    "timestamp": datetime.now().strftime("%H:%M:%S"),
                                    "is_important": True,
                                    "importance_reason": extracted.get("importance_reason", ""),
                                })
                            except Exception:
                                pass

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

            # 标记已处理
            self.red_dot_monitor.mark_processed(contact)

        # 切回原窗口（点击侧边栏第一个或原始联系人）
        try:
            if original_contact:
                self._log("info", f"[红点] 切回原窗口: {original_contact}")
                # 获取侧边栏截图，找到原联系人位置
                sidebar_img = self.red_dot_monitor.capture_sidebar(self.window)
                if sidebar_img is not None:
                    ocr_results = recognize(
                        sidebar_img,
                        scale=1.0,
                        min_confidence=0.40,
                        merge_bubble=False,
                        denoise=False,
                    )
                    # 找到原联系人的Y坐标
                    target_y = None
                    for r in ocr_results:
                        text = str(r.get("text", "")).strip()
                        if original_contact in text or text in original_contact:
                            target_y = r.get("y_center", 0)
                            break
                    if target_y:
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
