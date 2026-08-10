"""
OCR引擎模块 — 多引擎支持（微信OCR / PaddleOCR）+ 识别优化
=====================================================
优化点：
1. 多引擎切换：优先使用微信内置OCR（精度高/速度快），自动回退PaddleOCR
2. 图片预处理：灰度+降噪+锐化+对比度增强
3. 置信度过滤：丢弃低质量识别结果
4. 同气泡多行文本自动合并：避免一条消息被拆成多条
5. 图片区域检测：跳过纯图片消息的无效OCR
6. 发送者判断改进：用气泡整体边界而非单行文字中心
"""
import logging
import re
import numpy as np
import cv2

logger = logging.getLogger(__name__)

# 全局单例
_ocr_instance = None
_ocr_engine = None  # "wechat" or "paddle"


def _init_engine():
    """初始化OCR引擎，优先微信OCR，回退PaddleOCR"""
    global _ocr_engine

    if _ocr_engine is not None:
        return _ocr_engine

    # 尝试微信OCR
    try:
        from wechat_ocr_engine import is_wechat_ocr_available
        if is_wechat_ocr_available():
            _ocr_engine = "wechat"
            logger.info("[OCR] 使用微信内置OCR引擎（精度高/速度快）")
            return _ocr_engine
    except Exception as e:
        logger.debug(f"[OCR] 微信OCR探测异常: {e}")

    # 回退PaddleOCR
    _ocr_engine = "paddle"
    logger.info("[OCR] 使用PaddleOCR引擎")
    return _ocr_engine


def get_ocr():
    """获取 PaddleOCR 单例。首次调用时加载模型（约3-5秒），后续复用。"""
    global _ocr_instance
    if _ocr_instance is None:
        from paddleocr import PaddleOCR
        logger.info("正在加载 PaddleOCR 模型（首次约3-5秒）...")
        _ocr_instance = PaddleOCR(
            use_angle_cls=True,
            lang="ch",
            use_gpu=False,
            show_log=False,
        )
        logger.info("PaddleOCR 模型加载完成")
    return _ocr_instance


def _preprocess_image(image, denoise=True):
    """
    图片预处理：提升OCR识别率。
    1. 转灰度（减少颜色干扰）
    2. 双边滤波降噪（保边缘去噪点）
    3. 锐化（让文字边缘更清晰）
    4. CLAHE对比度增强（让浅色文字更可见）
    """
    if image is None:
        return None

    # 转灰度
    if len(image.shape) == 3:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    else:
        gray = image.copy()

    if denoise:
        # 双边滤波：降噪但保留文字边缘
        gray = cv2.bilateralFilter(gray, d=5, sigmaColor=50, sigmaSpace=50)

        # 锐化核
        kernel = np.array([
            [0, -1, 0],
            [-1, 5, -1],
            [0, -1, 0]
        ], dtype=np.float32)
        gray = cv2.filter2D(gray, -1, kernel)

        # CLAHE 自适应直方图均衡化（增强对比度）
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        gray = clahe.apply(gray)

    # 转回3通道（PaddleOCR需要）
    result = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    return result


