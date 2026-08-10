"""
微信内置OCR引擎 — 调用微信自带的 WeChatOCR.exe 进行文字识别
=============================================================
优势：
  - 微信自家的OCR，对微信界面文字识别精度极高
  - 速度快（本地C++引擎，非Python推理）
  - 带坐标信息（left/top/right/bottom）
  - 对小字、模糊文字识别率优于PaddleOCR

工作原理：
  1. 自动探测微信安装路径和WeChatOCR.exe路径
  2. 通过wechat_ocr包的OcrManager启动OCR服务
  3. 将numpy图片保存为临时文件 → DoOCRTask → 回调获取结果
  4. 转换为与ocr_engine.py一致的输出格式

依赖：
  pip install wechat-ocr
  需要已安装微信客户端
"""
import os
import sys
import time
import tempfile
import logging
import threading
import numpy as np
import cv2

logger = logging.getLogger(__name__)

# 全局单例
_wechat_ocr_manager = None
_wechat_ocr_available = None  # None=未检测, True/False
_wechat_ocr_lock = threading.Lock()

# 临时图片文件计数器
_temp_counter = 0
_temp_lock = threading.Lock()


def _detect_wechat_paths():
    """
    自动探测微信安装路径和WeChatOCR.exe路径。
    搜索顺序：
      1. 注册表 HKCU/HKLM Software\Tencent\WeChat
      2. APPDATA下的XPlugin目录
      3. 常见安装路径
      4. 运行中的微信进程
    Returns:
        (wechat_dir, ocr_exe_path) or (None, None)
    """
    import winreg

    wechat_dir = None
    ocr_exe_path = None

    # === 1. 注册表查微信安装路径 ===
    reg_paths = [
        (winreg.HKEY_CURRENT_USER, r"Software\Tencent\WeChat"),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\Tencent\WeChat"),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\WOW6432Node\Tencent\WeChat"),
    ]
    for hive, path in reg_paths:
        try:
            key = winreg.OpenKey(hive, path)
            try:
                wechat_dir, _ = winreg.QueryValueEx(key, "InstallPath")
            except FileNotFoundError:
                try:
                    wechat_dir, _ = winreg.QueryValueEx(key, "DisplayPath")
                except FileNotFoundError:
                    # 枚举所有值找路径
                    i = 0
                    while True:
                        try:
                            name, val, _ = winreg.EnumValue(key, i)
                            if isinstance(val, str) and os.path.isdir(val) and "WeChat" in val:
                                wechat_dir = val
                                break
                            i += 1
                        except OSError:
                            break
            winreg.CloseKey(key)
            if wechat_dir:
                logger.info(f"[微信OCR] 注册表找到微信路径: {wechat_dir}")
                break
        except FileNotFoundError:
            continue

    # === 2. 进程查微信路径（更可靠）===
    if not wechat_dir:
        try:
            import psutil
            for p in psutil.process_iter(["name", "exe"]):
                name = p.info.get("name", "")
                if name.lower() == "wechat.exe":
                    exe = p.info.get("exe", "")
                    if exe and os.path.exists(exe):
                        wechat_dir = os.path.dirname(exe)
                        logger.info(f"[微信OCR] 进程找到微信路径: {wechat_dir}")
                        break
        except Exception:
            pass

    # === 3. 常见路径 ===
    if not wechat_dir:
        appdata = os.environ.get("APPDATA", "")
        localappdata = os.environ.get("LOCALAPPDATA", "")
        candidates = [
            os.path.join(appdata, "Tencent", "WeChat"),
            os.path.join(localappdata, "Tencent", "WeChat"),
            r"C:\Program Files\Tencent\WeChat",
            r"C:\Program Files (x86)\Tencent\WeChat",
            r"D:\WeChat",
            r"D:\Program Files\Tencent\WeChat",
            r"D:\Tencent\WeChat",
            r"E:\WeChat",
        ]
        for c in candidates:
            if os.path.isdir(c) and os.path.exists(os.path.join(c, "WeChat.exe")):
                wechat_dir = c
                logger.info(f"[微信OCR] 常见路径找到: {wechat_dir}")
                break

    # === 4. 查WeChatOCR.exe ===
    if wechat_dir:
        # 方式A: APPDATA下的XPlugin
        appdata = os.environ.get("APPDATA", "")
        ocr_base = os.path.join(appdata, r"Tencent\WeChat\XPlugin\Plugins\WeChatOCR")
        if os.path.isdir(ocr_base):
            # 找最高版本号
            versions = sorted(os.listdir(ocr_base), reverse=True)
            for v in versions:
                exe = os.path.join(ocr_base, v, "extracted", "WeChatOCR.exe")
                if os.path.exists(exe):
                    ocr_exe_path = exe
                    logger.info(f"[微信OCR] 找到WeChatOCR.exe: {exe}")
                    break

        # 方式B: 微信安装目录下搜索
        if not ocr_exe_path:
            for root, dirs, files in os.walk(wechat_dir):
                if "WeChatOCR.exe" in files:
                    ocr_exe_path = os.path.join(root, "WeChatOCR.exe")
                    logger.info(f"[微信OCR] 在微信目录下找到: {ocr_exe_path}")
                    break
                # 只搜两层深度
                if root.count(os.sep) - wechat_dir.count(os.sep) >= 2:
                    dirs.clear()

    return wechat_dir, ocr_exe_path


