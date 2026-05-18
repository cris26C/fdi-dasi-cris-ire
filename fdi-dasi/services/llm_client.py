from typing import Optional
from loguru import logger
from ollama import AsyncClient
from core.config import config
import asyncio
import random


class LLMClient:

    def __init__(self, agent_name: str):
        self._name = agent_name
        self._client: Optional[AsyncClient] = None

    def _get(self) -> AsyncClient:
        if self._client is None:
            self._client = AsyncClient(host=config.OLLAMA_HOST)
        return self._client

    def _reset(self):
        self._client = None

    async def call(self, messages: list, tools: list, max_retries: int = 3):
        kwargs = {"model": config.LLM_MODEL, "messages": messages}
        if tools:
            kwargs["tools"] = tools
        last_err = None
        for attempt in range(1, max_retries + 1):
            try:
                return await asyncio.wait_for(self._get().chat(**kwargs), timeout=120.0)
            except asyncio.TimeoutError:
                last_err = "timeout"
                self._reset()
            except (ConnectionError, ConnectionRefusedError, ConnectionResetError) as e:
                last_err = f"{type(e).__name__}: {e}"
                self._reset()
            except Exception as e:
                last_err = f"{type(e).__name__}: {e}"
                if "RemoteProtocol" in type(e).__name__ or "disconnected" in str(e).lower():
                    self._reset()
            delay = 2 ** attempt + random.uniform(0, 2)
            logger.warning(f"[{self._name}] Ollama error ({attempt}/{max_retries}) {last_err}; retry {delay:.1f}s")
            if attempt < max_retries:
                await asyncio.sleep(delay)
        return None