def _detect_image_regions(image, min_area_ratio=0.02):
    """
    检测图片消息区域（大面积纯色或低纹理区域）。
    微信里别人发的图片消息会有一大块均匀色块区域，OCR会识别出乱码。
    检测到后标记这些区域，让后续逻辑跳过。

    Returns:
        list of (x1, y1, x2, y2): 图片区域矩形列表
    """
    if image is None:
        return []

    h, w = image.shape[:2]
    min_area = h * w * min_area_ratio

    # 转灰度
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if len(image.shape) == 3 else image

    # 计算局部方差（低方差区域=纯色/图片区域）
    blur = cv2.GaussianBlur(gray, (31, 31), 0)
    diff = cv2.absdiff(gray, blur)
    _, low_texture = cv2.threshold(diff, 8, 255, cv2.THRESH_BINARY_INV)

    # 形态学操作连通区域
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (15, 15))
    low_texture = cv2.morphologyEx(low_texture, cv2.MORPH_CLOSE, kernel)
    low_texture = cv2.morphologyEx(low_texture, cv2.MORPH_OPEN, kernel)

    contours, _ = cv2.findContours(low_texture, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    image_regions = []
    for cnt in contours:
        area = cv2.contourArea(cnt)
        if area > min_area:
            x, y, w2, h2 = cv2.boundingRect(cnt)
            image_regions.append((x, y, x + w2, y + h2))

    return image_regions


def _is_in_image_region(bbox, image_regions, padding=10):
    """检查OCR结果是否落在图片区域内"""
    if not image_regions:
        return False
    cx = (bbox[0][0] + bbox[2][0]) / 2
    cy = (bbox[0][1] + bbox[2][1]) / 2
    for (x1, y1, x2, y2) in image_regions:
        if (x1 - padding <= cx <= x2 + padding and
                y1 - padding <= cy <= y2 + padding):
            return True
    return False


def _merge_bubble_lines(ocr_results, y_gap=20, x_overlap=0.3):
    """
    合并同一气泡内的多行文本。
    判断标准：
    1. Y坐标接近（间距<y_gap像素）
    2. X方向有重叠或相邻（同一气泡左右位置接近）

    合并后返回气泡级别的结果，每条包含完整文本和整体bbox。
    """
    if not ocr_results or len(ocr_results) <= 1:
        return ocr_results

    # 按Y排序
    sorted_results = sorted(ocr_results, key=lambda r: r["y_center"])

    merged = []
    current_group = [sorted_results[0]]

    for i in range(1, len(sorted_results)):
        prev = current_group[-1]
        curr = sorted_results[i]

        y_gap_actual = abs(curr["y_center"] - prev["y_center"])

        # 计算X方向重叠度
        prev_x1 = min(p[0] for p in prev["bbox"])
        prev_x2 = max(p[0] for p in prev["bbox"])
        curr_x1 = min(p[0] for p in curr["bbox"])
        curr_x2 = max(p[0] for p in curr["bbox"])

        overlap = max(0, min(prev_x2, curr_x2) - max(prev_x1, curr_x1))
        prev_width = max(1, prev_x2 - prev_x1)
        x_overlap_ratio = overlap / prev_width

        # 同一气泡：Y接近且X有重叠
        if y_gap_actual < y_gap and x_overlap_ratio > -x_overlap:
            current_group.append(curr)
        else:
            merged.append(_merge_group(current_group))
            current_group = [curr]

    if current_group:
        merged.append(_merge_group(current_group))

    return merged


def _merge_group(group):
    """将一组OCR结果合并为一个气泡结果"""
    if len(group) == 1:
        return group[0]

    # 合并文本（按Y排序，换行连接）
    group_sorted = sorted(group, key=lambda r: r["y_center"])
    texts = [r["text"] for r in group_sorted if r["text"].strip()]
    merged_text = "\n".join(texts)

    # 合并bbox
    all_x = [p[0] for r in group for p in r["bbox"]]
    all_y = [p[1] for r in group for p in r["bbox"]]
    x_min, x_max = min(all_x), max(all_x)
    y_min, y_max = min(all_y), max(all_y)

    bbox = [[x_min, y_min], [x_max, y_min], [x_max, y_max], [x_min, y_max]]

    # 平均置信度
    avg_conf = sum(r["confidence"] for r in group) / len(group)

    return {
        "text": merged_text,
        "confidence": avg_conf,
        "bbox": bbox,
        "y_center": (y_min + y_max) / 2,
        "x_center": (x_min + x_max) / 2,
        "line_count": len(group),
    }


def recognize(image, scale=1.0, min_confidence=0.40,
              merge_bubble=False, denoise=False):
    """
    对图像进行OCR识别（多引擎版）。
    优先使用微信内置OCR（精度高/速度快），不可用时回退PaddleOCR。

    Args:
        image: numpy.ndarray (BGR格式)
        scale: 缩放比例（0.85=清晰且快，1.0=最清晰但慢）
        min_confidence: 最低置信度阈值，低于此值丢弃
        merge_bubble: 是否合并同气泡多行文本
        denoise: 是否启用图片预处理

    Returns:
        list of dict: [
            {"text": "识别文本", "confidence": 0.95,
             "bbox": [[x1,y1],...], "y_center": .., "x_center": ..},
            ...
        ]
        按Y坐标从上到下排序
    """
    if image is None:
        return []

    # 初始化引擎选择
    engine = _init_engine()

    # === 引擎1: 微信内置OCR ===
    if engine == "wechat":
        try:
            from wechat_ocr_engine import recognize_with_wechat_ocr
            results = recognize_with_wechat_ocr(image, min_confidence)
            if results is not None:
                # 微信OCR成功，应用后处理
                if merge_bubble and len(results) > 1:
                    results = _merge_bubble_lines(results)
                logger.info(f"[微信OCR] 识别到 {len(results)} 条文本")
                return results
            # None表示不可用，回退
            logger.warning("[微信OCR] 不可用，回退到PaddleOCR")
        except Exception as e:
            logger.warning(f"[微信OCR] 识别异常: {e}，回退到PaddleOCR")

    # === 引擎2: PaddleOCR（回退） ===
    return _recognize_with_paddle(image, scale, min_confidence, merge_bubble, denoise)


def _recognize_with_paddle(image, scale=1.0, min_confidence=0.40,
                           merge_bubble=False, denoise=False):
    """PaddleOCR识别（原始逻辑）"""
    ocr = get_ocr()

    # === 步骤1: 图片预处理（默认禁用——预处理可能降低OCR精度） ===
    processed = image  # 直接用原图，PaddleOCR自带预处理

    # === 步骤2: 图片区域检测（已禁用——误伤聊天气泡白色背景，导致文字被过滤） ===
    image_regions = []

    # === 步骤3: 缩放 ===
    h, w = processed.shape[:2]
    if scale < 1.0:
        small = cv2.resize(processed, (int(w * scale), int(h * scale)))
        input_image = small
    else:
        input_image = processed

    # === 步骤4: OCR识别 ===
    try:
        results = ocr.ocr(input_image, cls=True)
    except Exception as e:
        logger.error(f"OCR识别失败: {e}")
        return []

    if not results or not results[0]:
        return []

    parsed = []
    for line in results[0]:
        bbox = line[0]
        text = line[1][0]
        confidence = line[1][1]

        # 坐标映射回原图
        if scale < 1.0:
            bbox = [[p[0] / scale, p[1] / scale] for p in bbox]

        text = text.strip()
        if not text:
            continue

        # 置信度过滤
        if confidence < min_confidence:
            logger.debug(f"丢弃低置信度结果: '{text[:20]}' conf={confidence:.2f}")
            continue

        # 图片区域过滤（跳过发图消息上的乱码）
        if _is_in_image_region(bbox, image_regions):
            logger.debug(f"跳过图片区域内的OCR: '{text[:20]}'")
            continue

        parsed.append({
            "text": text,
            "confidence": confidence,
            "bbox": bbox,
            "y_center": (bbox[0][1] + bbox[2][1]) / 2,
            "x_center": (bbox[0][0] + bbox[1][0]) / 2,
        })

    # === 步骤5: 合并同气泡多行文本 ===
    if merge_bubble and len(parsed) > 1:
        parsed = _merge_bubble_lines(parsed)

    # 按Y排序
    parsed.sort(key=lambda x: x["y_center"])

    return parsed


def get_all_texts(ocr_results):
    """从OCR结果中提取所有文本，按顺序返回"""
    return [r["text"] for r in ocr_results]


def get_text_at_bottom(ocr_results, ratio=0.3):
    """获取聊天区域底部的最新消息"""
    if not ocr_results:
        return []
    max_y = max(r["y_center"] for r in ocr_results)
    min_y = min(r["y_center"] for r in ocr_results)
    height_range = max_y - min_y if max_y > min_y else 1
    cutoff_y = max_y - height_range * ratio
    return [r for r in ocr_results if r["y_center"] >= cutoff_y]


def identify_senders(ocr_results, image):
    """
    根据视觉特征识别每条文本的发送者（"我"还是"对方"）。

    改进策略（按优先级）：
    1. 位置判断：对方消息靠左，我的消息靠右
       - 用气泡bbox的左边界判断（而非文字中心），更准确
    2. 颜色判断（兜底）：采样气泡背景像素，绿色=我，白色=对方

    Args:
        ocr_results: OCR识别结果列表
        image: 截图对应的numpy数组(BGR)

    Returns:
        带 sender 字段的结果列表（"me" 或 "other"）
    """
    if not ocr_results or image is None:
        return ocr_results

    h, w = image.shape[:2]

    for r in ocr_results:
        bbox = r["bbox"]
        # 用bbox左边界和右边界综合判断
        left_x = min(p[0] for p in bbox)
        right_x = max(p[0] for p in bbox)
        center_x = r.get("x_center", (left_x + right_x) / 2)

        # 策略1: 位置判断
        # 对方消息：左边界在窗口左1/3区域，且文字不从右侧开始
        # 我的消息：右边界接近窗口右侧，且文字不从左侧开始
        if left_x < w * 0.35 and center_x < w * 0.45:
            r["sender"] = "other"
        elif right_x > w * 0.65 and center_x > w * 0.55:
            r["sender"] = "me"
        elif center_x < w * 0.48:
            r["sender"] = "other"
        elif center_x > w * 0.52:
            r["sender"] = "me"
        else:
            # 策略2: 颜色判断（居中模糊情况）
            r["sender"] = _detect_by_color(bbox, image, h, w)

    return ocr_results


def _detect_by_color(bbox, image, h, w):
    """
    采样文本周围像素，判断气泡颜色。
    绿色气泡=我，白色/浅色气泡=对方。

    改进：多点采样取多数投票，比单点更稳定。
    """
    left_x = int(min(p[0] for p in bbox))
    top_y = int(min(p[1] for p in bbox))
    right_x = int(max(p[0] for p in bbox))
    bottom_y = int(max(p[1] for p in bbox))

    # 在文本左侧多个位置采样
    green_votes = 0
    total_samples = 0

    sample_offsets = [-15, -10, -5]
    for offset in sample_offsets:
        sample_x = max(0, left_x + offset)
        sample_y = (top_y + bottom_y) // 2
        sample_y = min(max(0, sample_y), h - 1)

        if sample_x >= w:
            continue

        pixel = image[sample_y, sample_x].astype(np.float64)
        b_val, g_val, r_val = float(pixel[0]), float(pixel[1]), float(pixel[2])

        total_samples += 1
        # 绿色检测：G通道明显高于R和B
        if g_val > 100 and g_val > r_val * 1.10 and g_val > b_val * 1.15:
            green_votes += 1

    return "me" if green_votes > total_samples // 2 else "other"


def get_bubble_sender(bubble_items):
    """确定一个气泡的发送者：多数投票"""
    if not bubble_items:
        return "other"
    me_count = sum(1 for r in bubble_items if r.get("sender") == "me")
    return "me" if me_count > len(bubble_items) // 2 else "other"