def is_wechat_ocr_available():
    """检查微信OCR是否可用（已安装wechat_ocr包 + 找到微信路径）"""
    global _wechat_ocr_available
    if _wechat_ocr_available is not None:
        return _wechat_ocr_available

    try:
        import wechat_ocr  # noqa
        wechat_dir, ocr_exe = _detect_wechat_paths()
        if wechat_dir and ocr_exe:
            _wechat_ocr_available = True
            logger.info(f"[微信OCR] 可用! 微信目录={wechat_dir}, OCR引擎={ocr_exe}")
        else:
            _wechat_ocr_available = False
            if not wechat_dir:
                logger.info("[微信OCR] 不可用: 未找到微信安装路径，回退到PaddleOCR")
            else:
                logger.info("[微信OCR] 不可用: 未找到WeChatOCR.exe，回退到PaddleOCR")
    except ImportError:
        _wechat_ocr_available = False
        logger.info("[微信OCR] 不可用: wechat-ocr包未安装(pip install wechat-ocr)，回退到PaddleOCR")
    except Exception as e:
        _wechat_ocr_available = False
        logger.warning(f"[微信OCR] 探测失败: {e}，回退到PaddleOCR")

    return _wechat_ocr_available


def _get_ocr_manager():
    """获取/初始化OcrManager单例（线程安全）"""
    global _wechat_ocr_manager

    if _wechat_ocr_manager is not None:
        return _wechat_ocr_manager

    with _wechat_ocr_lock:
        if _wechat_ocr_manager is not None:
            return _wechat_ocr_manager

        try:
            from wechat_ocr.ocr_manager import OcrManager

            wechat_dir, ocr_exe = _detect_wechat_paths()
            if not wechat_dir or not ocr_exe:
                raise FileNotFoundError("未找到微信安装路径或WeChatOCR.exe")

            manager = OcrManager(wechat_dir)
            manager.SetExePath(ocr_exe)
            manager.SetUsrLibDir(wechat_dir)

            # 用事件+结果存储实现同步调用
            result_store = {}
            done_event = threading.Event()

            def _ocr_callback(img_path, results):
                """OCR完成回调，存储结果并触发事件"""
                result_store["results"] = results
                done_event.set()

            manager.SetOcrResultCallback(_ocr_callback)
            manager.StartWeChatOCR()

            # 等待连接
            logger.info("[微信OCR] 正在启动OCR服务...")
            for _ in range(15):  # 最多等15秒
                if manager.m_connect_state.value:
                    logger.info("[微信OCR] OCR服务连接成功!")
                    break
                time.sleep(1)
            else:
                raise TimeoutError("微信OCR服务连接超时(15s)")

            # 把回调相关的变量绑定到manager上供后续使用
            manager._result_store = result_store
            manager._done_event = done_event
            manager._callback_lock = threading.Lock()

            _wechat_ocr_manager = manager
            return manager

        except Exception as e:
            logger.error(f"[微信OCR] 初始化失败: {e}")
            import traceback
            logger.error(traceback.format_exc()[-300:])
            _wechat_ocr_manager = None
            return None


