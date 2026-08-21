"""
截图模块 — 使用 mss 快速截取屏幕区域（支持多显示器 + DPI缩放）
================================================================
修复点：
1. DPI感知：启动时设置Per-Monitor DPI Aware，确保坐标正确
2. 多显示器：用win32api获取真实窗口位置，不依赖pygetwindow的缩放坐标
3. 截图增强：mss用虚拟屏幕坐标截取，支持任意显示器
"""
import os
import ctypes
import logging
import numpy as np
import mss

logger = logging.getLogger(__name__)

# ★ 最近一次成功的 PrintWindow 截图是否为客户区(PW_CLIENTONLY)内容。
#   位图尺寸固定为客户区尺寸，但若成功的是"全窗口"flags(非CLIENTONLY)，
#   位图内容会含标题栏，内容整体下移 titlebar 像素 → 坐标换算必须同步修正。
_last_pw_client_only = True

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


def crop_chat_region_img(full_img, bottom_ratio=1.0):
    """
    把全窗/全客户区截图裁剪为"聊天消息区"：排除左侧联系人栏、顶部标题/搜索栏、底部输入框。
    PrintWindow 截的是整个客户区（含左侧栏），直接对全宽做OCR会把联系人列表误识别成聊天消息。
    这里按 get_chat_region 相同比例先裁掉侧栏，再取底部 bottom_ratio。

    Args:
        full_img: 全窗/全客户区截图 (numpy.ndarray, BGR)
        bottom_ratio: 在聊天区内再取底部比例（1.0=整个聊天区，0.35=聊天区底部35%）

    Returns:
        numpy.ndarray 或 None
    """
    if full_img is None:
        return None
    h, w = full_img.shape[:2]
    x1 = int(w * 0.30)              # 排除左侧联系人栏
    y1 = int(h * 0.08)              # 排除顶部标题/搜索栏
    x2 = x1 + int(w * 0.68)
    y2 = y1 + int(h * 0.77)         # 排除底部输入框
    chat = full_img[y1:y2, x1:x2]
    if chat.size == 0:
        return full_img
    if 0 < bottom_ratio < 1.0:
        ch = chat.shape[0]
        bh = max(int(ch * bottom_ratio), 1)
        chat = chat[ch - bh:, :]
    return chat


def crop_title_bar_img(full_img):
    """截聊天窗口顶部标题栏区域（群名/联系人名所在）。

    微信4.x 所有聊天窗口的*系统窗口标题*恒为"微信"，真实群名/联系人名
    只显示在聊天区顶部的标题栏（群名 + 下方成员数/微信号）。本函数裁剪
    该区域，供 OCR 提取当前会话真实名称，解决 contact 被误识别为"微信"的问题。
    坐标与 crop_chat_region_img 一致（排除左侧联系人栏）。
    """
    if full_img is None:
        return None
    h, w = full_img.shape[:2]
    x1 = int(w * 0.30)              # 排除左侧联系人栏
    x2 = x1 + int(w * 0.68)
    # 顶部标题栏高度：微信4.x 在不同 DPI/缩放下约 8%~14%，
    # 取 14% 留余量（多截一点不会误伤，少截则群名/成员数可能被切掉，
    # 此前 12% 时较长群名(如"东赚30②群(258)")被截断只识别到首字"一"）
    y2 = max(int(h * 0.14), 1)
    bar = full_img[0:y2, x1:x2]
    return bar if bar.size else full_img


def capture_chat_bottom(window, ratio=0.35):
    """
    只截取聊天区域底部（最新消息所在位置）。
    屏幕外模式自动切换为 PrintWindow 截图（mss无法截取屏幕外区域）。

    Args:
        window: 微信窗口对象
        ratio: 截取底部比例（默认35%）

    Returns:
        numpy.ndarray 或 None
    """
    from window_manager import get_chat_region, _get_hwnd

    left, top, width, height = get_chat_region(window)

    # 屏幕外模式：mss截不到屏幕外，用 PrintWindow 全窗截图后裁剪
    if left < -1000 or top < -1000:
        hwnd = _get_hwnd(window)
        if hwnd:
            full = capture_via_printwindow(hwnd)
            if full is not None:
                chat = crop_chat_region_img(full, bottom_ratio=ratio)
                logger.info("[屏幕外截图] PrintWindow裁剪聊天区底部: %s",
                            None if chat is None else "%dx%d" % (chat.shape[1], chat.shape[0]))
                return chat
        logger.warning("[屏幕外截图] PrintWindow失败，无法截取屏幕外窗口")
        return None

    bottom_height = int(height * ratio)
    bottom_top = top + height - bottom_height
    return capture_region((left, bottom_top, width, bottom_height))

