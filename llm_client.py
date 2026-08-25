"""
LLM 客户端模块 — 调用大模型（生成回复 + 信息提取）
===================================================
修复内容：
1. 增强API Key验证
2. 改进错误处理和重试
3. 增加测试调用功能
4. 优化日志输出
"""
import logging
import requests
import time
from datetime import datetime

logger = logging.getLogger(__name__)


class LLMClient:
    """LLM API 客户端，支持 DeepSeek / OpenAI 兼容接口。"""

    def __init__(self, llm_config, style_preset=None):
        self.provider = llm_config.get("provider", "custom")
        self.api_key = llm_config.get("api_key", "")
        self.model = llm_config.get("model", "qwen3.7-flash-2026-07-15")
        self.base_url = llm_config.get("base_url", "https://dashscope.aliyuncs.com/compatible-mode")
        self.max_tokens = llm_config.get("max_tokens", 200)
        self.temperature = llm_config.get("temperature", 0.6)
        self.thinking = llm_config.get("thinking", False)
        self.max_retries = 2  # 最多重试2次（配合30s硬超时）
        self.retry_delay = 1.5  # 重试延迟
        # 全局回复风格预设（叠加在角色模板之上，对所有回复生效）
        self._style_preset = style_preset or {}
        # RAG 检索器引用（由外部注入）
        self._rag_retriever = None
        # 日志回调（转发到 UI）
        self._log_callback = None

        # 验证API Key
        self._validate_api_key()

    def set_log_callback(self, callback):
        """设置日志回调，将 LLM 内部日志转发到 UI 面板"""
        self._log_callback = callback

    def _log(self, level, message):
        """统一日志：输出到本地 logger + 转发到 UI 回调"""
        getattr(logger, level.lower(), logger.info)(message)
        if self._log_callback:
            try:
                self._log_callback(level, message)
            except Exception:
                pass

    def _build_endpoint(self):
        """智能拼接 chat/completions 端点，兼容各厂商不同 URL 格式。

        标准 OpenAI 兼容 API 路径为 /v1/chat/completions，
        但智谱(/api/paas/v4/chat/completions)、火山引擎(/api/v3/chat/completions)
        等厂商路径不同，硬编码 /v1 会导致 404。

        规则：
        1. base_url 已含 chat/completions → 原样使用
        2. base_url 以 /v1~/v4 结尾 → 追加 /chat/completions
        3. 其他 → 追加 /v1/chat/completions
        """
        import re
        url = (self.base_url or "").rstrip("/")
        if "/chat/completions" in url:
            return url
        if re.search(r"/v\d+$", url):
            return f"{url}/chat/completions"
        return f"{url}/v1/chat/completions"

    def _validate_api_key(self):
        """验证API Key格式和可用性"""
        if not self.api_key:
            logger.error("[LLM] API Key未配置")
            return False

        if len(self.api_key) < 8:
            logger.error(f"[LLM] API Key过短: {self.api_key[:6]}...")
            return False

        logger.info(f"[LLM] API Key格式验证通过: {self.api_key[:10]}...")
        return True

    def set_rag_retriever(self, rag):
        """注入 RAG 检索器，启用历史消息检索增强回复"""
        self._rag_retriever = rag

    def test_connection(self):
        """测试连接和API Key有效性

        Returns:
            tuple: (success: bool, message: str)
        """
        if not self.api_key:
            return False, "API Key 未配置"
        if len(self.api_key) < 8:
            return False, "API Key 过短，请检查是否完整复制"

        try:
            logger.info("[LLM] 测试API连接...")
            response = requests.post(
                self._build_endpoint(),
                json={
                    "model": self.model,
                    "messages": [{"role": "user", "content": "test"}],
                    "max_tokens": 5
                },
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json"
                },
                timeout=10
            )

            if response.status_code == 200:
                data = response.json()
                reply = ""
                try:
                    reply = data["choices"][0]["message"]["content"]
                except Exception:
                    pass
                logger.info("[LLM] API连接测试成功")
                return True, f"连接成功，模型回复: {reply[:30]}"
            elif response.status_code == 401:
                return False, "认证失败：API Key 无效或已过期"
            elif response.status_code == 403:
                return False, "权限不足：API Key 无权访问该模型"
            elif response.status_code == 404:
                return False, f"模型不存在：{self.model}"
            else:
                try:
                    err_msg = response.json().get("error", {}).get("message", "")[:80]
                except Exception:
                    err_msg = response.text[:80]
                logger.error(f"[LLM] API连接测试失败: {response.status_code}")
                return False, f"HTTP {response.status_code}: {err_msg}"

        except requests.exceptions.Timeout:
            logger.error("[LLM] API连接超时")
            return False, "连接超时（10秒无响应）"
        except requests.exceptions.ConnectionError:
            logger.error("[LLM] API连接失败")
            return False, f"无法连接到 {self.base_url}"
        except Exception as e:
            logger.error(f"[LLM] API连接异常: {e}")
            return False, f"异常: {str(e)[:60]}"

    def _call_raw(self, messages, max_tokens=None, temperature=None):
        """
        底层API调用：直接传入messages列表，返回纯文本响应。
        供信息提取引擎和回复生成共用。

        修复内容：
        1. 增强错误处理和重试
        2. 改进日志输出
        3. 增加超时处理
        4. 响应验证

        Args:
            messages: [{"role": "system"/"user"/"assistant", "content": "..."}, ...]
            max_tokens: 覆盖默认max_tokens
            temperature: 覆盖默认temperature

        Returns:
            str: LLM响应文本，失败返回 None
        """
        payload = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens or self.max_tokens,
            "temperature": temperature if temperature is not None else self.temperature,
        }

        # thinking 参数是 DeepSeek 官方 API 专属扩展，仅对 api.deepseek.com 发送
        # 阿里云 DashScope 等兼容端点不认识此参数，发送反而可能导致模型行为异常
        if not self.thinking and "deepseek.com" in self.base_url.lower():
            payload["thinking"] = {"type": "disabled"}

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        endpoint = self._build_endpoint()

        for attempt in range(self.max_retries):
            try:
                self._log("debug", f"[LLM] 发起请求 (尝试 {attempt + 1}/{self.max_retries})")
                resp = requests.post(
                    endpoint,
                    json=payload,
                    headers=headers,
                    timeout=10,  # 单次请求超时（2次重试×10s=20s，在30s硬超时内）
                )
                resp.raise_for_status()

                data = resp.json()
                reply = data["choices"][0]["message"]["content"].strip()

                if not reply:
                    self._log("warning", "[LLM] 返回空回复")
                    return None

                self._log("info", f"[LLM] 请求成功，回复长度: {len(reply)}")
                return reply

            except requests.exceptions.Timeout:
                self._log("warning", f"[LLM] 请求超时 (尝试 {attempt + 1}/{self.max_retries})")
                if attempt < self.max_retries - 1:
                    time.sleep(self.retry_delay * (attempt + 1))  # 指数退避
            except requests.exceptions.HTTPError as e:
                self._log("error", f"[LLM] HTTP错误: {e.response.status_code} - {e.response.text[:100]}")
                if e.response.status_code in [401, 403]:
                    self._log("error", "[LLM] API Key无效或权限不足")
                    return None  # 认证错误不重试
                if attempt < self.max_retries - 1:
                    time.sleep(self.retry_delay)
            except requests.exceptions.RequestException as e:
                self._log("error", f"[LLM] 请求异常: {e}")
                if attempt < self.max_retries - 1:
                    time.sleep(self.retry_delay)
            except (KeyError, IndexError) as e:
                self._log("error", f"[LLM] 响应解析失败: {e}")
                self._log("error", f"[LLM] 响应内容: {resp.text[:200] if 'resp' in locals() else 'N/A'}")
                return None  # 解析错误不重试
            except Exception as e:
                self._log("error", f"[LLM] 未知异常: {e}")
                if attempt < self.max_retries - 1:
                    time.sleep(self.retry_delay)

        self._log("error", f"[LLM] 所有重试均失败")
        return None

    def call_with_image(self, system_prompt, user_text, image_base64, max_tokens=None, temperature=None):
        """
        多模态调用：发送文本+图片给LLM。

        Args:
            system_prompt: 系统提示词
            user_text: 用户文本提示
            image_base64: base64编码的图片字符串
            max_tokens: 最大token数
            temperature: 温度参数

        Returns:
            str: LLM响应文本，失败返回 None
        """
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": [
                {"type": "text", "text": user_text},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_base64}"}}
            ]}
        ]
        return self._call_raw(messages, max_tokens=max_tokens, temperature=temperature)

    # ========================================================================
    # Token 预算常量
    # ========================================================================
    _TOKEN_BUDGET_SYSTEM = 2000   # system prompt 上限（字符，约 1000 tokens）
    _TOKEN_BUDGET_TOTAL = 6000    # 整个 messages 总上限（字符，约 3000 tokens）
    _TOKEN_COMPRESS_THRESHOLD = 800  # 上下文超过此字符数触发压缩

    @staticmethod
    def _estimate_tokens(text):
        """粗略估算 token 数：中文 1 字≈1 token，英文 1 词≈1 token"""
        import re
        _cn = len(re.findall(r'[\u4e00-\u9fff]', text))
        _en = len(re.findall(r'[a-zA-Z]+', text))
        _other = len(text) - _cn - sum(len(w) for w in re.findall(r'[a-zA-Z]+', text))
        return _cn + _en + max(0, _other // 3)

    def _build_messages(self, sender_name, message_content, role_config, conversation_context):
        """构建自动回复模式的消息列表（带 token 预算控制）"""
        system_prompt = role_config.get("system_prompt", "你是一个友好的聊天助手。")
        role_name = role_config.get("name", "助手")
        style = role_config.get("reply_style", "自然")

        full_system = (
            f"{system_prompt}\n\n"
            f"你的名字是{role_name}，说话风格：{style}。\n"
            f"你正在微信上跟朋友聊天，用简短、口语化的方式回复，1-3句话即可。"
        )

        # === 回复风格预设（可裁剪） ===
        _style_bits = self._build_style_bits()
        if _style_bits:
            full_system += "\n\n【回复风格设定】\n" + "\n".join(_style_bits)

        # === RAG 上下文（可裁剪） ===
        _rag_text = self._build_rag_context(message_content, sender_name)

        # === 上下文窗口：取最近 10 条，保留 role 标记，过长时压缩 ===
        all_msgs = []
        for m in (conversation_context or []):
            if isinstance(m, dict):
                _msg_role = m.get("role", "user")
                _content = str(m.get("content", "")).strip()
                if _content:
                    all_msgs.append({"role": _msg_role, "content": _content})
            elif isinstance(m, str):
                all_msgs.append({"role": "user", "content": m.strip()})

        recent = []
        for m in all_msgs[-10:]:
            if m["content"] != message_content:
                recent.append(m)

        _ctx_total = sum(len(m["content"]) for m in recent)
        if _ctx_total > self._TOKEN_COMPRESS_THRESHOLD and len(recent) > 3:
            _split = max(2, len(recent) // 2)
            _old = recent[:_split]
            _new = recent[_split:]
            _summary = "；".join(m["content"][:50] for m in _old)
            full_system += "\n\n【对话上文摘要】" + _summary
            recent = _new

        # === ★ Token 预算控制：按优先级裁剪 system prompt ===
        # 优先级：system_prompt 核心 > 风格设定 > 上文摘要 > RAG
        _base_system = full_system
        _rag_added = False
        if _rag_text:
            _candidate = full_system + "\n\n【历史相关消息】\n" + _rag_text
            if len(_candidate) <= self._TOKEN_BUDGET_SYSTEM:
                full_system = _candidate
                _rag_added = True
            else:
                _trimmed_rag = _rag_text[:self._TOKEN_BUDGET_SYSTEM - len(full_system) - 50]
                if _trimmed_rag:
                    full_system += "\n\n【历史相关消息】\n" + _trimmed_rag
                    _rag_added = True

        # 如果 system prompt 仍然超预算，裁剪风格设定
        if len(full_system) > self._TOKEN_BUDGET_SYSTEM and _style_bits:
            full_system = _base_system
            if _rag_added:
                _rag_section = full_system[full_system.find("【历史相关消息】"):] if "【历史相关消息】" in full_system else ""
                full_system = _base_system + _rag_section
            # 裁剪风格设定到只剩语气
            _tone = None
            preset = getattr(self, "_style_preset", None) or {}
            if preset.get("tone"):
                _tone = f"- 语气：{preset['tone']}"
            if _tone and len(full_system + "\n\n【回复风格设定】\n" + _tone) <= self._TOKEN_BUDGET_SYSTEM:
                full_system += "\n\n【回复风格设定】\n" + _tone

        # 最后兜底：硬截断
        if len(full_system) > self._TOKEN_BUDGET_SYSTEM + 200:
            full_system = full_system[:self._TOKEN_BUDGET_SYSTEM] + "…"

        messages = [{"role": "system", "content": full_system}]

        for m in recent:
            messages.append({"role": m["role"], "content": m["content"]})

        messages.append({"role": "user", "content": message_content})

        return messages

    def _build_style_bits(self):
        """构建风格预设文本片段（供 _build_messages 和裁剪逻辑共用）"""
        preset = getattr(self, "_style_preset", None) or {}
        if not preset:
            return []
        bits = []
        if preset.get("tone"):
            bits.append(f"- 语气：{preset['tone']}")
        if preset.get("max_sentences"):
            bits.append(f"- 长度：每条不超过 {preset['max_sentences']} 句")
        if preset.get("emoji") is not None:
            bits.append(f"- emoji：{'允许' if preset['emoji'] else '禁止'}")
        if preset.get("pet_words"):
            bits.append(f"- 口头禅：{'、'.join(preset['pet_words'])}")
        if preset.get("forbidden"):
            bits.append(f"- 禁忌（绝不可出现）：{'、'.join(preset['forbidden'])}")
        if preset.get("must_include"):
            bits.append(f"- 必须包含：{'、'.join(preset['must_include'])}")
        if preset.get("notes"):
            bits.append(f"- 备注：{preset['notes']}")
        return bits

    def _build_rag_context(self, message_content, sender_name):
        """构建 RAG 上下文文本（供 _build_messages 和裁剪逻辑共用）"""
        try:
            rag = getattr(self, "_rag_retriever", None)
            if rag is not None and rag.enabled:
                return rag.build_context(message_content, contact=sender_name)
        except Exception:
            pass
        return ""

    def generate_reply(self, sender_name, message_content, role_config, conversation_context):
        """
        调用LLM生成自动回复。

        Args:
            sender_name: 发送者名称
            message_content: 对方发来的消息内容
            role_config: 角色配置
            conversation_context: 对话历史

        Returns:
            str: AI生成的回复文本。LLM 超时/失败时有降级兜底，不返回 None（避免静默失败/崩溃）。
        """
        messages = self._build_messages(sender_name, message_content, role_config, conversation_context)
        reply = self._call_raw(messages)
        if reply:
            self._log("info", f"LLM回复 ({sender_name}): {reply[:50]}...")
            return reply

        # ★ LLM 超时/失败降级兜底：根据对方消息类型智能选择模板
        _msg = message_content.strip()
        _fb = self._pick_fallback(_msg, role_config)
        self._log("warning", f"[LLM] 生成失败，使用降级模板回复: {_fb[:30]}...")
        return _fb

    @staticmethod
    def _pick_fallback(message, role_config):
        """根据消息内容智能选择降级模板"""
        _m = message.strip()
        _style = role_config.get("reply_style", "")

        # 问句
        if any(kw in _m for kw in ["?", "？", "吗", "呢", "怎么", "什么", "谁", "哪", "几点", "多少"]):
            return "这个问题我暂时没法回答，晚点帮你看看~"

        # 紧急
        if any(kw in _m for kw in ["急", "快", "救命", "SOS", "赶紧", "马上"]):
            return "看到啦，马上来！"

        # 图片/表情（OCR 后只剩描述性文字）
        if any(kw in _m for kw in ["图片", "表情", "贴图", "[表情]", "[图片]"]):
            import random
            return random.choice(["收到~", "哈哈", "有意思", "看看", "👀"])

        # 分享/链接
        if any(kw in _m for kw in ["http", "www.", "链接", "分享"]):
            return "收到，我看看~"

        # 正式风格
        if "正式" in _style:
            return "抱歉，我这会儿有点忙，稍后回复你。"

        # 默认
        return "收到~我这会儿有点事，晚点回你哈"