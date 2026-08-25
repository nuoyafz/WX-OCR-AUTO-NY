"""
RAG 本地知识库检索模块 — 向量检索增强 LLM 回复
====================================================
将历史消息索引用 Embedding 向量化，回复时检索 Top-K 最相关消息，
注入 LLM prompt 上下文，让 AI 具备"记忆"能力。

核心流程：
  用户消息 → embedding → ChromaDB 向量检索 Top-K 相关历史 → 拼入 prompt → LLM 生成回复

依赖：
  - chromadb (pip install chromadb)
  - sentence-transformers (与 semantic_dedup 共享模型)
  - 无依赖时自动降级，不影响主流程

集成点：llm_client.py generate_reply() / main.py 回复流水线
"""
import os
import time
import json
import hashlib
import logging
import threading
from collections import OrderedDict, deque
from datetime import datetime

logger = logging.getLogger(__name__)

_RAG_AVAILABLE = False  # 顶层不 import chromadb，避免 kubernetes/grpcio 依赖链加载


class RAGRetriever:
    """RAG 本地知识库检索器"""

    def __init__(self, config=None):
        self.config = config or {}
        self.enabled = bool(self.config.get("enabled", False))
        self.top_k = self.config.get("top_k", 5)
        self.similarity_threshold = self.config.get("similarity_threshold", 0.65)
        self.max_context_chars = self.config.get("max_context_chars", 800)
        self.collection_name = self.config.get("collection_name", "wechat_messages")

        self._client = None
        self._collection = None
        self._embed_model = None
        self._lock = threading.Lock()
        self._indexed_count = 0
        self._batch_buffer = deque()
        self._batch_size = self.config.get("batch_size", 10)
        self._batch_timer = 0

        if self.enabled:
            self._init_client()

    def _init_client(self):
        try:
            import os as _os
            db_dir = self.config.get("db_dir", "")
            if not db_dir:
                db_dir = _os.path.join(
                    _os.path.dirname(_os.path.abspath(__file__)),
                    "data", "chromadb")
            _os.makedirs(db_dir, exist_ok=True)

            self._client = chromadb.PersistentClient(
                path=db_dir,
                settings=Settings(anonymized_telemetry=False))
            try:
                self._collection = self._client.get_collection(
                    self.collection_name)
                self._indexed_count = self._collection.count()
                logger.info("[RAG] 已加载现有向量库: %d 条消息",
                            self._indexed_count)
            except Exception:
                self._collection = self._client.create_collection(
                    self.collection_name,
                    metadata={"hnsw:space": "cosine"})
                logger.info("[RAG] 已创建新向量库: %s", self.collection_name)

            self._init_embed_model()
            self.enabled = True
        except Exception as e:
            logger.warning("[RAG] 初始化失败: %s，降级为无检索模式", e)
            self.enabled = False

    def _init_embed_model(self):
        try:
            from sentence_transformers import SentenceTransformer
            model_name = self.config.get(
                "embedding_model", "all-MiniLM-L6-v2")
            self._embed_model = SentenceTransformer(model_name)
            logger.info("[RAG] Embedding 模型就绪: %s", model_name)
        except Exception as e:
            logger.warning("[RAG] Embedding 模型加载失败: %s", e)
            self.enabled = False

    def _encode(self, text):
        if self._embed_model is None:
            return None
        try:
            return self._embed_model.encode(
                text, convert_to_numpy=True, show_progress_bar=False)
        except Exception:
            return None

    def index_message(self, contact, sender, content, timestamp,
                      is_important=False, keywords=None):
        """
        索引单条消息到向量库。
        支持批量缓冲：达到 batch_size 条或超过 30s 才刷入。
        """
        if not self.enabled:
            return

        if not content or not content.strip():
            return

        msg_id = hashlib.md5(
            f"{contact}|{sender}|{content}|{timestamp}".encode()
        ).hexdigest()[:16]

        self._batch_buffer.append({
            "id": msg_id,
            "contact": contact,
            "sender": sender,
            "content": content.strip(),
            "timestamp": str(timestamp),
            "is_important": 1 if is_important else 0,
            "keywords": json.dumps(keywords or [], ensure_ascii=False),
        })

        if len(self._batch_buffer) >= self._batch_size:
            self._flush_batch()

    def _flush_batch(self):
        if not self._batch_buffer:
            return
        with self._lock:
            batch = list(self._batch_buffer)
            self._batch_buffer.clear()
            try:
                ids = [m["id"] for m in batch]
                docs = [m["content"] for m in batch]
                metas = [{
                    "contact": m["contact"],
                    "sender": m["sender"],
                    "timestamp": m["timestamp"],
                    "is_important": m["is_important"],
                    "keywords": m["keywords"],
                } for m in batch]
                self._collection.add(
                    ids=ids, documents=docs, metadatas=metas)
                self._indexed_count += len(batch)
                logger.debug("[RAG] 批量索引 %d 条消息", len(batch))
            except Exception as e:
                logger.warning("[RAG] 批量索引失败: %s", e)
                self._batch_buffer.extendleft(reversed(batch))

    def retrieve(self, query, contact=None, top_k=None):
        """
        检索与 query 最相关的历史消息。

        Args:
            query: 当前用户消息文本
            contact: 可选，限定检索某个联系人的消息
            top_k: 可选，返回条数（默认 self.top_k）

        Returns:
            list of dict: [{"content": ..., "contact": ..., "timestamp": ..., 
                            "similarity": ...}, ...]
        """
        if not self.enabled or self._collection is None:
            return []

        if top_k is None:
            top_k = self.top_k

        try:
            where_filter = None
            if contact:
                where_filter = {"contact": contact}

            results = self._collection.query(
                query_texts=[query],
                n_results=top_k,
                where=where_filter,
                include=["documents", "metadatas", "distances"])

            if not results or not results.get("documents") or not results["documents"][0]:
                return []

            retrieved = []
            for i, doc in enumerate(results["documents"][0]):
                meta = results["metadatas"][0][i] if results.get("metadatas") else {}
                distance = results["distances"][0][i] if results.get("distances") else 1.0
                similarity = 1.0 - float(distance) if distance is not None else 0.0

                if similarity < self.similarity_threshold:
                    continue

                retrieved.append({
                    "content": doc,
                    "contact": meta.get("contact", ""),
                    "sender": meta.get("sender", ""),
                    "timestamp": meta.get("timestamp", ""),
                    "similarity": round(similarity, 3),
                    "is_important": bool(meta.get("is_important")),
                })

            return retrieved

        except Exception as e:
            logger.warning("[RAG] 检索失败: %s", e)
            return []

    def build_context(self, query, contact=None, max_chars=None):
        """
        构建 RAG 上下文文本，直接拼入 LLM prompt。

        Returns:
            str: 格式化的上下文文本，或空字符串
        """
        if max_chars is None:
            max_chars = self.max_context_chars

        results = self.retrieve(query, contact=contact)
        if not results:
            return ""

        lines = []
        total_chars = 0
        header = "【相关历史消息】（来自过往对话，供参考）\n"
        total_chars += len(header)

        for r in results:
            ts = r.get("timestamp", "")[:16] if r.get("timestamp") else ""
            sender = r.get("sender", "未知")
            content = r.get("content", "")
            line = f"- [{ts}] {sender}: {content}\n"
            if total_chars + len(line) > max_chars:
                break
            lines.append(line)
            total_chars += len(line)

        if not lines:
            return ""

        return header + "".join(lines)

    def flush(self):
        self._flush_batch()

    def get_stats(self):
        return {
            "enabled": self.enabled,
            "indexed_count": self._indexed_count,
            "buffered": len(self._batch_buffer),
        }

    def clear(self):
        """清空向量库"""
        if self._collection is not None:
            try:
                self._client.delete_collection(self.collection_name)
                self._collection = self._client.create_collection(
                    self.collection_name,
                    metadata={"hnsw:space": "cosine"})
                self._indexed_count = 0
                self._batch_buffer.clear()
                logger.info("[RAG] 向量库已清空")
            except Exception as e:
                logger.warning("[RAG] 清空失败: %s", e)


# 全局单例
_rag_instance = None


def get_rag_retriever(config=None):
    global _rag_instance
    if _rag_instance is None:
        _rag_instance = RAGRetriever(config)
    return _rag_instance