def ensure_window_rendering(hwnd, window=None):
    """
    截图前自愈：确保窗口处于"恢复态+屏幕外"，尺寸正常。
    最小化窗口(237x56隐形窗口)截图必失败，必须先恢复。
    屏幕外窗口(-10000,0)尺寸正常时直接用，截图由PrintWindow或短暂恢复处理。

    Returns:
        (left, top, width, height) 有效的窗口矩形，失败返回 None
    """
    import ctypes
    import win32gui
    import win32con
    import time

    if not hwnd or not win32gui.IsWindow(hwnd):
        logger.warning("[自愈] 句柄无效: %s", hwnd)
        return None

    orig = None
    is_offscreen_managed = False
    try:
        from window_manager import _offscreen_original, OFFSCREEN_X, OFFSCREEN_Y
        orig = _offscreen_original.get(hwnd)
        if orig:
            is_offscreen_managed = True
    except Exception:
        pass

    try:
        is_min = win32gui.IsIconic(hwnd)
        is_vis = win32gui.IsWindowVisible(hwnd) != 0
    except Exception:
        is_min, is_vis = False, True

    rect = win32gui.GetWindowRect(hwnd)
    w, h = rect[2] - rect[0], rect[3] - rect[1]
    left, top = rect[0], rect[1]
    logger.info("[自愈] 状态: 最小化=%s, 可见=%s, 矩形=(%d,%d) %dx%d, offscreen_managed=%s",
                is_min, is_vis, left, top, w, h, is_offscreen_managed)

    if is_min:
        # ★ 关键：先设位置到屏幕外（仍最小化），再ShowWindow取消最小化
        #   和 move_window_offscreen 同样逻辑：窗口直接在屏幕外变为可见，不闪现
        logger.info("[自愈] 窗口最小化 → 先设位置到屏幕外再显示（不闪现）")
        try:
            # 获取正确尺寸（最小化窗口GetWindowRect返回的是237x56）
            if orig:
                rw, rh = orig[2], orig[3]
            else:
                try:
                    from window_manager import _get_normal_rect
                    normal = _get_normal_rect(hwnd)
                    rw = normal[2] - normal[0]
                    rh = normal[3] - normal[1]
                except Exception:
                    rw, rh = w, h

            # 先设位置（仍最小化）
            win32gui.SetWindowPos(
                hwnd, 0, OFFSCREEN_X, OFFSCREEN_Y, rw, rh,
                win32con.SWP_NOACTIVATE | win32con.SWP_NOZORDER
            )
            time.sleep(0.1)
            # 再取消最小化（窗口已在屏幕外，用户看不到）
            win32gui.ShowWindow(hwnd, win32con.SW_SHOWNOACTIVATE)
            time.sleep(0.2)
        except Exception as e:
            logger.warning("[自愈] 恢复失败: %s", e)
            return None
    elif not is_vis:
        logger.info("[自愈] 窗口被隐藏 → SW_SHOWNOACTIVATE...")
        try:
            win32gui.ShowWindow(hwnd, win32con.SW_SHOWNOACTIVATE)
            time.sleep(0.2)
        except Exception as e:
            logger.warning("[自愈] SW_SHOWNOACTIVATE失败: %s", e)

    rect = win32gui.GetWindowRect(hwnd)
    w, h = rect[2] - rect[0], rect[3] - rect[1]
    left, top = rect[0], rect[1]

    # 尺寸异常时重设
    if w < 300 or h < 200:
        size_src = None
        if orig:
            size_src = (orig[2], orig[3], "记录的原始尺寸")
        else:
            try:
                from window_manager import _get_normal_rect
                normal = _get_normal_rect(hwnd)
                if normal:
                    size_src = (normal[2] - normal[0], normal[3] - normal[1], "Placement还原矩形")
            except Exception:
                pass

        if size_src:
            sw_, sh_, sname = size_src
            target_x = OFFSCREEN_X if is_offscreen_managed else left
            target_y = OFFSCREEN_Y if is_offscreen_managed else 0
            logger.warning("[自愈] 尺寸异常(%dx%d)，用%s重设到(%d,%d)", w, h, sname, target_x, target_y)
            win32gui.SetWindowPos(hwnd, 0, target_x, target_y, sw_, sh_,
                                  win32con.SWP_NOACTIVATE | win32con.SWP_SHOWWINDOW)
            time.sleep(0.3)
            rect = win32gui.GetWindowRect(hwnd)
            w, h = rect[2] - rect[0], rect[3] - rect[1]
            left, top = rect[0], rect[1]
        else:
            logger.error("[自愈] 窗口尺寸异常(%dx%d)，无可用尺寸来源", w, h)

    if w < 300 or h < 200:
        logger.error("[自愈] ✖ 窗口尺寸异常(%dx%d)，放弃截图", w, h)
        return None

    # 强制重绘
    try:
        RDW_INVALIDATE = 0x0001
        RDW_UPDATENOW = 0x0100
        RDW_ALLCHILDREN = 0x0080
        ctypes.windll.user32.RedrawWindow(
            hwnd, None, None, RDW_INVALIDATE | RDW_UPDATENOW | RDW_ALLCHILDREN)
        time.sleep(0.1)
    except Exception:
        pass

    result = (left, top, w, h)

    if window is not None:
        window.left, window.top, window.width, window.height = result

    logger.info("[自愈] ✔ 窗口就绪: (%d,%d) %dx%d", left, top, w, h)
    return result


