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
        self.max_retries = 3  # 增加重试次数
        self.retry_delay = 2  # 增加重试延迟
        # 全局回复风格预设（叠加在角色模板之上，对所有回复生效）
        self._style_preset = style_preset or {}
        # RAG 检索器引用（由外部注入）
        self._rag_retriever = None

        # 验证API Key
        self._validate_api_key()

    def _validate_api_key(self):
        """验证API Key格式和可用性"""
        if not self.api_key:
            logger.error("[LLM] API Key未配置")
            return False

        if not self.api_key.startswith("sk-"):
            logger.error(f"[LLM] API Key格式无效: {self.api_key[:10]}...")
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
        if not self.api_key.startswith("sk-"):
            return False, f"API Key 格式无效（应以 sk- 开头）"

        try:
            logger.info("[LLM] 测试API连接...")
            response = requests.post(
                f"{self.base_url.rstrip('/')}/v1/chat/completions",
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

        if not self.thinking:
            payload["thinking"] = {"type": "disabled"}

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        endpoint = f"{self.base_url.rstrip('/')}/v1/chat/completions"

        for attempt in range(self.max_retries):
            try:
                logger.debug(f"[LLM] 发起请求 (尝试 {attempt + 1}/{self.max_retries})")
                resp = requests.post(
                    endpoint,
                    json=payload,
                    headers=headers,
                    timeout=20,  # 增加超时时间
                )
                resp.raise_for_status()

                data = resp.json()
                reply = data["choices"][0]["message"]["content"].strip()

                if not reply:
                    logger.warning("[LLM] 返回空回复")
                    return None

                logger.info(f"[LLM] 请求成功，回复长度: {len(reply)}")
                return reply

            except requests.exceptions.Timeout:
                logger.warning(f"[LLM] 请求超时 (尝试 {attempt + 1}/{self.max_retries})")
                if attempt < self.max_retries - 1:
                    time.sleep(self.retry_delay * (attempt + 1))  # 指数退避
            except requests.exceptions.HTTPError as e:
                logger.error(f"[LLM] HTTP错误: {e.response.status_code} - {e.response.text[:100]}")
                if e.response.status_code in [401, 403]:
                    logger.error("[LLM] API Key无效或权限不足")
                    return None  # 认证错误不重试
                if attempt < self.max_retries - 1:
                    time.sleep(self.retry_delay)
            except requests.exceptions.RequestException as e:
                logger.error(f"[LLM] 请求异常: {e}")
                if attempt < self.max_retries - 1:
                    time.sleep(self.retry_delay)
            except (KeyError, IndexError) as e:
                logger.error(f"[LLM] 响应解析失败: {e}")
                logger.error(f"[LLM] 响应内容: {resp.text[:200] if 'resp' in locals() else 'N/A'}")
                return None  # 解析错误不重试
            except Exception as e:
                logger.error(f"[LLM] 未知异常: {e}")
                if attempt < self.max_retries - 1:
                    time.sleep(self.retry_delay)

        logger.error(f"[LLM] 所有重试均失败")
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

    def _build_messages(self, sender_name, message_content, role_config, conversation_context):
        """构建自动回复模式的消息列表"""
        system_prompt = role_config.get("system_prompt", "你是一个友好的聊天助手。")
        role_name = role_config.get("name", "助手")
        style = role_config.get("reply_style", "自然")

        full_system = (
            f"{system_prompt}\n\n"
            f"你的名字是{role_name}，说话风格：{style}。\n"
            f"你正在微信上跟朋友聊天，用简短、口语化的方式回复，1-3句话即可。"
        )

        # === 叠加：用户全局回复风格预设（reply_style_preset，优先级高于角色默认语气）===
        preset = getattr(self, "_style_preset", None) or {}
        if preset:
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
            if bits:
                full_system += (
                    "\n\n【回复风格设定】（全局生效，优先级高于角色默认语气）\n"
                    + "\n".join(bits)
                )

        # === 叠加：RAG 检索到的历史相关消息上下文 ===
        rag_context = ""
        try:
            rag = getattr(self, "_rag_retriever", None)
            if rag is not None and rag.enabled:
                rag_context = rag.build_context(
                    message_content, contact=sender_name)
                if rag_context:
                    full_system += (
                        "\n\n【历史相关消息】以下是过往相关对话，"
                        "如果与当前问题相关，请参考这些信息回复：\n"
                        + rag_context
                    )
        except Exception:
            pass

        messages = [{"role": "system", "content": full_system}]

        other_msgs = []
        for m in (conversation_context or []):
            if isinstance(m, dict):
                if m.get("role") == "user":
                    other_msgs.append(m.get("content", ""))
            elif isinstance(m, str):
                # 兼容旧调用方传入的纯文本上下文
                other_msgs.append(m)
        for prev_msg in other_msgs[-3:]:
            if prev_msg != message_content:
                messages.append({"role": "user", "content": prev_msg})

        messages.append({"role": "user", "content": message_content})

        return messages

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
            logger.info(f"LLM回复 ({sender_name}): {reply[:50]}...")
            return reply

        # ★ LLM 超时/失败降级兜底：保证自动回复链不静默失败
        #   （LLM 挂掉时不崩溃、不沉默，给一句温和的延迟回复）
        _fallback = (
            role_config.get("reply_style", "").find("正式") >= 0
            and "抱歉，我这会儿有点忙，稍后回复你。"
            or "收到~我这会儿有点事，晚点回你哈"
        )
        logger.warning(f"[LLM] 生成失败，使用降级模板回复: {_fallback[:30]}...")
        return _fallback