def _save_temp_image(image):
    """将numpy图片保存为临时文件，返回路径"""
    global _temp_counter
    with _temp_lock:
        _temp_counter += 1
        idx = _temp_counter

    temp_dir = os.path.join(tempfile.gettempdir(), "wechat_ocr_temp")
    os.makedirs(temp_dir, exist_ok=True)
    temp_path = os.path.join(temp_dir, f"ocr_{int(time.time()*1000)}_{idx}.png")

    # numpy BGR → PIL → 保存（处理中文路径）
    from PIL import Image
    rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    Image.fromarray(rgb).save(temp_path)

    return temp_path


def _parse_wechat_ocr_result(results, min_confidence=0.0):
    """
    将微信OCR的返回格式转换为与PaddleOCR一致的格式。
    微信OCR返回: {"taskId": 1, "ocrResult": [{"text": "...", "location": {"left":..,"top":..,"right":..,"bottom":..}}]}
    我们需要: [{"text": "...", "confidence": 0.95, "bbox": [[x1,y1],...], "y_center": .., "x_center": ..}]
    """
    parsed = []
    ocr_result = results.get("ocrResult", [])

    for item in ocr_result:
        text = item.get("text", "").strip()
        if not text:
            continue

        loc = item.get("location", {})
        left = loc.get("left", 0) or 0
        top = loc.get("top", 0) or 0
        right = loc.get("right", 0) or 0
        bottom = loc.get("bottom", 0) or 0

        # 构造bbox（与PaddleOCR格式一致：4个角点）
        bbox = [
            [float(left), float(top)],
            [float(right), float(top)],
            [float(right), float(bottom)],
            [float(left), float(bottom)],
        ]

        # 微信OCR不返回置信度，设为1.0
        confidence = 1.0

        parsed.append({
            "text": text,
            "confidence": confidence,
            "bbox": bbox,
            "y_center": (top + bottom) / 2.0,
            "x_center": (left + right) / 2.0,
        })

    # 按Y排序
    parsed.sort(key=lambda r: r["y_center"])
    return parsed


def recognize_with_wechat_ocr(image, min_confidence=0.40):
    """
    用微信内置OCR识别图片文字（同步接口）。

    Args:
        image: numpy.ndarray (BGR格式)
        min_confidence: 最低置信度（微信OCR不返回置信度，此参数仅用于兼容）

    Returns:
        list of dict: 与ocr_engine.recognize()格式一致
    """
    if image is None:
        return []

    if not is_wechat_ocr_available():
        return None  # 返回None表示不可用，调用方应回退

    manager = _get_ocr_manager()
    if manager is None:
        return None

    try:
        # 保存临时图片
        temp_path = _save_temp_image(image)

        # 重置事件和结果
        with manager._callback_lock:
            manager._done_event.clear()
            manager._result_store.clear()

        # 执行OCR
        manager.DoOCRTask(temp_path)

        # 等待回调完成（最多30秒）
        if not manager._done_event.wait(timeout=30):
            logger.warning("[微信OCR] OCR超时(30s)，可能是服务异常")
            return []

        # 解析结果
        results = manager._result_store.get("results", {})
        parsed = _parse_wechat_ocr_result(results, min_confidence)

        # 清理临时文件
        try:
            os.remove(temp_path)
        except Exception:
            pass

        return parsed

    except Exception as e:
        logger.error(f"[微信OCR] 识别失败: {e}")
        return []
