"""
语义去重模块 — 基于本地 Embedding 的语义向量去重
====================================================
替代原有 pHash 去重，用句子向量做语义级相似度判断。
- 短文本（<8字）：精确匹配 + 编辑距离兜底
- 长文本：向量余弦相似度 > 阈值 → 视为重复
- 模型：sentence-transformers all-MiniLM-L6-v2（80MB，CPU 推理 <5ms）
- 无依赖时自动降级为原有 pHash 逻辑

集成点：smart_monitor.py / storage.py / main.py 的去重入口
"""
import os
import time
import hashlib
import logging
import threading
from collections import OrderedDict

logger = logging.getLogger(__name__)

# 尝试导入 sentence-transformers，失败则降级
_SEMANTIC_AVAILABLE = False
_EMBED_MODEL = None
_EMBED_LOCK = threading.Lock()

# 默认关闭语义向量（避免模块级触发 torch C++ DLL access violation 崩溃）
# 开启：config.yaml -> smart_monitor.semantic_dedup.enabled: true + pip install sentence-transformers


class SemanticDeduplicator:
    """语义向量去重器"""

    def __init__(self, config=None):
        self.config = config or {}
        self.similarity_threshold = self.config.get("semantic_threshold", 0.88)
        self.max_cache = self.config.get("semantic_cache_size", 500)
        self.min_text_len = self.config.get("semantic_min_len", 8)

        # 向量缓存：OrderedDict 实现 LRU
        self._embedding_cache = OrderedDict()
        # 精确匹配缓存（短文本兜底）
        self._exact_cache = set()
        self._max_exact = 500
        # 统计
        self.stats = {"semantic_hits": 0, "semantic_misses": 0, "exact_hits": 0}

        self._model = None
        self.enabled = bool(self.config.get("enabled", False))
        if self.enabled:
            self._init_model()
            if self._model is None:
                self.enabled = False
                logger.info("[语义去重] 模型不可用，自动降级到精确/编辑距离方案。")
        else:
            logger.info("[语义去重] 默认关闭。走精确/编辑距离降级方案。")

    def _init_model(self):
        """只有显式 enabled=true 才进。局部 import，失败绝对不崩。"""
        global _EMBED_MODEL
        with _EMBED_LOCK:
            if _EMBED_MODEL is None:
                try:
                    from sentence_transformers import SentenceTransformer
                except Exception as e:
                    logger.warning("[语义去重] import sentence_transformers 失败: %s", e)
                    return
                try:
                    model_name = self.config.get(
                        "embedding_model", "all-MiniLM-L6-v2")
                    _EMBED_MODEL = SentenceTransformer(model_name)
                    logger.info("[语义去重] 模型加载完成: %s", model_name)
                except Exception as e:
                    logger.warning("[语义去重] 模型加载失败: %s", e)
                    return
        self._model = _EMBED_MODEL

    def _encode(self, text):
        if self._model is None:
            return None
        try:
            vec = self._model.encode(text, convert_to_numpy=True,
                                     show_progress_bar=False)
            return vec
        except Exception as e:
            logger.debug("[语义去重] 编码失败: %s", e)
            return None

    def _cosine_similarity(self, a, b):
        import numpy as np
        dot = np.dot(a, b)
        norm_a = np.linalg.norm(a)
        norm_b = np.linalg.norm(b)
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return float(dot / (norm_a * norm_b))

    def _edit_distance(self, a, b):
        """Levenshtein 编辑距离（短文本兜底）"""
        if abs(len(a) - len(b)) > 4:
            return 999
        m, n = len(a), len(b)
        dp = [[0] * (n + 1) for _ in range(m + 1)]
        for i in range(m + 1):
            dp[i][0] = i
        for j in range(n + 1):
            dp[0][j] = j
        for i in range(1, m + 1):
            for j in range(1, n + 1):
                cost = 0 if a[i - 1] == b[j - 1] else 1
                dp[i][j] = min(dp[i - 1][j] + 1, dp[i][j - 1] + 1,
                               dp[i - 1][j - 1] + cost)
        return dp[m][n]

    def is_duplicate(self, text):
        """
        判断消息是否重复。
        Returns: True = 重复（应跳过），False = 新消息
        """
        if not text or not text.strip():
            return True

        text = text.strip()

        # 第1层：精确匹配（所有长度都查）
        if text in self._exact_cache:
            self.stats["exact_hits"] += 1
            return True

        # 第2层：短文本用编辑距离兜底
        if len(text) < self.min_text_len:
            for cached in list(self._exact_cache)[-50:]:
                if self._edit_distance(text, cached) <= 2:
                    self.stats["exact_hits"] += 1
                    return True
            self._add_to_exact(text)
            return False

        # 第3层：语义向量去重（长文本）
        if self._model is not None:
            vec = self._encode(text)
            if vec is not None:
                for cached_vec, cached_text in self._embedding_cache.values():
                    sim = self._cosine_similarity(vec, cached_vec)
                    if sim >= self.similarity_threshold:
                        self.stats["semantic_hits"] += 1
                        logger.debug(
                            "[语义去重] 重复: '%s' ~ '%s' (sim=%.3f)",
                            text[:30], cached_text[:30], sim)
                        return True
                self._add_to_cache(text, vec)
                self.stats["semantic_misses"] += 1
                return False

        # 第4层：pHash 降级（模型不可用时）
        self._add_to_exact(text)
        return False

    def _add_to_exact(self, text):
        self._exact_cache.add(text)
        if len(self._exact_cache) > self._max_exact:
            for _ in range(self._max_exact // 4):
                try:
                    self._exact_cache.pop()
                except KeyError:
                    break

    def _add_to_cache(self, text, vec):
        key = hashlib.md5(text.encode()).hexdigest()
        self._embedding_cache[key] = (vec.copy(), text)
        if len(self._embedding_cache) > self.max_cache:
            self._embedding_cache.popitem(last=False)

    def reset(self):
        self._embedding_cache.clear()
        self._exact_cache.clear()

    def get_stats(self):
        return dict(self.stats)


# 全局单例
_dedup_instance = None


def get_semantic_dedup(config=None):
    global _dedup_instance
    if _dedup_instance is None:
        _dedup_instance = SemanticDeduplicator(config)
    return _dedup_instance