def save_debug_image(img, prefix="preview", max_keep=30):
    """
    保存诊断截图到 debug/preview/（PIL方式，支持中文路径）。
    自动清理旧文件，只保留最新 max_keep 张。

    Args:
        img: numpy.ndarray (BGR) 或 None
        prefix: 文件名前缀（preview/capture/fail 等）
        max_keep: 最多保留张数

    Returns:
        保存路径 或 None
    """
    if img is None:
        return None
    try:
        from datetime import datetime
        from PIL import Image
        save_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "debug", "preview")
        os.makedirs(save_dir, exist_ok=True)

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = os.path.join(save_dir, f"{prefix}_{ts}.png")

        # BGR -> RGB
        img_rgb = img[:, :, ::-1].copy()
        Image.fromarray(img_rgb).save(path)
        logger.info("[存图] %s: %s (%dx%d, mean=%.1f)",
                    prefix, os.path.basename(path), img.shape[1], img.shape[0], img.mean())

        # 清理旧文件
        files = sorted(
            [f for f in os.listdir(save_dir) if f.startswith(prefix + "_") and f.endswith(".png")])
        while len(files) > max_keep:
            try:
                os.remove(os.path.join(save_dir, files.pop(0)))
            except Exception:
                break
        return path
    except Exception as e:
        logger.warning("[存图] 保存失败: %s", e)
        return None


def capture_via_bitblt(hwnd):
    """
    BitBlt 后台截图（PrintWindow的备选方案）。
    从窗口DC直接复制像素，部分窗口（Qt/Chromium）比PrintWindow更有效。

    Returns:
        numpy.ndarray (BGR) 或 None
    """
    import win32gui
    import win32ui
    import win32con

    try:
        rect = win32gui.GetWindowRect(hwnd)
        w = max(rect[2] - rect[0], 1)
        h = max(rect[3] - rect[1], 1)

        hdc_window = win32gui.GetWindowDC(hwnd)
        mfcDC = win32ui.CreateDCFromHandle(hdc_window)
        saveDC = mfcDC.CreateCompatibleDC()

        saveBitMap = win32ui.CreateBitmap()
        saveBitMap.CreateCompatibleBitmap(mfcDC, w, h)
        saveDC.SelectObject(saveBitMap)

        # BitBlt 直接从窗口DC复制
        result = saveDC.BitBlt((0, 0), (w, h), mfcDC, (0, 0), win32con.SRCCOPY)

        bmpinfo = saveBitMap.GetInfo()
        bmpstr = saveBitMap.GetBitmapBits(True)
        img = np.frombuffer(bmpstr, dtype=np.uint8)
        img = img.reshape(bmpinfo['bmHeight'], bmpinfo['bmWidth'], 4)[:, :, :3]

        saveDC.DeleteDC()
        mfcDC.DeleteDC()
        win32gui.ReleaseDC(hwnd, hdc_window)
        win32gui.DeleteObject(saveBitMap.GetHandle())

        if img.mean() < 1:
            logger.warning("[BitBlt] 截图全黑(mean=%.1f)", img.mean())
            return None
        logger.info("[BitBlt] ✔ 截图成功: %dx%d, mean=%.1f", w, h, img.mean())
        return img
    except Exception as e:
        logger.warning("[BitBlt] 异常: %s", e)
        return None


