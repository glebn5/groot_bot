import asyncio
import logging
import re
import time
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional, Tuple
from groq import AsyncGroq, RateLimitError, APIError
import google.generativeai as genai

from app.config import settings

logger = logging.getLogger(__name__)


class AIRequestManager:
    """
    Centralized manager for all AI LLM requests.
    Features:
    - Model availability and capability routing (tool-calling vs text-only)
    - Elimination of nonexistent models (prevents 404s)
    - Rate limit parsing (TPD, TPM, RPM, OTPM) and automatic model cooldown
    - Zero-internal-retry Groq client with controlled application-level fallback
    - Per-user request serialization (prevents competing requests from same user)
    - Concurrency throttle for external API protection
    - Seamless fallback to Gemini when Groq is exhausted
    """

    TOOL_MODELS = [
        "openai/gpt-oss-120b",
        "openai/gpt-oss-20b",
        "qwen/qwen3.8-27b",
        "qwen/qwen3.6-27b"
    ]

    TEXT_MODELS = [
        "openai/gpt-oss-120b",
        "openai/gpt-oss-20b",
        "qwen/qwen3.8-27b",
        "qwen/qwen3.6-27b",
        "groq/compound",
        "groq/compound-mini"
    ]

    GEMINI_MODELS = [
        "gemini-3.6-flash",
        "gemini-flash-latest",
        "gemini-3.7-flash"
    ]

    def __init__(self):
        # max_retries=0 ensures the SDK does not stall inside with 20-35s sleeps
        self.groq_client = AsyncGroq(api_key=settings.GROQ_API_KEY, max_retries=0)
        self._model_cooldowns: Dict[str, float] = {}  # model_name -> timestamp until which on cooldown
        self._user_locks: Dict[int, asyncio.Lock] = {}
        self._global_semaphore = asyncio.Semaphore(3)  # Max 3 concurrent LLM calls
        self._init_gemini()

    def _init_gemini(self):
        key = (settings.GEMINI_API_KEY or "").strip()
        if key and key != "your_gemini_api_key_here" and not key.startswith("your_"):
            try:
                genai.configure(api_key=key)
            except Exception as e:
                logger.warning(f"Failed to configure Gemini API: {e}")

    def is_gemini_available(self) -> bool:
        key = (settings.GEMINI_API_KEY or "").strip()
        return bool(key and key != "your_gemini_api_key_here" and not key.startswith("your_"))

    def get_user_lock(self, user_id: int) -> asyncio.Lock:
        if user_id not in self._user_locks:
            self._user_locks[user_id] = asyncio.Lock()
        return self._user_locks[user_id]

    def is_model_on_cooldown(self, model: str) -> Tuple[bool, float]:
        """
        Returns (is_cooling_down, remaining_seconds).
        """
        now = time.time()
        until = self._model_cooldowns.get(model, 0)
        if until > now:
            return True, until - now
        return False, 0.0

    def set_model_cooldown(self, model: str, duration_seconds: float, reason: str = ""):
        """
        Places a model in cooldown for duration_seconds.
        """
        until = time.time() + duration_seconds
        self._model_cooldowns[model] = until
        logger.warning(
            f"[AIRequestManager] Model '{model}' put on cooldown for {duration_seconds:.1f}s ({reason}). "
            f"Active until {datetime.fromtimestamp(until).strftime('%H:%M:%S')}."
        )

    def parse_rate_limit_cooldown(self, error: Exception) -> float:
        """
        Parses RateLimitError or generic error message to find how long to cooldown.
        Examples:
          'Please try again in 11m2.688s' -> 663s
          'Please try again in 4.5s' -> 5s
          'Retry-After' header -> seconds
        """
        err_str = str(error)
        
        # Check 'Please try again in ...' pattern
        match = re.search(r"try again in (?:(\d+)m)?(?:([\d\.]+)s)?", err_str, re.IGNORECASE)
        if match:
            minutes = float(match.group(1) or 0)
            seconds = float(match.group(2) or 0)
            total = minutes * 60 + seconds
            if total > 0:
                return total + 2  # Buffer 2 seconds

        # Check for explicit numbers of seconds
        sec_match = re.search(r"retry after (\d+) seconds", err_str, re.IGNORECASE)
        if sec_match:
            return float(sec_match.group(1)) + 2

        # If it's a TPD (Tokens Per Day) limit, default to at least 10 minutes
        if "tokens per day" in err_str.lower() or "tpd" in err_str.lower():
            return 600.0

        # Default short cooldown for generic 429
        return 30.0

    async def execute_tool_turn(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: float = 0.2,
        max_tokens: int = 1536
    ) -> Any:
        """
        Executes a turn with tool calling support using prioritized tool models.
        Skips models currently on cooldown.
        Falls back to Gemini if all Groq tool models fail or are on cooldown.
        """
        last_error = None

        async with self._global_semaphore:
            for model in self.TOOL_MODELS:
                cooling, rem = self.is_model_on_cooldown(model)
                if cooling:
                    logger.info(f"[AIRequestManager] Skipping '{model}' (cooling down for {rem:.0f}s).")
                    continue

                try:
                    logger.info(f"[AIRequestManager] Trying Groq tool model '{model}' with {len(tools or [])} tools...")
                    kwargs: Dict[str, Any] = {
                        "model": model,
                        "messages": messages,
                        "temperature": temperature,
                        "max_tokens": max_tokens
                    }
                    if tools:
                        kwargs["tools"] = tools
                        kwargs["tool_choice"] = "auto"

                    response = await self.groq_client.chat.completions.create(**kwargs)
                    return response, model
                except RateLimitError as rle:
                    cooldown = self.parse_rate_limit_cooldown(rle)
                    self.set_model_cooldown(model, cooldown, reason=f"429 RateLimit: {rle}")
                    last_error = rle
                except APIError as apie:
                    # If model returned 400 (e.g. tool calling unsupported) or 404
                    if apie.status_code == 400 and "tool calling" in str(apie).lower():
                        logger.error(f"[AIRequestManager] Model '{model}' does not support tool calling: {apie}")
                        self.set_model_cooldown(model, 3600, reason="Tool calling unsupported")
                    elif apie.status_code == 404:
                        logger.error(f"[AIRequestManager] Model '{model}' not found (404). Disabling for 24h.")
                        self.set_model_cooldown(model, 86400, reason="Model not found (404)")
                    else:
                        logger.warning(f"[AIRequestManager] Groq model '{model}' API error {apie.status_code}: {apie}")
                    last_error = apie
                except Exception as e:
                    logger.warning(f"[AIRequestManager] Groq model '{model}' failed: {e}")
                    last_error = e

        # If all Groq models failed, check Gemini
        if self.is_gemini_available():
            logger.info("[AIRequestManager] All Groq tool models failed or on cooldown. Falling back to Gemini...")
            try:
                gemini_resp = await self._execute_gemini_prompt(messages)
                return gemini_resp, "gemini-fallback"
            except Exception as ge:
                logger.error(f"[AIRequestManager] Gemini fallback failed: {ge}", exc_info=True)
                last_error = ge

        if last_error:
            raise last_error
        raise RuntimeError("Все доступные нейросетевые модели временно перегружены или находятся в периоде охлаждения.")

    async def execute_text_completion(
        self,
        messages: List[Dict[str, Any]],
        temperature: float = 0.2,
        response_format: Optional[Dict[str, str]] = None,
        max_tokens: int = 1536
    ) -> Tuple[str, str]:
        """
        Executes a pure text/json completion without tool calling.
        Can utilize broader TEXT_MODELS (including compound models).
        Returns (content, model_used).
        """
        last_error = None

        async with self._global_semaphore:
            for model in self.TEXT_MODELS:
                cooling, rem = self.is_model_on_cooldown(model)
                if cooling:
                    logger.info(f"[AIRequestManager] Skipping text model '{model}' (cooling down for {rem:.0f}s).")
                    continue

                try:
                    logger.info(f"[AIRequestManager] Trying Groq text model '{model}'...")
                    kwargs: Dict[str, Any] = {
                        "model": model,
                        "messages": messages,
                        "temperature": temperature,
                        "max_tokens": max_tokens
                    }
                    if response_format:
                        kwargs["response_format"] = response_format

                    response = await self.groq_client.chat.completions.create(**kwargs)
                    content = response.choices[0].message.content or ""
                    return content, model
                except RateLimitError as rle:
                    cooldown = self.parse_rate_limit_cooldown(rle)
                    self.set_model_cooldown(model, cooldown, reason=f"429 RateLimit: {rle}")
                    last_error = rle
                except APIError as apie:
                    if apie.status_code == 404:
                        logger.error(f"[AIRequestManager] Text model '{model}' not found (404). Disabling for 24h.")
                        self.set_model_cooldown(model, 86400, reason="Model not found (404)")
                    else:
                        logger.warning(f"[AIRequestManager] Groq text model '{model}' API error {apie.status_code}: {apie}")
                    last_error = apie
                except Exception as e:
                    logger.warning(f"[AIRequestManager] Groq text model '{model}' failed: {e}")
                    last_error = e

        # Fallback to Gemini
        if self.is_gemini_available():
            logger.info("[AIRequestManager] All Groq text models failed. Falling back to Gemini...")
            for gm in self.GEMINI_MODELS:
                try:
                    prompt = "\n\n".join([f"{m.get('role', 'user')}: {m.get('content', '')}" for m in messages])
                    model = genai.GenerativeModel(gm)
                    res = await model.generate_content_async(prompt)
                    return (res.text or ""), gm
                except Exception as ge:
                    logger.warning(f"[AIRequestManager] Gemini model '{gm}' failed: {ge}")
                    last_error = ge

        if last_error:
            raise last_error
        raise RuntimeError("Все текстовые модели временно недоступны.")

    async def _execute_gemini_prompt(self, messages: List[Dict[str, Any]]) -> str:
        """
        Helper for Gemini prompt formatting.
        """
        prompt_parts = []
        for m in messages:
            role = m.get("role", "user")
            content = m.get("content", "")
            if role == "system":
                prompt_parts.append(f"Инструкции системы:\n{content}")
            elif role == "user":
                prompt_parts.append(f"Пользователь: {content}")
            elif role == "assistant":
                prompt_parts.append(f"Ассистент: {content}")
            elif role == "tool":
                prompt_parts.append(f"Результат инструмента ({m.get('name', 'tool')}): {content}")

        full_prompt = "\n\n".join(prompt_parts)
        for gm in self.GEMINI_MODELS:
            try:
                model = genai.GenerativeModel(gm)
                res = await model.generate_content_async(full_prompt)
                return res.text or ""
            except Exception as ge:
                logger.warning(f"[AIRequestManager] Gemini model '{gm}' failed: {ge}")
        raise RuntimeError("Все модели Gemini завершились с ошибкой.")


ai_request_manager = AIRequestManager()
