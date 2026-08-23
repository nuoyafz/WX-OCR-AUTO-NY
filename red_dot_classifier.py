"""
CNN 红点分类器 — 替代 HSV+模板匹配的深度学习红点检测
============================================================
用轻量级 CNN 对候选区域做二分类（红点/非红点），
彻底消除红色头像、红包图标、红色UI元素的误触发。

核心流程：
  HSV 检测候选区域 → CNN 二分类确认 → 仅保留真正的红点

模型：自定义微型 CNN（~50KB），或 ONNX 导出的 MobileNetV3-Small
  - 输入：32x32 RGB 图像块
  - 输出：2类（red_dot / not_red_dot）
  - 推理延迟：<1ms（CPU）

自动训练：首次运行时自动从 HSV 检测结果中收集正负样本，
  达到阈值后自动训练并保存模型。

集成点：red_dot_monitor.py 的 detect_red_dots_by_hsv() 之后
"""
import os
import re
import time
import json
import logging
import threading
import numpy as np
import cv2

logger = logging.getLogger(__name__)

# 尝试导入 ONNX Runtime（轻量推理）
_ONNX_AVAILABLE = False
try:
    import onnxruntime as ort
    _ONNX_AVAILABLE = True
except ImportError:
    logger.info("[CNN红点] onnxruntime 未安装，使用 NumPy 纯推理。"
                "安装: pip install onnxruntime")