def _read_bitmap_to_bgr(save_bitmap):
    """把已选入内存DC的位图读成 numpy BGR 数组。"""
    bmpinfo = save_bitmap.GetInfo()
    bmp_w = bmpinfo['bmWidth']
    bmp_h = bmpinfo['bmHeight']
    bmpstr = save_bitmap.GetBitmapBits(True)
    img = np.frombuffer(bmpstr, dtype=np.uint8)
    img = img.reshape(bmp_h, bmp_w, 4)  # BGRA
    return img[:, :, :3]  # BGR


def _get_frame_rect_dwm(hwnd):
    """用 DWM API 取窗口真实可见框架矩形（剔除 Win10/11 隐形缩放边框）。
    GetWindowRect 会带上左右/底部约 7px 的隐形边框，且 DPI 缩放下有偏移；
    DwmGetWindowAttribute(EXTENDED_FRAME_BOUNDS) 返回真实可见边界，适合做区域裁剪。
    Returns (left, top, right, bottom) 或 None
    """
    import ctypes
    from ctypes import wintypes
    DWMWA_EXTENDED_FRAME_BOUNDS = 9
    try:
        rect = wintypes.RECT()
        hr = ctypes.windll.dwmapi.DwmGetWindowAttribute(
            wintypes.HWND(hwnd),
            wintypes.DWORD(DWMWA_EXTENDED_FRAME_BOUNDS),
            ctypes.byref(rect),
            wintypes.DWORD(ctypes.sizeof(rect)),
        )
        if hr == 0:  # S_OK
            return (rect.left, rect.top, rect.right, rect.bottom)
    except Exception:
        pass
    return None


