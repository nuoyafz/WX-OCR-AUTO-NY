"""
多联系人批量扫描模块
====================
自动遍历微信左侧联系人列表，逐个切换聊天窗口并扫描消息。
配合主监听循环使用，实现多联系人的信息提取。
"""
import time
import logging
import pyautogui
import pygetwindow as gw

logger = logging.getLogger(__name__)

# 安全设置
pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0.05


class ContactScanner:
    """多联系人批量扫描器"""

    def __init__(self, scanner_config, window_manager_module):
        """
        Args:
            scanner_config: config.yaml 中 contact_scanner 段配置
            window_manager_module: window_manager 模块引用
        """
        self.config = scanner_config or {}
        self.enabled = self.config.get("enabled", False)
        self.scan_interval = self.config.get("scan_interval", 30)
        self.max_contacts = self.config.get("max_contacts", 20)
        self.list_x_ratio = self.config.get("contact_list_x_ratio", 0.12)
        self.list_start_y_ratio = self.config.get("contact_list_start_y", 0.15)
        self.list_step_y_ratio = self.config.get("contact_list_step_y", 0.06)

        self.wm = window_manager_module
        self._last_scan_time = 0
        self._scanned_contacts = set()
        self._current_index = 0

    def should_scan(self):
        """检查是否到了扫描时间"""
        if not self.enabled:
            return False
        elapsed = time.time() - self._last_scan_time
        return elapsed >= self.scan_interval

    def scan_next(self, window):
        """
        扫描下一个联系人：点击联系人列表中的下一项。
        返回切换后的联系人名称，失败返回 None。

        Args:
            window: 微信窗口对象

        Returns:
            str: 新的联系人名称，或 None
        """
        if not self.enabled:
            return None

        if self._current_index >= self.max_contacts:
            self._current_index = 0
            logger.info("联系人列表扫描完成一轮，重新开始")

        # 计算点击位置
        left, top = window.left, window.top
        w, h = window.width, window.height

        click_x = left + int(w * self.list_x_ratio)
        click_y = top + int(h * (self.list_start_y_ratio + self._current_index * self.list_step_y_ratio))

        logger.info(f"点击联系人列表第 {self._current_index + 1} 项: ({click_x}, {click_y})")

        try:
            # 点击联系人项
            pyautogui.click(click_x, click_y)
            time.sleep(0.8)  # 等待聊天窗口切换

            # 重新获取窗口（标题可能已变）
            windows = gw.getWindowsWithTitle("微信")
            if windows:
                new_window = None
                for win in windows:
                    if win.width > 400 and win.height > 300:
                        title = win.title or ""
                        if "AI" in title or "助手" in title:
                            continue
                        new_window = win
                        break

                if new_window:
                    contact_name = self.wm.get_contact_name(new_window)
                    self._current_index += 1
                    self._last_scan_time = time.time()

                    if contact_name not in self._scanned_contacts:
                        self._scanned_contacts.add(contact_name)
                        logger.info(f"切换到联系人: {contact_name} (第 {self._current_index} 个)")
                    return contact_name, new_window
                else:
                    logger.warning("切换后未找到微信窗口")
                    return None, window
            else:
                logger.warning("切换后未找到微信窗口")
                return None, window

        except Exception as e:
            logger.error(f"扫描联系人失败: {e}")
            return None, window

    def reset(self):
        """重置扫描状态"""
        self._current_index = 0
        self._last_scan_time = 0
        self._scanned_contacts.clear()
        logger.info("联系人扫描状态已重置")

    def get_scanned_contacts(self):
        """获取已扫描的联系人列表"""
        return list(self._scanned_contacts)

    def click_specific_contact(self, window, contact_name):
        """
        尝试点击指定的联系人（通过在搜索框中搜索）。
        这是备用方案：如果批量扫描无法定位，可以用搜索。

        Args:
            window: 微信窗口对象
            contact_name: 要查找的联系人名称

        Returns:
            bool: 是否成功切换到该联系人
        """
        left, top = window.left, window.top
        w, h = window.width, window.height

        try:
            # 点击微信搜索框（通常在左上角）
            search_x = left + int(w * 0.12)
            search_y = top + int(h * 0.04)
            pyautogui.click(search_x, search_y)
            time.sleep(0.3)

            # 清空搜索框并输入联系人名
            pyautogui.hotkey("ctrl", "a")
            time.sleep(0.05)

            import pyperclip
            pyperclip.copy(contact_name)
            pyautogui.hotkey("ctrl", "v")
            time.sleep(0.5)

            # 按回车搜索
            pyautogui.press("enter")
            time.sleep(0.8)

            logger.info(f"通过搜索切换到联系人: {contact_name}")
            return True

        except Exception as e:
            logger.error(f"搜索联系人失败: {e}")
            return False
