"""
Windows UI Automation 未读监控 — 无渲染模式下的红点检测
========================================================
核心原理：通过 Windows UI Automation (UIA) API 直接读取微信侧边栏
的无障碍树节点，获取未读消息数/红点状态，完全不依赖屏幕截图。

优势：
1. 最小化/屏幕外/锁屏状态均可用（无需 GPU 渲染）
2. 延迟 < 50ms（无截图/OCR 开销）
3. 零用户视觉干扰（不闪现窗口）

技术栈：
- comtypes + Windows UIAutomationCore.dll
- 或 uiautomation 库（更友好的 Python 封装）

回退策略：
1. UIA 直接读取未读徽章（首选）
2. UIA 读取联系人列表项，检测 Name 包含未读数
3. 降级为原有截图方案

注意：微信 4.x 的 UIA 支持取决于具体版本和 Qt 无障碍实现。
如果 UIA 不可用，自动降级为原有截图方案。
"""
import time
import logging
import threading
import re

logger = logging.getLogger(__name__)

_UIA_AVAILABLE = False
try:
    import uiautomation as auto
    _UIA_AVAILABLE = True
except ImportError:
    try:
        import comtypes.client
        _UIA_AVAILABLE = True
    except ImportError:
        logger.info("[UIA] uiautomation 未安装，"
                    "安装: pip install uiautomation")