def capture_via_printwindow(hwnd):
    """
    用 PrintWindow 对屏幕外窗口截图（Qt/QML/Chromium 适配版）。

    屏幕外窗口(-10000,0)的PrintWindow可能返回黑图（Qt停止GPU渲染），
    多次RedrawWindow尝试触发重绘，黑图时用短暂恢复方案兜底。

    关键修复：
    1. 多次 RedrawWindow 强制 Qt 提交渲染
    2. flags=3 (PW_CLIENTONLY|PW_RENDERFULLCONTENT) 优先
    3. 黑图即降级，逐个尝试 flags
    4. 最终兜底：短暂恢复到可见位置 → 截图 → 移回屏幕外

    Args:
        hwnd: 窗口句柄

    Returns:
        numpy.ndarray (BGR) 或 None
    """
    import ctypes
    import time
    import win32con
    import win32gui
    import win32ui

    PW_RENDERFULLCONTENT = 0x00000002
    PW_CLIENTONLY = 0x00000001
    WM_PAINT = 0x000F
    RDW_INVALIDATE = 0x0001
    RDW_UPDATENOW = 0x0100
    RDW_ALLCHILDREN = 0x0080

    try:
        client_rect = win32gui.GetClientRect(hwnd)
        w = client_rect[2] - client_rect[0]
        h = client_rect[3] - client_rect[1]
        if w <= 0 or h <= 0:
            frame = _get_frame_rect_dwm(hwnd) or win32gui.GetWindowRect(hwnd)
            w = frame[2] - frame[0]
            h = frame[3] - frame[1]
        if w <= 0 or h <= 0:
            logger.warning("[PrintWindow] 窗口尺寸无效: hwnd=%s", hwnd)
            return None

        logger.info("[PrintWindow] 准备截图: hwnd=%s, 客户区尺寸=%dx%d", hwnd, w, h)

        # 多次强制Qt重绘，确保GPU渲染提交
        for attempt in range(2):
            try:
                ctypes.windll.user32.RedrawWindow(
                    hwnd, None, None,
                    RDW_INVALIDATE | RDW_UPDATENOW | RDW_ALLCHILDREN)
                ctypes.windll.user32.UpdateWindow(hwnd)
                time.sleep(0.2)
            except Exception:
                pass

        hdc_window = win32gui.GetWindowDC(hwnd)
        mfcDC = win32ui.CreateDCFromHandle(hdc_window)
        saveDC = mfcDC.CreateCompatibleDC()
        saveBitMap = win32ui.CreateBitmap()
        saveBitMap.CreateCompatibleBitmap(mfcDC, w, h)
        saveDC.SelectObject(saveBitMap)

        try:
            for flags, method in [
                (PW_CLIENTONLY | PW_RENDERFULLCONTENT, "FLAGS3(clientonly+renderfull)"),
                (PW_RENDERFULLCONTENT, "FLAGS2(renderfull)"),
                (0, "FLAGS0(normal)"),
            ]:
                try:
                    saveDC.PatBlt((0, 0, w, h), win32con.BLACKNESS)
                except Exception:
                    pass

                ok = ctypes.windll.user32.PrintWindow(hwnd, saveDC.GetSafeHdc(), flags)
                if not ok:
                    logger.info("[PrintWindow] %s 调用失败，尝试下一方案", method)
                    continue

                img = _read_bitmap_to_bgr(saveBitMap)
                mean_val = float(img.mean())
                std_val = float(img.std())
                logger.info("[PrintWindow] %s: %dx%d, 均值=%.1f, 标准差=%.1f",
                            method, img.shape[1], img.shape[0], mean_val, std_val)

                if mean_val < 1:
                    logger.warning("[PrintWindow] %s 截到黑图(mean=%.1f)，降级重试", method, mean_val)
                    continue
                if std_val < 5:
                    logger.warning("[PrintWindow] %s 截到纯色(std=%.1f)，降级重试", method, std_val)
                    continue

                # ★ 记录截图内容是否为客户区（决定坐标是否需减去标题栏高）
                global _last_pw_client_only
                _last_pw_client_only = bool(flags & PW_CLIENTONLY)

                logger.info("[PrintWindow] ✔ 成功(%s): %dx%d", method, img.shape[1], img.shape[0])
                return img

            # 所有flags均为黑图 → 兜底：短暂恢复窗口后截图
            logger.warning("[PrintWindow] 所有flags均为黑图，尝试短暂恢复窗口截图")
            img = _capture_via_temporary_restore(hwnd, w, h)
            if img is not None:
                return img
            return None
        finally:
            saveDC.DeleteDC()
            mfcDC.DeleteDC()
            win32gui.ReleaseDC(hwnd, hdc_window)
            win32gui.DeleteObject(saveBitMap.GetHandle())

    except Exception as e:
        logger.error("[PrintWindow] 异常: %s", e)
        import traceback
        logger.error(traceback.format_exc())
        return None