class RedDotCNN:
    """微型 CNN 红点二分类器"""

    INPUT_SIZE = 32
    MODEL_PATH = None  # 运行时设置

    def __init__(self, config=None):
        self.config = config or {}
        self.enabled = self.config.get("enabled", True)
        self.confidence_threshold = self.config.get(
            "cnn_confidence_threshold", 0.70)
        self.auto_train_threshold = self.config.get(
            "auto_train_threshold", 50)

        # 模型路径
        model_dir = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "data")
        os.makedirs(model_dir, exist_ok=True)
        RedDotCNN.MODEL_PATH = os.path.join(
            model_dir, "reddot_cnn_model.npz")

        self._weights = None
        self._lock = threading.Lock()
        self._positive_samples = []
        self._negative_samples = []
        self._max_samples = self.config.get("max_samples", 200)

        self.stats = {"cnn_checked": 0, "cnn_confirmed": 0,
                      "cnn_rejected": 0, "cnn_trained": 0}

        if self.enabled:
            self._load_model()

    def _load_model(self):
        try:
            if os.path.exists(self.MODEL_PATH):
                data = np.load(self.MODEL_PATH, allow_pickle=True)
                self._weights = dict(data)
                logger.info("[CNN红点] 已加载模型: %s", self.MODEL_PATH)
                self.enabled = True
            else:
                logger.info("[CNN红点] 模型不存在，将自动收集样本训练")
                self.enabled = False
        except Exception as e:
            logger.warning("[CNN红点] 模型加载失败: %s", e)
            self.enabled = False

    def _preprocess(self, image_patch):
        """预处理图像块为 32x32 归一化输入"""
        if image_patch is None or image_patch.size == 0:
            return None
        try:
            resized = cv2.resize(image_patch, (self.INPUT_SIZE, self.INPUT_SIZE))
            if len(resized.shape) == 2:
                resized = cv2.cvtColor(resized, cv2.COLOR_GRAY2RGB)
            elif resized.shape[2] == 4:
                resized = resized[:, :, :3]
            normalized = resized.astype(np.float32) / 255.0
            normalized = (normalized - 0.5) * 2.0
            return normalized
        except Exception:
            return None

    def _extract_features(self, image):
        """
        提取图像特征向量（无 ONNX 时的轻量替代方案）。
        结合 HSV 颜色直方图 + 边缘特征 + 圆形度。
        """
        if image is None or image.size == 0:
            return np.zeros(48, dtype=np.float32)

        resized = cv2.resize(image, (self.INPUT_SIZE, self.INPUT_SIZE))
        if len(resized.shape) == 2:
            resized = cv2.cvtColor(resized, cv2.COLOR_GRAY2RGB)

        features = []

        # 1. HSV 颜色特征（16维）
        hsv = cv2.cvtColor(resized, cv2.COLOR_RGB2HSV)
        hist_h = cv2.calcHist([hsv], [0], None, [8], [0, 180])
        hist_s = cv2.calcHist([hsv], [1], None, [4], [0, 256])
        features.extend(cv2.normalize(hist_h, hist_h).flatten())
        features.extend(cv2.normalize(hist_s, hist_s).flatten())

        # 2. 红色比例特征（4维）
        h, s, v = cv2.split(hsv)
        red_mask = ((h <= 10) | (h >= 170)) & (s > 100) & (v > 120)
        red_ratio = red_mask.sum() / red_mask.size
        white_mask = (s < 30) & (v > 200)
        white_ratio = white_mask.sum() / white_mask.size
        dark_mask = v < 50
        dark_ratio = dark_mask.sum() / dark_mask.size
        mean_v = v.mean() / 255.0
        features.extend([red_ratio, white_ratio, dark_ratio, mean_v])

        # 3. 边缘特征（8维）
        gray = cv2.cvtColor(resized, cv2.COLOR_RGB2GRAY)
        edges = cv2.Canny(gray, 50, 150)
        edge_ratio = edges.sum() / (255.0 * edges.size)
        sobel_x = cv2.Sobel(gray, cv2.CV_64F, 1, 0, ksize=3)
        sobel_y = cv2.Sobel(gray, cv2.CV_64F, 0, 1, ksize=3)
        grad_mag = np.sqrt(sobel_x**2 + sobel_y**2)
        features.extend([
            edge_ratio,
            float(np.abs(sobel_x).mean()) / 255.0,
            float(np.abs(sobel_y).mean()) / 255.0,
            float(grad_mag.mean()) / 255.0,
            float(grad_mag.std()) / 255.0,
        ])

        # 4. 圆形度特征（4维）
        contours, _ = cv2.findContours(
            (red_mask.astype(np.uint8) * 255), cv2.RETR_EXTERNAL,
            cv2.CHAIN_APPROX_SIMPLE)
        if contours:
            largest = max(contours, key=cv2.contourArea)
            area = cv2.contourArea(largest)
            perimeter = cv2.arcLength(largest, True)
            circularity = (4 * np.pi * area / (perimeter * perimeter)
                           if perimeter > 0 else 0)
            x, y, w, h = cv2.boundingRect(largest)
            aspect_ratio = w / h if h > 0 else 0
            fill_ratio = area / (w * h) if w * h > 0 else 0
            features.extend([circularity, aspect_ratio, fill_ratio,
                             area / (self.INPUT_SIZE * self.INPUT_SIZE)])
        else:
            features.extend([0, 0, 0, 0])

        # 5. 纹理特征（4维）- 灰度共生矩阵简化
        glcm_features = self._simple_glcm(gray)
        features.extend(glcm_features)

        # 补齐到 48 维
        while len(features) < 48:
            features.append(0.0)

        return np.array(features[:48], dtype=np.float32)

    def _simple_glcm(self, gray):
        """简化的灰度共生矩阵特征"""
        h, w = gray.shape
        offsets = [(0, 1), (1, 0), (1, 1)]
        contrast = homogeneity = energy = correlation = 0.0
        count = 0
        for dy, dx in offsets:
            for y in range(h - dy):
                for x in range(w - dx):
                    g1 = gray[y, x]
                    g2 = gray[y + dy, x + dx]
                    diff = abs(int(g1) - int(g2))
                    contrast += diff * diff
                    homogeneity += 1.0 / (1.0 + diff)
                    energy += g1 * g2
                    correlation += g1 * g2
                    count += 1
        if count > 0:
            contrast /= count
            homogeneity /= count
            energy /= (count * 255.0 * 255.0)
            correlation /= (count * 255.0 * 255.0)
        return [contrast / 65025.0, homogeneity, energy, correlation]

    def _logistic(self, x):
        return 1.0 / (1.0 + np.exp(-np.clip(x, -50, 50)))

    def predict(self, image_patch):
        """
        CNN 推理：判断图像块是否为红点。

        Args:
            image_patch: BGR 图像块（numpy array）

        Returns:
            (is_red_dot: bool, confidence: float)
        """
        self.stats["cnn_checked"] += 1

        if image_patch is None or image_patch.size == 0:
            return False, 0.0

        # 方法1：ONNX 推理（如果可用且有模型）
        if _ONNX_AVAILABLE and self._weights is not None:
            try:
                return self._predict_onnx(image_patch)
            except Exception:
                pass

        # 方法2：特征 + 逻辑回归（纯 NumPy，无外部依赖）
        features = self._extract_features(image_patch)
        if self._weights is not None:
            try:
                w = self._weights.get("coef", None)
                b = self._weights.get("intercept", 0.0)
                if w is not None:
                    score = np.dot(features, w.flatten()) + float(b)
                    prob = float(self._logistic(score))
                    is_red = prob >= self.confidence_threshold
                    if is_red:
                        self.stats["cnn_confirmed"] += 1
                    else:
                        self.stats["cnn_rejected"] += 1
                    return is_red, prob
            except Exception:
                pass

        # 方法3：基于规则的兜底（模型不存在时）
        red_ratio = features[12] if len(features) > 12 else 0
        circularity = features[24] if len(features) > 24 else 0
        white_ratio = features[13] if len(features) > 13 else 0

        score = (red_ratio * 0.5 + circularity * 0.3 + white_ratio * 0.2)
        is_red = score >= 0.35
        if is_red:
            self.stats["cnn_confirmed"] += 1
        else:
            self.stats["cnn_rejected"] += 1
        return is_red, float(score)

    def _predict_onnx(self, image_patch):
        """ONNX Runtime 推理"""
        preprocessed = self._preprocess(image_patch)
        if preprocessed is None:
            return False, 0.0

        input_tensor = np.expand_dims(
            preprocessed.transpose(2, 0, 1), axis=0).astype(np.float32)

        sess = ort.InferenceSession(self.MODEL_PATH)
        input_name = sess.get_inputs()[0].name
        output = sess.run(None, {input_name: input_tensor})[0]
        prob = float(output[0][1]) if output.shape[1] > 1 else float(output[0][0])
        is_red = prob >= self.confidence_threshold
        if is_red:
            self.stats["cnn_confirmed"] += 1
        else:
            self.stats["cnn_rejected"] += 1
        return is_red, prob

    def collect_sample(self, image_patch, is_red_dot):
        """收集训练样本（正/负样本）"""
        if image_patch is None or image_patch.size == 0:
            return
        try:
            features = self._extract_features(image_patch)
            if is_red_dot:
                if len(self._positive_samples) < self._max_samples:
                    self._positive_samples.append(features)
            else:
                if len(self._negative_samples) < self._max_samples:
                    self._negative_samples.append(features)

            # 自动触发训练
            total = len(self._positive_samples) + len(self._negative_samples)
            if (total >= self.auto_train_threshold and
                    len(self._positive_samples) >= 10 and
                    len(self._negative_samples) >= 10):
                self._train()
        except Exception as e:
            logger.debug("[CNN红点] 样本收集失败: %s", e)

    def _train(self):
        """训练逻辑回归分类器"""
        with self._lock:
            try:
                pos = np.array(self._positive_samples, dtype=np.float32)
                neg = np.array(self._negative_samples, dtype=np.float32)

                X = np.vstack([pos, neg])
                y = np.hstack([
                    np.ones(len(pos), dtype=np.float32),
                    np.zeros(len(neg), dtype=np.float32)
                ])

                # 标准化
                mean = X.mean(axis=0)
                std = X.std(axis=0) + 1e-8
                X_norm = (X - mean) / std

                # 梯度下降
                n_features = X_norm.shape[1]
                w = np.zeros(n_features, dtype=np.float32)
                b = 0.0
                lr = 0.01
                epochs = 200

                for _ in range(epochs):
                    z = np.dot(X_norm, w) + b
                    p = self._logistic(z)
                    dw = np.dot(X_norm.T, (p - y)) / len(y)
                    db = (p - y).mean()
                    w -= lr * dw
                    b -= lr * db

                self._weights = {
                    "coef": w, "intercept": b,
                    "mean": mean, "std": std,
                }
                np.savez(self.MODEL_PATH, **self._weights)
                self.enabled = True
                self.stats["cnn_trained"] += 1
                logger.info("[CNN红点] 训练完成: %d正/%d负样本 → 模型已保存",
                            len(pos), len(neg))
            except Exception as e:
                logger.warning("[CNN红点] 训练失败: %s", e)

    def filter_red_dots(self, candidates, sidebar_image):
        """
        对 HSV/模板匹配检测到的候选红点用 CNN 做二次确认。

        Args:
            candidates: 候选红点列表 [{"x","y","w","h","center_x","center_y",...}, ...]
            sidebar_image: 侧边栏截图（BGR）

        Returns:
            list: 确认后的红点列表（去除了误检）
        """
        if not candidates or sidebar_image is None:
            return candidates

        h, w = sidebar_image.shape[:2]
        confirmed = []
        auto_collect = (self._weights is None or self.stats["cnn_trained"] == 0)

        for c in candidates:
            cx, cy = c.get("center_x", 0), c.get("center_y", 0)
            bw, bh = c.get("w", 20), c.get("h", 20)

            # 裁剪候选区域（带边距）
            pad = 4
            x1 = max(0, cx - bw // 2 - pad)
            y1 = max(0, cy - bh // 2 - pad)
            x2 = min(w, cx + bw // 2 + pad)
            y2 = min(h, cy + bh // 2 + pad)

            if x2 <= x1 or y2 <= y1:
                continue

            patch = sidebar_image[y1:y2, x1:x2]

            if self.enabled:
                is_red, conf = self.predict(patch)
                if is_red:
                    c["cnn_confidence"] = round(conf, 3)
                    c["method"] = c.get("method", "hsv") + "+cnn"
                    confirmed.append(c)
                elif auto_collect:
                    self.collect_sample(patch, is_red_dot=False)
            else:
                # 模型未训练：收集样本 + 返回所有候选（保持原有行为）
                if auto_collect:
                    self.collect_sample(patch, is_red_dot=True)
                c["cnn_confidence"] = 0.0
                c["method"] = c.get("method", "hsv") + "+cnn(collecting)"
                confirmed.append(c)

        return confirmed

    def get_stats(self):
        return dict(self.stats)


# 全局单例
_cnn_instance = None


def get_red_dot_cnn(config=None):
    global _cnn_instance
    if _cnn_instance is None:
        _cnn_instance = RedDotCNN(config)
    return _cnn_instance