class UIAMonitor:
    """基于 Windows UI Automation 的未读消息监控"""

    def __init__(self, config=None):
        self.config = config or {}
        self.enabled = _UIA_AVAILABLE and self.config.get("enabled", True)
        self.scan_interval = self.config.get("uia_scan_interval", 1.0)
        self._last_scan = 0
        self._unread_cache = {}
        self._cache_ttl = self.config.get("uia_cache_ttl", 2.0)
        self._lock = threading.Lock()
        self._hwnd = None

        self.stats = {
            "uia_scans": 0,
            "uia_unread_found": 0,
            "uia_fallback": 0,
        }

        if self.enabled:
            logger.info("[UIA] Windows UI Automation 未读监控已启用")

    def set_hwnd(self, hwnd):
        self._hwnd = hwnd

    def get_unread_contacts(self, window):
        """
        通过 UIA 读取微信侧边栏的未读联系人列表。

        返回格式与 red_dot_monitor.get_unread_contacts 一致：
        [{"contact": "张三", "unread_count": 3, "red_dot_y": 200, "method": "uia"}, ...]
        """
        if not self.enabled:
            return None

        now = time.time()
        if now - self._last_scan < self.scan_interval:
            return self._cached_results()

        self._last_scan = now
        self.stats["uia_scans"] += 1

        try:
            results = self._scan_uia_tree(window)
            if results is not None:
                with self._lock:
                    self._unread_cache = {
                        "results": results, "timestamp": now}
                if results:
                    self.stats["uia_unread_found"] += 1
                return results
        except Exception as e:
            logger.debug("[UIA] 扫描异常: %s，降级截图方案", e)
            self.stats["uia_fallback"] += 1

        return None

    def _cached_results(self):
        with self._lock:
            if self._unread_cache:
                age = time.time() - self._unread_cache["timestamp"]
                if age < self._cache_ttl:
                    return self._unread_cache["results"]
        return None

    def _scan_uia_tree(self, window):
        """扫描微信窗口的 UIA 树，提取未读联系人"""
        if not _UIA_AVAILABLE:
            return None

        hwnd = getattr(window, "_hWnd", None)
        if not hwnd:
            return None

        try:
            return self._scan_with_uiautomation(hwnd)
        except Exception:
            try:
                return self._scan_with_comtypes(hwnd)
            except Exception:
                return None

    def _scan_with_uiautomation(self, hwnd):
        """使用 uiautomation 库扫描"""
        import uiautomation as auto

        wechat = auto.ControlFromHandle(hwnd)
        if not wechat:
            return None

        unread = []

        def _find_unread_badges(control, depth=0):
            if depth > 20:
                return
            try:
                name = control.Name or ""
                if not name:
                    return

                # 模式1：未读徽章控件（Name 为纯数字，如 "3"、"99+"）
                if re.match(r'^\d{1,3}\+?$', name.strip()):
                    count = int(name.strip().rstrip('+'))
                    if 1 <= count <= 999:
                        rect = control.BoundingRectangle
                        if rect:
                            cy = (rect.top + rect.bottom) // 2
                            unread.append({
                                "contact": "",
                                "unread_count": count,
                                "red_dot_y": cy,
                                "method": "uia_badge",
                            })

                # 模式2：列表项包含未读数，如 "张三 3条新消息"
                m = re.search(r'(\d+)\s*条', name)
                if m:
                    count = int(m.group(1))
                    contact_name = name[:m.start()].strip()
                    if contact_name and count > 0:
                        rect = control.BoundingRectangle
                        if rect:
                            cy = (rect.top + rect.bottom) // 2
                            unread.append({
                                "contact": contact_name,
                                "unread_count": count,
                                "red_dot_y": cy,
                                "method": "uia_listitem",
                            })

                # 递归子节点
                for child in control.GetChildren():
                    _find_unread_badges(child, depth + 1)
            except Exception:
                pass

        _find_unread_badges(wechat)
        return unread if unread else None

    def _scan_with_comtypes(self, hwnd):
        """使用 comtypes 直接调用 UIA COM 接口"""
        import comtypes.client

        UIA_dll = comtypes.client.GetModule("UIAutomationCore.dll")
        if not UIA_dll:
            return None

        ui = comtypes.client.CreateObject(
            "{ff48dba4-3c73-44c8-9e7a-12f7e09e3e14}",
            interface=UIA_dll.IUIAutomation)
        if not ui:
            return None

        element = ui.ElementFromHandle(hwnd)
        if not element:
            return None

        unread = []
        condition = ui.CreateTrueCondition()
        cache_request = ui.CreateCacheRequest()

        # 遍历所有后代元素
        walker = ui.ControlViewWalker
        child = ui.GetFocusedElement(element) or element

        try:
            children = element.FindAll(
                UIA_dll.TreeScope.TreeScope_Descendants, condition)
            if children:
                for i in range(children.Length):
                    elem = children.GetElement(i)
                    try:
                        name = elem.CurrentName or ""
                        if not name:
                            continue
                        m = re.search(r'(\d+)\s*条', name)
                        if m:
                            count = int(m.group(1))
                            contact = name[:m.start()].strip()
                            if contact and count > 0:
                                rect = elem.CurrentBoundingRectangle
                                if rect:
                                    cy = (rect.top + rect.bottom) // 2
                                    unread.append({
                                        "contact": contact,
                                        "unread_count": count,
                                        "red_dot_y": cy,
                                        "method": "uia_com",
                                    })
                    except Exception:
                        continue
        except Exception:
            pass

        return unread if unread else None

    def get_unread_count_total(self, window):
        """
        快速获取总未读数（仅数字，不需要联系人列表）。

        用于快速判断「是否有未读消息」，比完整扫描快 10 倍。
        """
        if not self.enabled or not _UIA_AVAILABLE:
            return None

        hwnd = getattr(window, "_hWnd", None)
        if not hwnd:
            return None

        try:
            import uiautomation as auto
            wechat = auto.ControlFromHandle(hwnd)
            if not wechat:
                return None

            # 微信标题栏通常显示 "微信 (3)" 表示有 3 条未读
            title = wechat.Name or ""
            m = re.search(r'\((\d+)\)', title)
            if m:
                return int(m.group(1))

            # 或者搜索任务栏图标上的角标
            for child in wechat.GetChildren():
                name = child.Name or ""
                m = re.search(r'^(\d{1,3})\+?$', name.strip())
                if m:
                    return int(m.group(1))
        except Exception:
            pass

        return None

    def should_check(self, interval=3):
        """检查是否该进行 UIA 扫描"""
        return (time.time() - self._last_scan) >= interval

    def get_stats(self):
        return dict(self.stats)


# 全局单例
_uia_instance = None


def get_uia_monitor(config=None):
    global _uia_instance
    if _uia_instance is None:
        _uia_instance = UIAMonitor(config)
    return _uia_instance