def _capture_via_temporary_restore(hwnd, w, h):
    """
    兜底截图：PrintWindow全黑时的最后手段。
    策略：先设位置到屏幕边缘（仍最小化），再ShowWindow → 截图 → 移回屏幕外。
    窗口只在屏幕边缘短暂出现，尽量减少用户感知。
    """
    import win32gui
    import win32con
    import ctypes
    import time
    import win32ui

    logger.info("[兜底] PrintWindow黑屏，短暂移到屏幕边缘截图...")

    try:
        rect = win32gui.GetWindowRect(hwnd)
        orig_x, orig_y = rect[0], rect[1]
        orig_w = rect[2] - rect[0]
        orig_h = rect[3] - rect[1]
    except Exception:
        orig_x, orig_y, orig_w, orig_h = 0, 0, w, h

    # 获取原始尺寸（最小化窗口GetWindowRect返回的是237x56）
    real_w, real_h = orig_w, orig_h
    try:
        from window_manager import _offscreen_original
        orig = _offscreen_original.get(hwnd)
        if orig:
            real_w, real_h = orig[2], orig[3]
    except Exception:
        pass
    if real_w < 200 or real_h < 200:
        try:
            from window_manager import _get_normal_rect
            normal = _get_normal_rect(hwnd)
            if normal:
                real_w = normal[2] - normal[0]
                real_h = normal[3] - normal[1]
        except Exception:
            pass

    screen_w = ctypes.windll.user32.GetSystemMetrics(0)
    screen_h = ctypes.windll.user32.GetSystemMetrics(1)
    # 移到屏幕右下角，大部分在屏幕外
    flash_x = screen_w - 10
    flash_y = screen_h - real_h - 5

    was_minimized = win32gui.IsIconic(hwnd)
    img = None
    # ★ 上交互锁：防止截图恢复期间 keep_alive/watcher 误判并干预窗口
    try:
        from window_manager import _acquire_interaction, _release_interaction
        _acquire_interaction()
        time.sleep(0.05)
    except Exception:
        _acquire_interaction = None
    try:
        # ★ 同样逻辑：先设位置（仍最小化），再ShowWindow
        win32gui.SetWindowPos(
            hwnd, win32con.HWND_TOPMOST,
            flash_x, flash_y, real_w, real_h,
            win32con.SWP_NOACTIVATE | win32con.SWP_NOZORDER
        )
        time.sleep(0.1)

        if was_minimized:
            win32gui.ShowWindow(hwnd, win32con.SW_SHOWNOACTIVATE)
            time.sleep(0.2)
        else:
            win32gui.SetWindowPos(
                hwnd, win32con.HWND_TOPMOST,
                flash_x, flash_y, real_w, real_h,
                win32con.SWP_NOACTIVATE | win32con.SWP_SHOWWINDOW
            )
            time.sleep(0.2)

        RDW_INVALIDATE = 0x0001
        RDW_UPDATENOW = 0x0100
        RDW_ALLCHILDREN = 0x0080
        ctypes.windll.user32.RedrawWindow(
            hwnd, None, None, RDW_INVALIDATE | RDW_UPDATENOW | RDW_ALLCHILDREN)
        ctypes.windll.user32.UpdateWindow(hwnd)
        time.sleep(0.15)

        frame = _get_frame_rect_dwm(hwnd) or win32gui.GetWindowRect(hwnd)
        cw = frame[2] - frame[0]
        ch = frame[3] - frame[1]

        hdc_window = win32gui.GetWindowDC(hwnd)
        mfcDC = win32ui.CreateDCFromHandle(hdc_window)
        saveDC = mfcDC.CreateCompatibleDC()
        saveBitMap = win32ui.CreateBitmap()
        saveBitMap.CreateCompatibleBitmap(mfcDC, cw, ch)
        saveDC.SelectObject(saveBitMap)

        PW_RENDERFULLCONTENT = 0x00000002
        PW_CLIENTONLY = 0x00000001
        
        for flags, method in [
            (PW_CLIENTONLY | PW_RENDERFULLCONTENT, "FLAGS3"),
            (PW_RENDERFULLCONTENT, "FLAGS2"),
            (0, "FLAGS0"),
        ]:
            try:
                saveDC.PatBlt((0, 0, cw, ch), win32con.BLACKNESS)
            except Exception:
                pass
            
            ok = ctypes.windll.user32.PrintWindow(hwnd, saveDC.GetSafeHdc(), flags)
            if not ok:
                continue
            
            img = _read_bitmap_to_bgr(saveBitMap)
            if img.mean() > 1 and img.std() > 5:
                # ★ 记录截图内容是否为客户区（决定坐标是否需减去标题栏高）
                global _last_pw_client_only
                _last_pw_client_only = bool(flags & PW_CLIENTONLY)
                logger.info("[兜底] ✔ 截图成功(%s): %dx%d, mean=%.1f",
                            method, img.shape[1], img.shape[0], img.mean())
                break
            img = None

        saveDC.DeleteDC()
        mfcDC.DeleteDC()
        win32gui.ReleaseDC(hwnd, hdc_window)
        win32gui.DeleteObject(saveBitMap.GetHandle())

    except Exception as e:
        logger.warning("[兜底] 异常: %s", e)
        img = None
    finally:
        # 恢复到屏幕外（直接SetWindowPos即可，不需要再ShowWindow）
        try:
            from window_manager import OFFSCREEN_X, OFFSCREEN_Y
            win32gui.SetWindowPos(
                hwnd, win32con.HWND_NOTOPMOST,
                OFFSCREEN_X, OFFSCREEN_Y, real_w, real_h,
                win32con.SWP_NOACTIVATE | win32con.SWP_SHOWWINDOW |
                win32con.SWP_NOZORDER
            )
            logger.info("[兜底] 窗口已恢复到屏幕外(%d,%d)", OFFSCREEN_X, OFFSCREEN_Y)
        except Exception:
            pass
        # ★ 解除交互锁（不重启watcher，由调用方主循环决定）
        try:
            if _acquire_interaction is not None:
                _release_interaction()
        except Exception:
            pass

    return img


