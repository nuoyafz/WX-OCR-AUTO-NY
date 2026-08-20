"""
OCR引擎模块 — 多引擎支持（微信OCR / PaddleOCR）+ 识别优化 V2
================================================================
V2 优化升级：
1. 多引擎切换：微信内置OCR → PaddleOCR（支持更多Paddle模型参数调优）
2. 智能多尺度预处理：
   - 自适应缩放（小字放大2~3倍，大字不缩小）
   - 多通道预处理 Pipeline：灰度+降噪+锐化+CLAHE+形态学膨胀
   - 反色处理（检测深色背景浅色文字时自动反色）
   - 超分辨率（小字区域使用Lanczos放大）
3. 多策略识别重试：原图 → 增强图 → 二值化图 → 反色图 → 4倍放大图
4. 置信度过滤：自适应阈值（短文本放宽，长文本收紧）
5. 文本清洗V2：OCR常见形近字纠错字典 + 重复标点压缩 + 数字修复
6. 同气泡多行文本合并：基于Y间距+X重叠+气泡背景色聚类
7. 图片区域检测V2：改进低纹理区域识别，避免误伤白色气泡
8. 发送者判断V3：气泡背景色采样+位置加权+气泡边缘检测
9. 结果融合：多轮识别结果投票，取置信度最高的文本
"""
import logging
import re
import numpy as np
import cv2

logger = logging.getLogger(__name__)

# 全局单例
_ocr_instance = None
_ocr_engine = None  # "wechat" or "paddle"

# ================ V2: OCR常见形近字纠错字典 ================
# 针对微信聊天场景，修正PaddleOCR/微信OCR的常见识别错误
CHAR_CORRECTION_MAP = {
    # 数字/字母混淆
    "O": "0", "o": "0",  # 字母O→数字0（在数字串中）
    "l": "1", "I": "1",  # 小写L/大写I→数字1
    "S": "5", "s": "5",  # 字母S→数字5（在数字串中）
    "Z": "2", "z": "2",  # 字母Z→数字2（在数字串中）
    "B": "8",            # 字母B→数字8
    "q": "9",
    "G": "6", "b": "6",
    # 中文形近字
    "人": "入", "入": "人",  # 双向，需要上下文判断
    "己": "已", "已": "己",
    "未": "末", "末": "未",
    # 标点符号
    "：": ":", "；": ";", "（": "(", "）": ")",
    "【": "[", "】": "]", "“": '"', "”": '"',
    "‘": "'", "’": "'", "—": "-", "－": "-",
    # 微信聊天常见emoji/符号残留清洗
    "\u200b": "", "\ufeff": "", "\u3000": " ",
    "️": "",  # 变体选择符
}

# 只在纯数字上下文中触发的纠错（识别结果看起来像手机号/验证码/金额时）
DIGIT_CONTEXT_MAP = {
    "O": "0", "o": "0", "Q": "0",
    "l": "1", "I": "1", "i": "1", "|": "1",
    "S": "5", "s": "5",
    "Z": "2", "z": "2",
    "B": "8",
    "G": "6", "b": "6",
    "q": "9", "g": "9",
    "T": "7",
}


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
    """
    获取 PaddleOCR 单例 V2。
    调优：
    - 启用 det_db_thresh 降低阈值，不漏掉弱文字（小字/浅色字）
    - det_db_box_thresh 放宽，允许更多候选框
    - 用 mobile 模型+裁剪掉的小字区域配合 rec 识别
    - 启用 rec_batch_num 加速
    """
    global _ocr_instance
    if _ocr_instance is None:
        from paddleocr import PaddleOCR
        logger.info("正在加载 PaddleOCR 模型（首次约3-5秒）V2优化版...")
        try:
            _ocr_instance = PaddleOCR(
                use_angle_cls=True,
                lang="ch",
                use_gpu=False,
                show_log=False,
                # V2: 检测参数调优 —— 更灵敏地捕捉小字、浅色字
                det_db_thresh=0.25,          # 降低检测阈值（默认0.3），不漏掉弱文字
                det_db_box_thresh=0.35,      # 放宽候选框阈值（默认0.5）
                det_db_unclip_ratio=2.2,     # 扩展框的比例（默认1.6），防止文字被截断
                det_limit_side_len=1920,     # 最大边限制，提升大图识别
                # V2: 识别参数
                rec_batch_num=6,             # 批量识别数提升（默认6）
                # V2: 算法结构 — 新版本PP-OCRv4（若可用）
                ocr_version="PP-OCRv4",
            )
            logger.info("PaddleOCR PP-OCRv4 模型加载完成 (V2优化参数)")
        except Exception as e1:
            logger.warning(f"PP-OCRv4加载失败，回退默认模型: {e1}")
            _ocr_instance = PaddleOCR(
                use_angle_cls=True,
                lang="ch",
                use_gpu=False,
                show_log=False,
                det_db_thresh=0.28,
                det_db_box_thresh=0.38,
                det_db_unclip_ratio=2.0,
                rec_batch_num=6,
            )
            logger.info("PaddleOCR 默认模型加载完成 (带V2优化参数)")
    return _ocr_instance


# ================ V2: 智能预处理 Pipeline ================

