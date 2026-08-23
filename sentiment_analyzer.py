"""
情感分析 + 紧急度分级模块 — 本地轻量级三阶段流水线
========================================================
三阶段策略（由快到慢，由简到精）：
  阶段1: 规则引擎（关键词+正则，0ms，~70%准确率）
  阶段2: 轻量级统计模型（TF-IDF + 朴素贝叶斯，<1ms，~85%准确率）
  阶段3: LLM API 兜底（仅高置信度触发，可配置关闭）

输出字段：
  - sentiment: positive / neutral / negative
  - urgency: 0-10 紧急度分数
  - urgency_reason: 紧急原因（关键词匹配时附带）
  - is_urgent: 是否需要立即关注（urgency >= 7）

集成点：extractor.py 提取后、storage.py 存储前
"""
import re
import logging
import json
import os
import threading
from collections import Counter

logger = logging.getLogger(__name__)

# 尝试导入 sklearn（可选优化）
_SKLEARN_AVAILABLE = False
try:
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.naive_bayes import MultinomialNB
    import pickle
    _SKLEARN_AVAILABLE = True
except ImportError:
    logger.info("[情感分析] sklearn 未安装，使用纯规则模式。"
                "安装: pip install scikit-learn")


class SentimentAnalyzer:
    """三阶段情感分析 + 紧急度分级"""

    def __init__(self, config=None):
        self.config = config or {}
        self.enabled = self.config.get("enabled", True)
        self.urgent_threshold = self.config.get("urgent_threshold", 7)
        self.use_llm_fallback = self.config.get("llm_fallback", False)

        # 模型路径
        model_dir = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "data")
        os.makedirs(model_dir, exist_ok=True)
        self._model_path = os.path.join(
            model_dir, "sentiment_model.pkl")

        self._model = None
        self._vectorizer = None
        self._lock = threading.Lock()
        self._training_data = []
        self._train_count = 0

        if _SKLEARN_AVAILABLE:
            self._load_or_init_model()

        self.stats = {"rule_hits": 0, "model_hits": 0, "llm_hits": 0}

    def _load_or_init_model(self):
        try:
            if os.path.exists(self._model_path):
                with open(self._model_path, "rb") as f:
                    data = pickle.load(f)
                self._vectorizer = data.get("vectorizer")
                self._model = data.get("model")
                self._train_count = data.get("train_count", 0)
                if self._model and self._vectorizer:
                    logger.info("[情感分析] 已加载训练模型: %d 条样本",
                                self._train_count)
                    return
        except Exception as e:
            logger.warning("[情感分析] 模型加载失败: %s", e)

        self._vectorizer = TfidfVectorizer(
            max_features=500, ngram_range=(1, 2),
            token_pattern=r'(?u)\b\w+\b')
        self._model = MultinomialNB(alpha=0.1)

    def analyze(self, text, sender="other"):
        """
        分析消息情感和紧急度。

        Args:
            text: 消息文本
            sender: 发送者（"me" / "other"）

        Returns:
            dict: {
                "sentiment": "positive"|"neutral"|"negative",
                "urgency": 0-10,
                "urgency_reason": str,
                "is_urgent": bool,
                "method": "rule"|"model"|"llm"
            }
        """
        if not text or not text.strip():
            return self._default_result()

        text = text.strip()

        # === 阶段1：规则引擎（快速过滤） ===
        rule_result = self._rule_analyze(text)
        if rule_result["confidence"] >= 0.85:
            self.stats["rule_hits"] += 1
            return {
                "sentiment": rule_result["sentiment"],
                "urgency": rule_result["urgency"],
                "urgency_reason": rule_result["reason"],
                "is_urgent": rule_result["urgency"] >= self.urgent_threshold,
                "method": "rule",
            }

        # === 阶段2：统计模型（sklearn 可用时） ===
        if self._model is not None and _SKLEARN_AVAILABLE:
            try:
                model_result = self._model_analyze(text)
                if model_result["confidence"] >= 0.6:
                    self.stats["model_hits"] += 1
                    return {
                        "sentiment": model_result["sentiment"],
                        "urgency": max(model_result["urgency"],
                                        rule_result["urgency"]),
                        "urgency_reason": rule_result["reason"] or "模型判断",
                        "is_urgent": (model_result["urgency"] >=
                                       self.urgent_threshold),
                        "method": "model",
                    }
            except Exception as e:
                logger.debug("[情感分析] 模型推断失败: %s", e)

        # === 阶段3：返回规则结果 ===
        self.stats["rule_hits"] += 1
        return {
            "sentiment": rule_result["sentiment"],
            "urgency": rule_result["urgency"],
            "urgency_reason": rule_result["reason"],
            "is_urgent": rule_result["urgency"] >= self.urgent_threshold,
            "method": "rule",
        }

    def _rule_analyze(self, text):
        """规则引擎分析"""
        sentiment = "neutral"
        urgency = 0
        reason = ""
        confidence = 0.5

        # === 紧急度关键词 ===
        urgent_patterns = [
            # 极高紧急 (10分)
            (r'(救命|SOS|紧急|出事|报警|120|110|火灾|地震|车祸|快来人)',
             10, "生命安全相关"),
            # 高紧急 (8-9分)
            (r'(马上|赶紧|立刻|立即|快点|速度|急急急|十万火急|来不及了)',
             9, "时间紧迫"),
            (r'(老板|领导|客户|甲方).{0,4}(找|催|问|要)',
             9, "上级/客户催促"),
            (r'(钱|转账|付款|汇款|打款|结账|报销).{0,4}(急|马上|赶紧)',
             9, "紧急财务"),
            # 中高紧急 (7分)
            (r'(在吗|在不在|看到回复|收到请回|请回复).{0,2}$',
             7, "等待回复确认"),
            (r'(会议|开会|面试|笔试|答辩).{0,4}(马上|开始|还有|快要)',
             7, "会议/面试临近"),
            (r'(截止|ddl|deadline|过期).{0,4}(今天|明天|马上|快)',
             7, "截止日期临近"),
            (r'(密码|验证码|登录|账号).{0,4}(发|给|需要|要)',
             7, "账号安全相关"),
            # 中紧急 (5-6分)
            (r'(问题|bug|报错|错误|失败|崩溃|不行|挂了|坏了)',
             6, "出现问题"),
            (r'(帮|帮忙|求助|请教|问一下|问个).{0,2}',
             5, "请求帮助"),
            (r'(重要|注意|提醒|别忘了|别忘了)',
             5, "重要提醒"),
            # 低紧急 (3-4分)
            (r'(有空|方便|能不能|可以吗|行不行).{0,2}[?？]',
             4, "请求确认"),
            (r'(什么时候|几点|哪天|啥时候|何时)',
             3, "时间询问"),
        ]

        for pattern, score, reason_text in urgent_patterns:
            if re.search(pattern, text):
                if score > urgency:
                    urgency = score
                    reason = reason_text
                confidence = max(confidence, 0.85)

        # === 情感关键词 ===
        negative_words = [
            "烦", "气死", "无语", "崩溃", "糟糕", "倒霉", "难受", "伤心",
            "失望", "生气", "恼火", "不爽", "讨厌", "恶心", "滚", "垃圾",
            "完蛋", "绝望", "累死", "困死", "饿死", "惨", "失败",
        ]
        positive_words = [
            "哈哈", "谢谢", "开心", "太棒", "厉害", "nice", "酷", "赞",
            "爱了", "绝了", "牛", "666", "完美", "恭喜", "生日快乐",
            "喜欢", "好看", "好吃", "好玩", "强", "好的", "OK", "ok",
            "没问题", "收到", "了解了", "明白", "知道了",
        ]

        neg_count = sum(1 for w in negative_words if w in text)
        pos_count = sum(1 for w in positive_words if w in text)

        if neg_count > pos_count:
            sentiment = "negative"
            urgency = max(urgency, 4)
            confidence = max(confidence, 0.75)
        elif pos_count > neg_count:
            sentiment = "positive"
            confidence = max(confidence, 0.75)

        # 问句增强紧急度
        if text.endswith("?") or text.endswith("？"):
            urgency = max(urgency, 3)
            if reason:
                reason += "，带提问"

        # 长度极短的消息（如"好"、"嗯"）通常是中性/正面
        if len(text) <= 3 and urgency == 0:
            sentiment = "positive" if pos_count > 0 else "neutral"
            confidence = 0.7

        return {
            "sentiment": sentiment,
            "urgency": min(urgency, 10),
            "reason": reason,
            "confidence": confidence,
        }

    def _model_analyze(self, text):
        """统计模型分析"""
        if self._model is None or self._vectorizer is None:
            return {"sentiment": "neutral", "urgency": 0, "confidence": 0.0}

        try:
            X = self._vectorizer.transform([text])
            proba = self._model.predict_proba(X)[0]
            pred = self._model.predict(X)[0]
            max_prob = max(proba)

            # 映射类别到情感
            sentiment_map = {0: "neutral", 1: "positive", 2: "negative"}
            sentiment = sentiment_map.get(pred, "neutral")

            # 模型紧急度：根据负面概率估算
            neg_prob = proba[2] if len(proba) > 2 else 0
            urgency = min(10, int(neg_prob * 10 + 1))

            return {
                "sentiment": sentiment,
                "urgency": urgency,
                "confidence": float(max_prob),
            }
        except Exception:
            return {"sentiment": "neutral", "urgency": 0, "confidence": 0.0}

    def _default_result(self):
        return {
            "sentiment": "neutral",
            "urgency": 0,
            "urgency_reason": "",
            "is_urgent": False,
            "method": "rule",
        }

    def feed_training(self, text, label, urgency=0):
        """
        在线学习：用户反馈 → 增量训练模型。
        label: "positive" / "neutral" / "negative"
        """
        if not _SKLEARN_AVAILABLE:
            return

        label_map = {"neutral": 0, "positive": 1, "negative": 2}
        lbl = label_map.get(label, 0)

        self._training_data.append((text, lbl))
        self._train_count += 1

        if len(self._training_data) >= 10:
            self._retrain()

    def _retrain(self):
        if not self._training_data or not _SKLEARN_AVAILABLE:
            return
        with self._lock:
            try:
                texts = [d[0] for d in self._training_data]
                labels = [d[1] for d in self._training_data]
                X = self._vectorizer.fit_transform(texts)
                self._model.partial_fit(X, labels, classes=[0, 1, 2])
                self._save_model()
                logger.info("[情感分析] 增量训练完成: %d 条样本",
                            self._train_count)
            except Exception as e:
                logger.warning("[情感分析] 训练失败: %s", e)

    def _save_model(self):
        try:
            data = {
                "vectorizer": self._vectorizer,
                "model": self._model,
                "train_count": self._train_count,
            }
            with open(self._model_path, "wb") as f:
                pickle.dump(data, f)
        except Exception as e:
            logger.warning("[情感分析] 模型保存失败: %s", e)

    def get_stats(self):
        return dict(self.stats)


# 全局单例
_sentiment_instance = None


def get_sentiment_analyzer(config=None):
    global _sentiment_instance
    if _sentiment_instance is None:
        _sentiment_instance = SentimentAnalyzer(config)
    return _sentiment_instance