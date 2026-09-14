from __future__ import annotations

import json
import os
import re

from dotenv import load_dotenv
from openai import AsyncOpenAI

load_dotenv()

JSON_EXTRACT_PROMPT = (
    "请从以下信息中提取结构化数据，严格按照 system prompt 要求的 JSON 格式输出。\n"
    "只输出 JSON，不要附带任何其他文字。\n\n"
)


class LLMClient:
    """Chat Completions API 封装。需要联网搜索时用搜索模型获取信息，再用主模型结构化。"""

    def __init__(self, *, model: str | None = None) -> None:
        self.model = model if model is not None else os.getenv("OPENAI_MODEL")
        if not self.model or not self.model.strip():
            raise ValueError("API mode requires --model or OPENAI_MODEL for your API provider")
        self.model = self.model.strip()
        self.search_model = os.getenv("OPENAI_SEARCH_MODEL")
        if not self.search_model or not self.search_model.strip():
            raise ValueError("API mode requires OPENAI_SEARCH_MODEL for your provider's search model")
        self.search_model = self.search_model.strip()
        self.client = AsyncOpenAI(
            api_key=os.getenv("OPENAI_API_KEY"),
            base_url=os.getenv("OPENAI_BASE_URL") or None,
        )
        self.total_input_tokens = 0
        self.total_output_tokens = 0

    async def query(
        self,
        system: str,
        user: str,
        *,
        web_search: bool = False,
    ) -> str:
        """发送一次请求，返回纯文本。web_search=True 时走搜索模型。"""
        model = self.search_model if web_search else self.model
        return await self._call(model, system, user)

    async def query_json(
        self,
        system: str,
        user: str,
        *,
        web_search: bool = False,
    ) -> dict:
        """发送请求并返回 JSON。搜索模型不支持 JSON mode，需两步完成。"""
        if not web_search:
            text = await self._call(self.model, system, user, json_mode=True)
            return json.loads(self._strip_codeblock(text))

        raw = await self._call(self.search_model, system, user)
        try:
            return json.loads(self._strip_codeblock(raw))
        except json.JSONDecodeError:
            pass

        text = await self._call(
            self.model,
            system + "\n\n" + JSON_EXTRACT_PROMPT,
            raw,
            json_mode=True,
        )
        return json.loads(self._strip_codeblock(text))

    @property
    def token_summary(self) -> str:
        total = self.total_input_tokens + self.total_output_tokens
        return (
            f"Token 用量: {total:,} "
            f"(输入 {self.total_input_tokens:,} + 输出 {self.total_output_tokens:,})"
        )

    # ------------------------------------------------------------------

    async def _call(
        self,
        model: str,
        system: str,
        user: str,
        *,
        json_mode: bool = False,
    ) -> str:
        kwargs: dict = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        response = await self.client.chat.completions.create(**kwargs)

        usage = getattr(response, "usage", None)
        if usage:
            self.total_input_tokens += getattr(usage, "prompt_tokens", 0)
            self.total_output_tokens += getattr(usage, "completion_tokens", 0)

        return response.choices[0].message.content or ""

    @staticmethod
    def _strip_codeblock(text: str) -> str:
        """移除 markdown 代码块标记。"""
        text = text.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            lines = [l for l in lines if not l.startswith("```")]
            text = "\n".join(lines)
        return text.strip()
