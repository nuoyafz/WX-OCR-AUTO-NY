"""
LLM 客户端模块 — 调用大模型（生成回复 + 信息提取）
===================================================
支持两种调用模式：
1. generate_reply(): 自动回复模式，构建角色prompt生成回复
2. _call_raw(): 底层调用，直接传入messages，供信息提取引擎使用
"""
import logging
import requests
import time

logger = logging.getLogger(__name__)


class LLMClient:
    """LLM API 客户端，支持 DeepSeek / OpenAI 兼容接口。"""

    def __init__(self, llm_config):
        self.provider = llm_config.get("provider", "deepseek")
        self.api_key = llm_config.get("api_key", "")
        self.model = llm_config.get("model", "deepseek-chat")
        self.base_url = llm_config.get("base_url", "https://api.deepseek.com")
        self.max_tokens = llm_config.get("max_tokens", 200)
        self.temperature = llm_config.get("temperature", 0.6)
        self.thinking = llm_config.get("thinking", False)
        self.max_retries = 2
        self.retry_delay = 1

    def _call_raw(self, messages, max_tokens=None, temperature=None):
        """
        底层API调用：直接传入messages列表，返回纯文本响应。
        供信息提取引擎和回复生成共用。

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
                resp = requests.post(
                    endpoint,
                    json=payload,
                    headers=headers,
                    timeout=15,
                )
                resp.raise_for_status()

                data = resp.json()
                reply = data["choices"][0]["message"]["content"].strip()
                return reply

            except requests.exceptions.Timeout:
                logger.warning(f"LLM请求超时 (尝试 {attempt + 1}/{self.max_retries})")
                if attempt < self.max_retries - 1:
                    time.sleep(self.retry_delay)
            except requests.exceptions.RequestException as e:
                logger.error(f"LLM请求失败: {e}")
                if attempt < self.max_retries - 1:
                    time.sleep(self.retry_delay)
            except (KeyError, IndexError) as e:
                logger.error(f"LLM响应解析失败: {e}")
                return None

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

        messages = [{"role": "system", "content": full_system}]

        other_msgs = [m["content"] for m in conversation_context if m.get("role") == "user"]
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
            str: AI生成的回复文本，失败返回 None
        """
        messages = self._build_messages(sender_name, message_content, role_config, conversation_context)
        reply = self._call_raw(messages)
        if reply:
            logger.info(f"LLM回复 ({sender_name}): {reply[:50]}...")
        return reply