def capture_minimized_window(window, hwnd=None, skip_printwindow=False, flash_position="corner"):
    """
    对最小化/后台窗口截图。
    
    Qt/Chromium 框架的微信不支持 PrintWindow 和屏幕外渲染，
    唯一可靠方案：短暂恢复窗口到可见位置 → 截图 → 立即最小化回去。
    
    Args:
        window: 微信窗口对象
        hwnd: 窗口句柄（可选，自动获取）
        skip_printwindow: 跳过 PrintWindow 直接用恢复方案
        flash_position: 窗口短暂显示位置:
            - "corner": 右下角角落（默认，最不遮挡）
            - "original": 原始位置
            - "topright": 右上角
            - "center": 屏幕中央
    """
    import win32gui
    import win32con
    import time

    if not hwnd:
        hwnd = getattr(window, '_hWnd', None)
    if not hwnd:
        title = getattr(window, 'title', '')
        if title:
            hwnd = win32gui.FindWindow(None, title)
    if not hwnd:
        logger.warning("[最小化截图] 无法获取窗口句柄")
        return None

    # 校验句柄有效性
    if not win32gui.IsWindow(hwnd):
        logger.error("[最小化截图] 窗口句柄无效: %s", hwnd)
        return None

    was_minimized = win32gui.IsIconic(hwnd)
    logger.info("[最小化截图] 开始: hwnd=%s, 最小化=%s", hwnd, was_minimized)

    # 方案A：PrintWindow（仅对非Qt应用有效，Qt微信基本无效）
    if not skip_printwindow:
        img = capture_via_printwindow(hwnd)
        if img is not None and img.mean() > 5 and img.std() > 5:
            logger.info("[最小化截图] ✔ PrintWindow 成功: %dx%d, mean=%.1f",
                        img.shape[1], img.shape[0], img.mean())
            return img
        elif img is not None:
            logger.warning("[最小化截图] PrintWindow 无内容(mean=%.1f)，将用恢复方案", img.mean())
        else:
            logger.info("[最小化截图] PrintWindow 不可用，将用恢复方案")

    # 方案B：恢复窗口到可见位置截图（Qt框架唯一可靠方案）
    logger.info("[最小化截图] ⚡ 方案B：短暂恢复窗口截图")
    
    # 保存原始窗口位置和大小
    try:
        orig_rect = win32gui.GetWindowRect(hwnd)
        orig_left, orig_top = orig_rect[0], orig_rect[1]
        orig_width = orig_rect[2] - orig_rect[0]
        orig_height = orig_rect[3] - orig_rect[1]
        logger.info("[最小化截图] 原始位置: (%d,%d) %dx%d", orig_left, orig_top, orig_width, orig_height)
    except Exception:
        orig_left, orig_top, orig_width, orig_height = 0, 0, 800, 600

    # 获取屏幕尺寸
    try:
        import win32api
        screen_w = win32api.GetSystemMetrics(0)
        screen_h = win32api.GetSystemMetrics(1)
    except Exception:
        screen_w, screen_h = 1920, 1080

    # 计算窗口短暂显示位置
    if flash_position == "corner":
        flash_x = max(0, screen_w - orig_width - 20)
        flash_y = max(0, screen_h - orig_height - 60)
    elif flash_position == "topright":
        flash_x = max(0, screen_w - orig_width - 20)
        flash_y = 20
    elif flash_position == "center":
        flash_x = max(0, (screen_w - orig_width) // 2)
        flash_y = max(0, (screen_h - orig_height) // 2)
    elif flash_position == "original":
        flash_x, flash_y = orig_left, orig_top
    else:
        flash_x, flash_y = orig_left, orig_top

    logger.info("[最小化截图] 目标位置: (%d,%d) %dx%d, 屏幕: %dx%d",
                flash_x, flash_y, orig_width, orig_height, screen_w, screen_h)

    img = None
    # ★ 上交互锁：防止截图恢复期间 keep_alive 误判干预（标题/前台检测）
    _lock_applied = False
    try:
        from window_manager import _acquire_interaction, _release_interaction
        _acquire_interaction()
        time.sleep(0.05)
        _lock_applied = True
    except Exception:
        pass
    try:
        # 步骤1：恢复窗口（不抢焦点）
        if was_minimized:
            win32gui.ShowWindow(hwnd, win32con.SW_SHOWNOACTIVATE)
            time.sleep(0.1)

        # 步骤2：移到目标位置并置顶
        win32gui.SetWindowPos(
            hwnd, win32con.HWND_TOPMOST,
            flash_x, flash_y, orig_width, orig_height,
            win32con.SWP_SHOWWINDOW | win32con.SWP_NOACTIVATE
        )
        
        # 步骤3：等待 Qt 渲染（必须足够长，Qt渲染约1-2秒）
        render_wait = 1.5
        logger.info("[最小化截图] 等待 Qt 渲染 %.1f 秒...", render_wait)
        time.sleep(render_wait)

        # 步骤4：获取当前窗口位置
        curr_rect = win32gui.GetWindowRect(hwnd)
        curr_left, curr_top = curr_rect[0], curr_rect[1]
        curr_width = curr_rect[2] - curr_rect[0]
        curr_height = curr_rect[3] - curr_rect[1]
        
        window.left = curr_left
        window.top = curr_top
        window.width = curr_width
        window.height = curr_height

        logger.info("[最小化截图] 当前位置: (%d,%d) %dx%d", curr_left, curr_top, curr_width, curr_height)

        # 步骤5：mss 截图
        logger.info("[最小化截图] mss截图中...")
        img = capture_region((curr_left, curr_top, curr_width, curr_height))

        if img is not None:
            logger.info("[最小化截图] ✔ 截图成功: %dx%d, mean=%.1f",
                        img.shape[1], img.shape[0], img.mean())
            if img.mean() < 5:
                logger.warning("[最小化截图] 截图全黑(mean=%.1f)，重试一次...", img.mean())
                time.sleep(1.0)
                img = capture_region((curr_left, curr_top, curr_width, curr_height))
                if img is not None:
                    logger.info("[最小化截图] 重试成功: %dx%d, mean=%.1f",
                                img.shape[1], img.shape[0], img.mean())
        else:
            logger.error("[最小化截图] mss截图返回None")

    except Exception as e:
        logger.error("[最小化截图] 恢复截图异常: %s", e)
        import traceback
        logger.error(traceback.format_exc())
        return None
    finally:
        # 步骤6：恢复原状态（最小化 or 屏幕外），不能留在桌面上
        try:
            from window_manager import _offscreen_original, OFFSCREEN_X, OFFSCREEN_Y
            was_offscreen_managed = _offscreen_original.get(hwnd) is not None
        except Exception:
            was_offscreen_managed = False
        try:
            if was_offscreen_managed:
                # offscreen管理模式：窗口回屏幕外，绝不留在桌面
                try:
                    _ow, _oh = _offscreen_original[hwnd][2], _offscreen_original[hwnd][3]
                except Exception:
                    _ow, _oh = orig_width, orig_height
                win32gui.SetWindowPos(
                    hwnd, win32con.HWND_NOTOPMOST,
                    OFFSCREEN_X, OFFSCREEN_Y, _ow, _oh,
                    win32con.SWP_NOACTIVATE | win32con.SWP_SHOWWINDOW | win32con.SWP_NOZORDER
                )
                logger.info("[最小化截图] ✔ 已恢复到屏幕外(%d,%d)", OFFSCREEN_X, OFFSCREEN_Y)
            elif was_minimized:
                win32gui.ShowWindow(hwnd, win32con.SW_MINIMIZE)
                logger.info("[最小化截图] ✔ 已恢复最小化")
        except Exception as e:
            logger.error("[最小化截图] 恢复窗口状态失败: %s", e)
        # ★ 解除交互锁
        if _lock_applied:
            try:
                _release_interaction()
            except Exception:
                pass

    return img



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