"""
截图模块 — 使用 mss 快速截取屏幕区域（支持多显示器 + DPI缩放）
================================================================
修复点：
1. DPI感知：启动时设置Per-Monitor DPI Aware，确保坐标正确
2. 多显示器：用win32api获取真实窗口位置，不依赖pygetwindow的缩放坐标
3. 截图增强：mss用虚拟屏幕坐标截取，支持任意显示器
"""
import ctypes
import logging
import numpy as np
import mss

logger = logging.getLogger(__name__)

# === DPI 感知设置（必须在程序启动早期执行） ===
try:
    # Windows 10/11: Per-Monitor DPI Aware v2
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        # 旧版Windows兼容
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def _get_window_rect_win32(window):
    """
    用win32api获取窗口的真实物理坐标（不受DPI缩放影响）。
    如果win32不可用，回退到pygetwindow的坐标。

    Returns:
        (left, top, width, height) 物理屏幕坐标
    """
    try:
        import win32gui
        import win32con

        # 通过标题查找窗口句柄
        title = window.title
        if not title:
            return (window.left, window.top, window.width, window.height)

        hwnd = win32gui.FindWindow(None, title)
        if not hwnd:
            return (window.left, window.top, window.width, window.height)

        # 获取客户区域（不含标题栏和边框）
        rect = win32gui.GetWindowRect(hwnd)
        left, top, right, bottom = rect
        width = right - left
        height = bottom - top

        return (left, top, width, height)
    except Exception:
        # win32不可用，回退到pygetwindow
        return (window.left, window.top, window.width, window.height)


def capture_region(region):
    """
    截取屏幕指定区域（支持多显示器）。

    Args:
        region: (left, top, width, height) 元组，绝对屏幕坐标

    Returns:
        numpy.ndarray (BGR格式) 或 None
    """
    left, top, width, height = region

    # 确保是整数
    left = int(left)
    top = int(top)
    width = int(width)
    height = int(height)

    # 确保宽高有效
    if width <= 0 or height <= 0:
        logger.error(f"截图区域无效: width={width}, height={height}")
        return None

    monitor = {
        "left": left,
        "top": top,
        "width": width,
        "height": height,
    }

    try:
        with mss.mss() as sct:
            # mss 的 grab 支持虚拟屏幕坐标（跨显示器）
            img = sct.grab(monitor)
            arr = np.array(img)
            # BGRA -> BGR
            result = arr[:, :, :3]

            # 检查截图是否全黑（可能坐标在屏幕外）
            if result.mean() < 1:
                logger.warning(f"截图全黑，可能坐标不在任何显示器上: left={left}, top={top}")
                # 尝试遍历所有显示器找到包含该区域的显示器
                for m in sct.monitors[1:]:
                    m_left = m["left"]
                    m_top = m["top"]
                    m_right = m_left + m["width"]
                    m_bottom = m_top + m["height"]
                    if m_left <= left < m_right and m_top <= top < m_bottom:
                        logger.info(f"找到匹配显示器: {m}")
                        break

            return result
    except Exception as e:
        logger.error(f"截图失败: {e}")
        return None

def capture_chat_area(window):
    """
    截取微信完整聊天区域（从顶部栏到输入框上方）。

    Args:
        window: 微信窗口对象

    Returns:
        numpy.ndarray 或 None
    """
    from window_manager import get_chat_region
    region = get_chat_region(window)
    return capture_region(region)


def capture_full_window(window):
    """
    截取整个微信窗口（用于调试和"识别当前信息"功能）。
    使用win32api获取真实坐标，解决DPI缩放问题。

    Args:
        window: 微信窗口对象

    Returns:
        numpy.ndarray 或 None
    """
    left, top, width, height = _get_window_rect_win32(window)
    return capture_region((left, top, width, height))


def capture_chat_bottom(window, ratio=0.35):
    """
    只截取聊天区域底部（最新消息所在位置）。

    Args:
        window: 微信窗口对象
        ratio: 截取底部比例（默认35%）

    Returns:
        numpy.ndarray 或 None
    """
    from window_manager import get_chat_region

    left, top, width, height = get_chat_region(window)
    bottom_height = int(height * ratio)
    bottom_top = top + height - bottom_height
    return capture_region((left, bottom_top, width, bottom_height))

def is_image_blank(image, threshold=10):
    """
    检查图像是否基本为空（被遮挡或黑屏）。
    返回 True 表示图像可能无效。
    """
    if image is None:
        return True
    avg_brightness = np.mean(image)
    return avg_brightness < threshold


def is_similar_to(image1, image2, diff_threshold=0.02):
    """
    比较两张图像是否相似，用于判断聊天窗口是否有变化。
    """
    if image1 is None or image2 is None:
        return False
    if image1.shape != image2.shape:
        return False
    diff = np.abs(image1.astype(np.int16) - image2.astype(np.int16))
    changed_pixels = np.mean(diff > 30)
    return changed_pixels < diff_threshold


def get_monitor_info():
    """获取所有显示器信息（调试用）"""
    try:
        with mss.mss() as sct:
            monitors = []
            for i, m in enumerate(sct.monitors):
                monitors.append({
                    "index": i,
                    "left": m["left"],
                    "top": m["top"],
                    "width": m["width"],
                    "height": m["height"],
                })
            return monitors
    except Exception:
        return []