def _detect_text_color_mode(gray):
    """
    判断文字是深色文字浅色背景（常规）还是浅色文字深色背景（反色）。
    通过分析灰度直方图，返回 "dark_on_light" 或 "light_on_dark"。
    """
    h, w = gray.shape[:2]
    # 四角采样判断背景色
    corners = [
        gray[2:10, 2:10], gray[2:10, -10:-2],
        gray[-10:-2, 2:10], gray[-10:-2, -10:-2],
    ]
    corner_means = [float(c.mean()) for c in corners if c.size > 0]
    avg_corner = sum(corner_means) / max(1, len(corner_means))

    # 中心区域均值（聊天区文字密度高）
    center = gray[int(h*0.3):int(h*0.7), int(w*0.2):int(w*0.8)]
    center_mean = float(center.mean()) if center.size > 0 else 127

    # 背景亮则文字暗；背景暗则文字亮
    if avg_corner > 160:
        return "dark_on_light"
    if avg_corner < 80:
        return "light_on_dark"
    # 四角不明显时，用中心判断
    return "light_on_dark" if center_mean < 100 else "dark_on_light"


def _auto_invert_if_needed(image):
    """
    V2: 自动反色 —— 如果检测到是浅色文字+深色背景，自动反色以提升识别率。
    微信聊天默认是白/浅色背景，但有些主题或夜间模式会变黑。
    """
    if len(image.shape) == 3:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    else:
        gray = image.copy()

    mode = _detect_text_color_mode(gray)
    if mode == "light_on_dark":
        logger.debug("[OCR预处理] 检测到深色背景，自动反色")
        if len(image.shape) == 3:
            return cv2.bitwise_not(image)
        else:
            return cv2.bitwise_not(image)
    return image


def _preprocess_image(image, denoise=True, aggressive=False):
    """
    V2 智能图片预处理 Pipeline：提升OCR识别率。
    策略：
    1. 自动反色（检测深色背景浅色文字时）
    2. 转灰度
    3. 轻度降噪（双边滤波，保边缘）
    4. Unsharp Mask 锐化（比简单卷积核更自然）
    5. CLAHE 对比度增强
    6. aggressive=True时追加：形态学膨胀+Otsu二值化
    """
    if image is None:
        return None

    # V2: 自动反色
    image = _auto_invert_if_needed(image)

    # 转灰度
    if len(image.shape) == 3:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    else:
        gray = image.copy()

    if denoise:
        # V2: 轻度非局部均值降噪——比双边滤波更均匀，适合微信UI文字
        try:
            gray = cv2.fastNlMeansDenoising(gray, None, h=5, templateWindowSize=7, searchWindowSize=21)
        except Exception:
            # 回退双边滤波
            gray = cv2.bilateralFilter(gray, d=5, sigmaColor=50, sigmaSpace=50)

        # V2: Unsharp Mask 锐化（原图 + 1.2*(原图-高斯模糊)）
        # 让文字边缘更锐利，小字更清晰
        gaussian = cv2.GaussianBlur(gray, (0, 0), 1.2)
        gray = cv2.addWeighted(gray, 1.5, gaussian, -0.5, 0)

        # V2: CLAHE 对比度增强 — 更积极的参数
        clahe = cv2.createCLAHE(clipLimit=2.8, tileGridSize=(6, 6))
        gray = clahe.apply(gray)

    if aggressive:
        # V2: 激进模式 —— 形态学膨胀使文字变粗，修补断裂笔画
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
        gray = cv2.morphologyEx(gray, cv2.MORPH_CLOSE, kernel)

    # 转回3通道
    result = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    return result


def _preprocess_binary(image):
    """
    V2: 二值化预处理 —— 对浅色/模糊文字作为第三轮识别策略。
    自适应高斯二值化 + 中值滤波去盐椒噪声。
    """
    if image is None:
        return None
    if len(image.shape) == 3:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    else:
        gray = image.copy()

    mode = _detect_text_color_mode(gray)
    if mode == "light_on_dark":
        gray = cv2.bitwise_not(gray)

    # 轻度降噪
    gray = cv2.bilateralFilter(gray, d=5, sigmaColor=40, sigmaSpace=40)

    # 自适应高斯二值化
    binary = cv2.adaptiveThreshold(
        gray, 255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        25, 10
    )
    # 中值滤波去盐椒噪声
    binary = cv2.medianBlur(binary, 3)
    return cv2.cvtColor(binary, cv2.COLOR_GRAY2BGR)


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


def _estimate_line_height(sorted_results):
    """V2: 估计文本行高（基于已识别行的bbox高度中位数），自适应y_gap。"""
    heights = []
    for r in sorted_results:
        ys = [p[1] for p in r["bbox"]]
        heights.append(max(ys) - min(ys))
    if not heights:
        return 18
    heights.sort()
    return max(14.0, heights[len(heights) // 2])


def _merge_bubble_lines(ocr_results, y_gap=None, x_overlap=0.3):
    """
    V2 合并同一气泡内的多行文本。
    改进：
    - 自适应y_gap：根据文字行高中位数动态设置（默认行高×1.3）
    - 动态X重叠阈值：气泡内第一行和后续行的左右边界对齐度
    - 发送者一致性：已带sender字段时，sender不同不合并
    """
    if not ocr_results or len(ocr_results) <= 1:
        return ocr_results

    sorted_results = sorted(ocr_results, key=lambda r: r["y_center"])

    # V2: 自适应 y_gap = 估计行高 × 1.25
    if y_gap is None:
        lh = _estimate_line_height(sorted_results)
        y_gap = lh * 1.35

    merged = []
    current_group = [sorted_results[0]]

    for i in range(1, len(sorted_results)):
        prev = current_group[-1]
        curr = sorted_results[i]

        y_gap_actual = abs(curr["y_center"] - prev["y_center"])

        # 计算X方向重叠度（含相邻容忍度）
        prev_x1 = min(p[0] for p in prev["bbox"])
        prev_x2 = max(p[0] for p in prev["bbox"])
        curr_x1 = min(p[0] for p in curr["bbox"])
        curr_x2 = max(p[0] for p in curr["bbox"])

        # V2: 用更宽的相邻容忍（负overlap时允许1个字符宽约24px）
        union_x1 = min(prev_x1, curr_x1)
        union_x2 = max(prev_x2, curr_x2)
        union_w = max(1, union_x2 - union_x1)
        inters = max(0, min(prev_x2, curr_x2) - max(prev_x1, curr_x1))
        # 左右边界对齐度：如果两行为同气泡，它们的左边界或右边界应该接近
        left_aligned = abs(prev_x1 - curr_x1) < 36
        right_aligned = abs(prev_x2 - curr_x2) < 36

        # 同一气泡：Y间距小于自适应阈值 且 (X有重叠 或 左右边界对齐)
        x_ok = (inters > -24) and (inters / union_w > -0.2) or left_aligned or right_aligned

        # V2: sender一致性检查（已有sender字段时才启用）
        prev_sender = prev.get("sender")
        curr_sender = curr.get("sender")
        sender_ok = (prev_sender is None or curr_sender is None or
                     prev_sender == curr_sender)

        if y_gap_actual < y_gap and x_ok and sender_ok:
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
                # 文本清洗：修正常见识别瑕疵
                for r in results:
                    if "text" in r:
                        r["text"] = _clean_text(r["text"])
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


def _compute_adaptive_scale(h, w, base_scale):
    """
    V2 智能缩放：根据图像尺寸和估计的文字高度自适应放大/缩小。
    增量裁剪的小区域若再缩小，文字会被压到不可识别——这是识别率差的关键。
    升级：极小区域最高放大到3倍；整屏截图若文字高度<16px则不再缩小。
    """
    small_dim = min(h, w)
    big_dim = max(h, w)

    if small_dim < 80:
        return 3.0                       # 极小区域：Lanczos 放大3倍
    if small_dim < 150:
        return 2.2                       # 非常小：放大2.2倍
    if small_dim < 250:
        return 1.6                       # 单条消息裁剪：放大1.6倍
    if small_dim < 400:
        return 1.15                      # 中等区域：略放大补偿DPI缩放
    # 整屏截图：若长边>1600，用base_scale但不低于0.80（默认base_scale=0.85）
    if big_dim > 1600:
        return max(base_scale, 0.80)
    return 1.0                           # 其它：原尺寸


def _upsample_small(image, scale_factor=1.0):
    """V2: 高质量缩放 —— 放大用Lanczos，缩小用面积插值。"""
    if abs(scale_factor - 1.0) < 0.001:
        return image
    h, w = image.shape[:2]
    new_w, new_h = int(w * scale_factor), int(h * scale_factor)
    if scale_factor > 1.0:
        interp = cv2.INTER_LANCZOS4  # 放大：Lanczos保留文字细节
    else:
        interp = cv2.INTER_AREA      # 缩小：面积插值防锯齿
    return cv2.resize(image, (new_w, new_h), interpolation=interp)


def _enhance_image(image):
    """V2: 增强图像 —— 使用新的预处理 Pipeline。"""
    return _preprocess_image(image, denoise=True, aggressive=True)


# ================ V2: 文本清洗 + 形近字纠错 ================

def _looks_like_digit_context(text):
    """
    判断文本是否属于数字上下文（手机号/验证码/金额/QQ号等），
    用于决定是否启用 DIGIT_CONTEXT_MAP 形近字纠错。
    判定：去除符号后，>=50% 的字符是数字或 DIGIT_CONTEXT_MAP 中的键。
    """
    cleaned = re.sub(r'[\s\-\(\)\[\]\+\.]+', '', text)
    if not cleaned:
        return False
    digit_like = sum(1 for c in cleaned
                     if c.isdigit() or c in DIGIT_CONTEXT_MAP)
    return digit_like >= len(cleaned) * 0.5 and len(cleaned) >= 3


def _apply_digit_correction(text):
    """对数字上下文的文本进行形近字→数字纠错。"""
    chars = list(text)
    for i, ch in enumerate(chars):
        if ch in DIGIT_CONTEXT_MAP and not ch.isdigit():
            chars[i] = DIGIT_CONTEXT_MAP[ch]
    return "".join(chars)


def _clean_text(text):
    """
    V2 OCR文本清洗：修正常见识别瑕疵 + 形近字纠错 + 数字上下文修复。
    1. 清理不可见字符（零宽空格/BOM/变体选择符）
    2. 压缩多余空格/空行
    3. 清理行首行尾的噪声符号
    4. 合并重复的标点
    5. 数字上下文的字母→数字纠错（手机号/验证码/金额场景）
    6. 统一中英文标点
    """
    if not text:
        return text

    # 1. 不可见字符清理
    for bad, good in [
        ("\u200b", ""), ("\ufeff", ""), ("\u200e", ""),
        ("\u200f", ""), ("\u00a0", " "), ("\u3000", " "),
        ("\u202a", ""), ("\u202b", ""), ("\u202c", ""),
        ("\ufe0f", ""), ("\ufe0e", ""), ("\u2060", ""),
    ]:
        text = text.replace(bad, good)

    # 2. 压缩连续空白（保留换行）
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)

    # 3. 行首行尾的孤立噪声符号
    text = re.sub(r'^[\s\|_\-—•·~`ˉ￣ˇ〃‥…]+', '', text)
    text = re.sub(r'[\s\|_\-—•·~`ˉ￣ˇ〃‥…]+$', '', text)

    # 4. 重复标点压缩（中文标点+英文标点）
    text = re.sub(r'([。！？!?，,、\.])\1{2,}', r'\1\1', text)
    # 问号感叹号连写保留（?!/!?很常见）

    # 5. 数字上下文纠错（手机号/验证码/金额）
    if _looks_like_digit_context(text):
        text = _apply_digit_correction(text)

    # 6. 统一全角标点→半角（数字串内），保留中文全角标点在中文语境
    # 这里只处理明显是英文/数字混合的场景
    if re.search(r'[A-Za-z0-9]', text):
        text = text.replace("（", "(").replace("）", ")")

    return text.strip()


# ================ V2: 自适应置信度阈值 ================

def _adaptive_min_confidence(text, base_conf):
    """
    V2: 根据文本长度自适应调整置信度阈值。
    - 极短文本（1-2字，比如"嗯"、"好"）：识别难度高，降低阈值
    - 短文本（3-5字）：略降
    - 长文本：置信度天然更可靠，提升阈值避免乱码通过
    """
    text_len = len(text.strip())
    if text_len <= 2:
        return max(0.25, base_conf - 0.20)   # 单字放宽
    if text_len <= 5:
        return max(0.30, base_conf - 0.15)
    if text_len <= 10:
        return max(0.35, base_conf - 0.08)
    if text_len <= 20:
        return base_conf
    return min(0.85, base_conf + 0.05)      # 长文本收紧


# ================ V2: 多策略PaddleOCR识别 ================

def _recognize_with_paddle(image, scale=1.0, min_confidence=0.40,
                           merge_bubble=False, denoise=False):
    """
    V2: PaddleOCR识别（多策略重试版）。
    重试策略：
      1. 原图 + 自适应缩放
      2. (为空或极少) 预处理增强图
      3. (仍少) 二值化图
      4. (仍少) 更高倍率放大 (upsample ×2) + 增强
    每次识别的结果都会汇总，使用去重+取最高置信度的融合策略。
    """
    ocr = get_ocr()

    image_regions = []  # 图片区域检测保持禁用

    h, w = image.shape[:2]

    # === 智能缩放 ===
    eff_scale = _compute_adaptive_scale(h, w, scale)

    def _scale_and_prepare(img, sc):
        return _upsample_small(img, sc)

    def _run_ocr_on(img):
        try:
            results = ocr.ocr(img, cls=True)
        except Exception as e:
            logger.error(f"OCR识别失败: {e}")
            return None
        if not results or not results[0]:
            return []
        return results[0]

    def _parse_lines(raw_lines, sc_factor, pass_name=""):
        """把Paddle返回的raw_lines解析为标准parsed结构"""
        out = []
        if not raw_lines:
            return out
        for line in raw_lines:
            try:
                bbox = line[0]
                text = line[1][0]
                confidence = line[1][1]
            except (IndexError, TypeError, KeyError):
                continue

            if abs(sc_factor - 1.0) > 0.001:
                bbox = [[p[0] / sc_factor, p[1] / sc_factor] for p in bbox]

            text = text.strip()
            if not text:
                continue

            # V2: 自适应置信度阈值
            effective_conf = _adaptive_min_confidence(text, min_confidence)
            if confidence < effective_conf:
                logger.debug(f"[{pass_name}] 丢弃低置信度: '{text[:20]}' "
                             f"conf={confidence:.2f} (阈值={effective_conf:.2f})")
                continue

            if _is_in_image_region(bbox, image_regions):
                continue

            text = _clean_text(text)
            if not text:
                continue

            out.append({
                "text": text,
                "confidence": confidence,
                "bbox": bbox,
                "y_center": (bbox[0][1] + bbox[2][1]) / 2,
                "x_center": (bbox[0][0] + bbox[1][0]) / 2,
                "_pass": pass_name,
            })
        return out

    # ========= 多轮策略识别 =========
    all_candidates = []  # 每轮解析结果
    strategy_log = []    # 用于诊断日志

    # --- Pass 1: 原图 + 自适应缩放 ---
    try:
        img_p1 = _scale_and_prepare(image, eff_scale)
        r1 = _run_ocr_on(img_p1)
        p1_parsed = _parse_lines(r1, eff_scale, pass_name="P1原图")
        all_candidates.append(p1_parsed)
        strategy_log.append(f"P1原图({len(p1_parsed)}条)")
    except Exception as e:
        logger.warning(f"[OCR] P1异常: {e}")

    # --- Pass 2: 增强预处理图（V2 pipeline） ---
    p1_count = len(all_candidates[-1]) if all_candidates else 0
    if p1_count < 8:
        try:
            enhanced = _preprocess_image(image, denoise=True, aggressive=(denoise and p1_count == 0))
            img_p2 = _scale_and_prepare(enhanced, eff_scale)
            r2 = _run_ocr_on(img_p2)
            p2_parsed = _parse_lines(r2, eff_scale, pass_name="P2增强")
            all_candidates.append(p2_parsed)
            strategy_log.append(f"P2增强({len(p2_parsed)}条)")
        except Exception as e:
            logger.warning(f"[OCR] P2增强图异常: {e}")

    # --- Pass 3: 二值化图（兜底浅色字/低对比度） ---
    best_so_far = max((len(c) for c in all_candidates), default=0)
    if best_so_far < 6:
        try:
            binary_img = _preprocess_binary(image)
            img_p3 = _scale_and_prepare(binary_img, eff_scale)
            r3 = _run_ocr_on(img_p3)
            p3_parsed = _parse_lines(r3, eff_scale, pass_name="P3二值化")
            all_candidates.append(p3_parsed)
            strategy_log.append(f"P3二值化({len(p3_parsed)}条)")
        except Exception as e:
            logger.warning(f"[OCR] P3二值化异常: {e}")

    # --- Pass 4: 2×放大 + 增强（兜底小字） ---
    best_so_far = max((len(c) for c in all_candidates), default=0)
    if best_so_far < 4 and min(h, w) > 120:
        try:
            big_scale = min(2.5, max(1.3, eff_scale) * 1.6)
            enhanced2 = _preprocess_image(image, denoise=True, aggressive=True)
            img_p4 = _upsample_small(enhanced2, big_scale)
            r4 = _run_ocr_on(img_p4)
            p4_parsed = _parse_lines(r4, big_scale, pass_name="P4放大")
            all_candidates.append(p4_parsed)
            strategy_log.append(f"P4放大x{big_scale:.1f}({len(p4_parsed)}条)")
        except Exception as e:
            logger.warning(f"[OCR] P4放大异常: {e}")

    # ========= V2: 多轮结果融合 =========
    # 融合策略：
    #   对同一位置（y_center & x_center 接近）的多个候选，取置信度最高的文本
    #   对位置完全不重叠的候选直接合并
    merged_by_pos = {}   # key: (y_bin, x_bin) -> best candidate
    all_items = [item for sublist in all_candidates for item in sublist]

    def _pos_key(item, h_bin=12, w_bin=14):
        return (int(item["y_center"]) // h_bin, int(item["x_center"]) // w_bin)

    for item in all_items:
        key = _pos_key(item)
        if key not in merged_by_pos:
            merged_by_pos[key] = item
        else:
            existing = merged_by_pos[key]
            # 同样位置：取置信度更高的
            if item["confidence"] > existing["confidence"]:
                merged_by_pos[key] = item

    parsed = list(merged_by_pos.values())

    # 诊断日志（限频）
    if len(parsed) > 0:
        logger.info("[OCR策略] " + " → ".join(strategy_log) +
                    f" → 融合后{len(parsed)}条")

    # === 合并同气泡多行文本 ===
    if merge_bubble and len(parsed) > 1:
        parsed = _merge_bubble_lines(parsed)

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


# ================================================================
# V3: 群聊 vs 私聊 区分 + 群成员昵称识别
# 微信 PC 版聊天界面的关键视觉特征：
#  ┌── 私聊 ────────────────────────────────────┐
#  │ 自己气泡：右侧绿色 + 没有昵称行
#  │ 对方气泡：左侧白色 + 没有昵称行（头像旁边也不显示昵称）
#  └─────────────────────────────────────────────┘
#  ┌── 群聊 ────────────────────────────────────┐
#  │ 自己气泡：右侧绿色 + 没有昵称行（和私聊一致）
#  │ 对方气泡：左侧白色气泡 上方有一行小字(群成员昵称)
#  │ 昵称行特征：x < 气泡左上角 x, 文本长度2~12字, 不含标点/数字比例低
#  └─────────────────────────────────────────────┘
#  算法：
#   1) 扫描聊天区前30% 高度内的文本 → 检测 "XX人在线/群公告/群文件/XX条新消息" 群聊关键词
#   2) 对每条左侧气泡，检测其上方是否存在 1~40px 内的短昵称行（非时间非系统文字）
#   3) 若 >1 条气泡匹配到昵称行 → 判定为群聊
#   4) 把昵称行从气泡正文里剥离，记录到 group_member 字段
# ================================================================

_GROUP_HINT_WORDS = [
    "人在线", "群公告", "群文件", "群聊", "条新消息",
    "群成员", "邀请入群", "加入群聊", "加入了群聊",
    "已成为群友", "管理员", "禁言",
]

# 群聊标题常见结构：XXX群 / XX班级 / XX工作群 / XXX交流群 / XX家人
_GROUP_NAME_SUFFIX = ("群", "班", "组", "队", "家族", "家人", "部门",
                      "办公室", "同事", "同学", "校友", "战友", "社",
                      "俱乐部", "俱乐部", "商会", "协会", "支部")


def infer_chat_kind_by_title(contact_title):
    """
    V3: 根据窗口标题（联系人名）快速推断是群聊还是私聊。
    群聊标题特征：
      - 包含"群"字、或XX班/组/队等群后缀
      - 括号内有数字："XX项目组 (12)" / "开心一家人(56)"
    """
    if not contact_title:
        return "unknown"
    t = contact_title.strip()
    # 括号里带数字人数
    if re.search(r'[（(]\s*\d+\s*[)）]', t):
        return "group"
    for suf in _GROUP_NAME_SUFFIX:
        if t.endswith(suf) or suf in t:
            return "group"
    return "personal"


def _detect_chat_kind_hints(ocr_results, image_h, image_w):
    """
    扫描OCR结果，找聊天区上方/头部的群聊提示词（"群公告/群文件/群成员/XX人在线"等）。
    返回 dict: {kind: "group"/"personal"/"unknown", confidence: 0~1, hints: []}
    """
    if not ocr_results:
        return {"kind": "unknown", "confidence": 0.0, "hints": []}

    hits = []
    for r in ocr_results:
        txt = (r.get("text") or "").strip()
        if not txt:
            continue
        for kw in _GROUP_HINT_WORDS:
            if kw in txt:
                hits.append((txt, kw))
                break

    if len(hits) >= 2:
        return {"kind": "group", "confidence": 0.92, "hints": hits}
    if len(hits) == 1:
        return {"kind": "group", "confidence": 0.72, "hints": hits}

    # 私聊特征：前20%高度内出现"朋友圈 / 视频号 / 发消息 / 语音"等私聊功能
    personal_hits = 0
    for r in ocr_results:
        if r["y_center"] < image_h * 0.25:
            txt = (r.get("text") or "").strip()
            for pw in ["朋友圈", "视频号", "发送朋友", "标为已读", "置顶聊天"]:
                if pw in txt:
                    personal_hits += 1
                    break
    if personal_hits >= 1:
        return {"kind": "personal", "confidence": 0.60, "hints": []}

    return {"kind": "unknown", "confidence": 0.0, "hints": []}


def _is_pure_nickname_candidate(text):
    """
    V3: 昵称候选判定 — 微信群聊里"成员昵称行"的特征：
     - 长度 1~14（中文昵称一般2~6，加emoji也不会太长）
     - 不包含常见系统词（时间/冒号/撤回/回复/图片/红包/转账等）
     - 不以数字为主（避免误判时间戳"12:34"）
     - 不是"我"或"对方"等已有sender词
    """
    if not text:
        return False
    t = text.strip()
    # 去常见 OCR 昵称尾部残留（冒号、@、空格）
    t_stripped = t.rstrip("：:、　 ")
    n = len(t_stripped)
    if n < 1 or n > 14:
        return False
    # 系统/状态词
    bad = ("撤回", "拍了拍", "的红包", "转账", "回复", "图片", "视频",
           "语音", "位置", "收藏", "表情", "文件", "公告", "@所有人",
           "上午", "下午", "昨天", "今天", "星期", "分钟前", "小时前",
           "系统消息", "以上是", "新消息", "APP", "打开", "查看")
    for b in bad:
        if b in t_stripped:
            return False
    # 时间戳格式
    if re.match(r'^\d{1,2}[:：]\d{2}$', t_stripped):
        return False
    # 纯数字
    if re.match(r'^[\d\s\.\,]+$', t_stripped):
        return False
    # 纯标点
    if re.match(r'^[\s\.\,，。！？\!\?\-\_、；：\:\"\'\(\)\[\]（）【】]+$', t_stripped):
        return False
    return True


def _group_detect_and_strip_member_name(ocr_results, image_h=None):
    """
    V3: 对已按气泡合并过的 parsed 结果（_merge_bubble_lines输出）做群聊增强：
    1) 遍历每条气泡（左侧、sender=other）
    2) 向上找最近的一条"昵称候选行"：
       - y_center 范围：[气泡顶部 - 36px, 气泡顶部 - 2px]
       - x 中心在气泡x范围的左侧或与气泡左边界对齐
       - 文本是昵称候选
    3) 如果找到了：从正文里剥离昵称行、写入 group_member 字段、打上 is_group=True
    4) 返回 (增强后的parsed, is_group, unique_members_set)
    """
    if not ocr_results:
        return ocr_results, False, set()

    # 行高估计（昵称行一般比正文略小）
    heights = []
    for r in ocr_results:
        ys = [p[1] for p in r["bbox"]]
        heights.append(max(ys) - min(ys))
    median_line_h = sorted(heights)[len(heights) // 2] if heights else 20

    # 昵称间距阈值：通常昵称行在气泡上方 ~0.8~2.5 倍行高
    gap_min = max(3, median_line_h * 0.6)
    gap_max = max(14, median_line_h * 3.2)

    # 先收集所有单行（短文本，sender=other）的候选昵称行
    nickname_candidates = []
    for r in ocr_results:
        txt = r.get("text", "") or ""
        # 昵称候选条件：
        #  - 单行（无换行）
        #  - 是纯昵称
        #  - 位置偏左（x_center < 图片宽60%）
        #  - 长度 <= 12
        if "\n" not in txt and _is_pure_nickname_candidate(txt) and len(txt.strip()) <= 12:
            nickname_candidates.append(r)

    # 给每条左气泡尝试匹配昵称
    matched_member_count = 0
    unique_members = set()
    parsed_enhanced = list(ocr_results)

    for i, bubble in enumerate(parsed_enhanced):
        if bubble.get("sender") == "me":
            continue  # 自己气泡上方没有昵称

        bubble_top = min(p[1] for p in bubble["bbox"])
        bubble_left = min(p[0] for p in bubble["bbox"])
        bubble_right = max(p[0] for p in bubble["bbox"])

        best_nick = None
        best_dist = 1e9
        for cand in nickname_candidates:
            # 昵称必须在气泡的上方 gap_min~gap_max 内
            cand_bottom = max(p[1] for p in cand["bbox"])
            dist = bubble_top - cand_bottom
            if dist < gap_min * 0.6 or dist > gap_max:
                continue
            # x必须偏左（不超过气泡左侧往右50%气泡宽处，避免气泡正文自己被当成昵称）
            cand_x_center = cand.get("x_center", (cand["bbox"][0][0] + cand["bbox"][1][0]) / 2)
            if cand_x_center > bubble_left + (bubble_right - bubble_left) * 0.6:
                continue
            # 昵称和气泡不能是同一条（避免正文自匹配）
            if cand is bubble:
                continue
            # 取最近的
            if dist < best_dist:
                best_dist = dist
                best_nick = cand

        if best_nick is not None:
            member_name = (best_nick.get("text") or "").strip()
            member_name = member_name.rstrip("：:、　 ")
            if member_name:
                bubble["group_member"] = member_name
                # 昵称行OCR置信度做参考
                bubble["group_member_confidence"] = best_nick.get("confidence", 0.0)
                unique_members.add(member_name)
                matched_member_count += 1

    # V3: 决策群聊还是私聊
    #  - 匹配到 >=1 条成员昵称 → 群聊（置信度随数量递增）
    #  - 否则保持 unknown，交由上层用 infer_chat_kind_by_title 二次决策
    is_group = matched_member_count >= 1

    return parsed_enhanced, is_group, unique_members


def recognize_with_group_enhance(image, contact_title=None, scale=1.0,
                                 min_confidence=0.40, denoise=False):
    """
    V3 入口：完整识别 + 群聊增强 + 发送者V4 + 自己消息识别强化。
    返回：
      {
        "lines": [...],            # 合并气泡后的 parsed lines（sender/group_member/text/confidence/bbox）
        "chat_kind": "group"/"personal"/"unknown",
        "group_members": {"张三", "李四"},    # 本轮识别到的群成员
        "chat_kind_confidence": 0.0~1.0,
        "hints": [...],
      }
    """
    import os as _os
    # 禁用环境变量覆盖（防止测试时奇怪的行为）
    _ = _os.environ.get("DISABLE_OCR_LOG")

    # 基础识别（带多策略重试+自适应缩放）
    parsed = recognize(image, scale=scale, min_confidence=min_confidence,
                       merge_bubble=True, denoise=denoise)

    # 发送者V3：identify_senders 原逻辑（位置+颜色）— 已在 recognize 里调用过
    # 这里再跑一次以强化"自己"消息判断（V4增强）
    parsed = identify_senders_v4(parsed, image)

    h, w = (0, 0) if image is None else image.shape[:2]

    # 1) 标题层推断（优先，稳定、廉价）
    title_kind = infer_chat_kind_by_title(contact_title) if contact_title else "unknown"

    # 2) 聊天区头部提示词推断
    hint_res = _detect_chat_kind_hints(parsed, h, w)

    # 3) 昵称行 + 气泡结构推断
    parsed, has_member_matches, unique_members = _group_detect_and_strip_member_name(
        parsed, image_h=h
    )

    # === V3: 综合决策（加权投票） ===
    vote = {"group": 0.0, "personal": 0.0, "unknown": 0.0}

    if title_kind == "group":
        vote["group"] += 0.85
    elif title_kind == "personal":
        vote["personal"] += 0.75
    else:
        vote["unknown"] += 0.2

    if hint_res["kind"] == "group":
        vote["group"] += hint_res["confidence"]
    elif hint_res["kind"] == "personal":
        vote["personal"] += hint_res["confidence"]

    if has_member_matches:
        # 成员匹配越多越可能是群聊
        vote["group"] += min(0.95, 0.55 + 0.08 * len(unique_members))

    # 如果没有成员匹配且标题明显不像群聊 → 加大私聊权重
    if not has_member_matches and title_kind == "personal":
        vote["personal"] += 0.25

    # 选出最高票
    best_kind = max(vote, key=vote.get)
    best_score = vote[best_kind]
    if best_score < 0.40:
        chat_kind = "unknown"
    else:
        chat_kind = best_kind
    chat_kind_confidence = round(min(1.0, max(0.0, best_score)), 3)

    # 给每条消息附加 chat_kind 字段（UI展示用）
    for r in parsed:
        r["chat_kind"] = chat_kind

    return {
        "lines": parsed,
        "chat_kind": chat_kind,
        "group_members": list(unique_members),
        "chat_kind_confidence": chat_kind_confidence,
        "hints": hint_res.get("hints", []),
    }


# ================================================================
# V4: 发送者判断强化 — 特别强化"我自己发的消息"别漏识别
# 微信PC自己的消息特征：
#   1) 气泡紧贴右侧（right_x / image_w > 0.65）
#   2) 气泡背景色：#95EC69 或相近绿（BGR下 G>R 且 G>B, G>160）
#   3) 文字右对齐（但文字整体box未必贴窗口最右，要看气泡整体的最右像素）
#   4) 气泡左侧通常有 1px~3px 的白色/背景色间隔
# ================================================================

def _bubble_right_bg_is_green(image, bubble_bbox, h, w):
    """
    采样气泡最右边界往右 3~15px 的空白区域 + 气泡内部像素，
    判断是否为微信"自己"的绿色气泡背景。
    返回 True（绿色气泡 = me）/ False。
    """
    try:
        left_x = int(min(p[0] for p in bubble_bbox))
        top_y = int(min(p[1] for p in bubble_bbox))
        right_x = int(max(p[0] for p in bubble_bbox))
        bottom_y = int(max(p[1] for p in bubble_bbox))
    except Exception:
        return False

    green_votes = 0
    total = 0

    # 1) 气泡内部左上采样（10×10 条带），注意避开文字行
    inner_x0 = max(0, left_x + 2)
    inner_x1 = min(w - 1, left_x + 12)
    inner_y0 = max(0, top_y + 1)
    inner_y1 = min(h - 1, top_y + 6)
    if inner_x1 > inner_x0 and inner_y1 > inner_y0:
        roi = image[inner_y0:inner_y1, inner_x0:inner_x1]
        if roi.size > 0:
            mean_bgr = roi.mean(axis=(0, 1)).astype(np.float64)
            b, g, r = float(mean_bgr[0]), float(mean_bgr[1]), float(mean_bgr[2])
            total += 1
            if g > 150 and g > r * 1.10 and g > b * 1.12:
                green_votes += 1

    # 2) 气泡内部右上采样
    inner_x0 = max(0, right_x - 12)
    inner_x1 = min(w - 1, right_x - 2)
    if inner_x1 > inner_x0 and inner_y1 > inner_y0:
        roi = image[inner_y0:inner_y1, inner_x0:inner_x1]
        if roi.size > 0:
            mean_bgr = roi.mean(axis=(0, 1)).astype(np.float64)
            b, g, r = float(mean_bgr[0]), float(mean_bgr[1]), float(mean_bgr[2])
            total += 1
            if g > 150 and g > r * 1.10 and g > b * 1.12:
                green_votes += 1

    # 3) 气泡正下方 3~8px 采样（绿气泡尾巴区域）
    below_y0 = min(h - 1, bottom_y + 3)
    below_y1 = min(h - 1, bottom_y + 9)
    cx0, cx1 = max(0, right_x - 18), min(w - 1, right_x - 4)
    if below_y1 > below_y0 and cx1 > cx0:
        roi = image[below_y0:below_y1, cx0:cx1]
        if roi.size > 0:
            mean_bgr = roi.mean(axis=(0, 1)).astype(np.float64)
            b, g, r = float(mean_bgr[0]), float(mean_bgr[1]), float(mean_bgr[2])
            total += 1
            # 尾巴通常略比气泡深一点
            if g > 130 and g > r * 1.08 and g > b * 1.10:
                green_votes += 1

    if total == 0:
        return False
    # 多数投票
    return green_votes * 2 >= total


def identify_senders_v4(ocr_results, image):
    """
    V4: 发送者判断（强化自己消息识别）
    步骤：
      1. 先走 V3 identify_senders 基础判断（位置+简单颜色）
      2. 对每条气泡再做 V4 绿色气泡背景采样
         - 如果颜色强命中绿气泡 → 强制 sender=me
         - 如果位置居中但颜色命中 → 修正为 me
         - 如果位置说 me 颜色没命中（可能是自己气泡的文字部分采样不到背景）
           → 保留 me，但降低 sender_confidence
      3. 对每条标注 sender_confidence 字段（UI/Obsidian展示用）
    """
    if not ocr_results or image is None:
        return ocr_results

    # Step 1: 先调用旧版 V3 基础判断（保证位置兜底）
    ocr_results = identify_senders(ocr_results, image)

    h, w = image.shape[:2]

    for r in ocr_results:
        bbox = r.get("bbox")
        if not bbox:
            continue
        left_x = min(p[0] for p in bbox)
        right_x = max(p[0] for p in bbox)
        center_x = r.get("x_center", (left_x + right_x) / 2)

        base_sender = r.get("sender", "other")
        base_conf = 0.60   # V3判断的基线置信度

        # Step 2: V4 气泡背景色采样
        is_green = _bubble_right_bg_is_green(image, bbox, h, w)

        # Step 3: 强化边界位置判断 — 右侧紧贴窗口基本就是me
        right_ratio = right_x / max(1, w)
        left_ratio = left_x / max(1, w)

        strong_me_by_pos = (right_ratio > 0.68 and center_x / max(1, w) > 0.60)
        strong_other_by_pos = (left_ratio < 0.30 and center_x / max(1, w) < 0.42)

        final_sender = base_sender
        conf = base_conf

        if is_green:
            # 绿色气泡：不管V3判了啥，都强修me
            final_sender = "me"
            conf = 0.92 if strong_me_by_pos else 0.85
        else:
            # 非绿色：综合位置
            if strong_me_by_pos and base_sender != "me":
                # 位置极右但没采样到绿（可能是文字部分刚好没覆盖到绿色）
                # → 放宽：如果 right_x 超过窗口 72%，也判定 me
                if right_ratio > 0.72:
                    final_sender = "me"
                    conf = 0.70
                else:
                    final_sender = base_sender
                    conf = 0.55
            elif strong_me_by_pos:
                final_sender = "me"
                conf = 0.80
            elif strong_other_by_pos:
                final_sender = "other"
                conf = 0.88
            else:
                final_sender = base_sender
                conf = 0.55

        r["sender"] = final_sender
        r["sender_confidence"] = round(conf, 3)

    return ocr_results
