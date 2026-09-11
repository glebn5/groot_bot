import asyncio
import logging
import re
import time
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional, Tuple
from groq import AsyncGroq, RateLimitError as GroqRateLimitError, APIError as GroqAPIError
from openai import AsyncOpenAI, RateLimitError as OpenAIRateLimitError, APIError as OpenAIAPIError
import google.generativeai as genai

from app.config import settings

logger = logging.getLogger(__name__)


class AIRequestManager:
    """
    Centralized manager for all AI LLM requests.
    Features:
    - Primary integration with ProxyAPI (OpenAI-compatible gateway, Qwen 3.8 Flash, native tool calling, prompt caching)
    - Automated logging of KV-cache (Prompt Caching) hit-rates and token savings
    - Secondary/fallback to Groq (Llama / GPT-OSS) and Google Gemini
    - Model availability and capability routing (tool-calling vs text-only)
    - Rate limit parsing (TPD, TPM, RPM, OTPM) and automatic model cooldown
    - Zero-internal-retry clients with fast application-level fallback
    - Per-user request serialization (prevents competing requests from same user)
    - Concurrency throttle for external API protection
    """

    # Groq fallback tool models
    GROQ_TOOL_MODELS = [
        "openai/gpt-oss-120b",
        "openai/gpt-oss-20b",
        "llama-3.1-8b-instant"
    ]

    # Groq fallback text models
    GROQ_TEXT_MODELS = [
        "openai/gpt-oss-120b",
        "openai/gpt-oss-20b",
        "llama-3.1-8b-instant",
        "groq/compound",
        "groq/compound-mini"
    ]

    GEMINI_MODELS = [
        "gemini-3.6-flash",
        "gemini-flash-latest",
        "gemini-3.7-flash"
    ]

    def __init__(self):
        self.groq_client = AsyncGroq(api_key=settings.GROQ_API_KEY, max_retries=0)
        self.proxyapi_client: Optional[AsyncOpenAI] = None
        self._model_cooldowns: Dict[str, float] = {}  # model_name -> timestamp until which on cooldown
        self._user_locks: Dict[int, asyncio.Lock] = {}
        self._global_semaphore = asyncio.Semaphore(3)  # Max 3 concurrent LLM calls
        self._init_proxyapi()
        self._init_gemini()

    def _init_proxyapi(self):
        key = (getattr(settings, "PROXYAPI_KEY", "") or "").strip()
        if key and key != "your_proxyapi_key_here" and not key.startswith("your_"):
            try:
                base_url = getattr(settings, "PROXYAPI_BASE_URL", "https://api.proxyapi.ru/openai/v1")
                self.proxyapi_client = AsyncOpenAI(
                    api_key=key,
                    base_url=base_url,
                    max_retries=0
                )
                logger.info(f"[AIRequestManager] ProxyAPI client initialized for base_url={base_url}")
            except Exception as e:
                logger.warning(f"Failed to initialize ProxyAPI client: {e}")

    def _init_gemini(self):
        key = (settings.GEMINI_API_KEY or "").strip()
        if key and key != "your_gemini_api_key_here" and not key.startswith("your_"):
            try:
                genai.configure(api_key=key)
            except Exception as e:
                logger.warning(f"Failed to configure Gemini API: {e}")

    def is_proxyapi_available(self) -> bool:
        key = (getattr(settings, "PROXYAPI_KEY", "") or "").strip()
        return self.proxyapi_client is not None and bool(key and not key.startswith("your_"))

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
        """
        err_str = str(error)
        match = re.search(r"try again in (?:(\d+)m)?(?:([\d\.]+)s)?", err_str, re.IGNORECASE)
        if match:
            minutes = float(match.group(1) or 0)
            seconds = float(match.group(2) or 0)
            cooldown = minutes * 60 + seconds + 2.0
            return max(cooldown, 5.0)

        match_sec = re.search(r"in ([\d\.]+)s", err_str, re.IGNORECASE)
        if match_sec:
            return float(match_sec.group(1)) + 2.0

        if "tpd" in err_str.lower() or "tokens per day" in err_str.lower():
            return 600.0

        return 30.0

    def _log_cache_metrics(self, response: Any, provider_name: str = "ProxyAPI"):
        """
        Extracts and logs prompt caching metrics from API response usage.
        """
        try:
            usage = getattr(response, "usage", None)
            if not usage:
                return
            prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
            completion_tokens = getattr(usage, "completion_tokens", 0) or 0
            cached_tokens = 0

            # Check prompt_tokens_details (OpenAI/ProxyAPI format)
            details = getattr(usage, "prompt_tokens_details", None)
            if details:
                if isinstance(details, dict):
                    cached_tokens = details.get("cached_tokens", 0) or 0
                else:
                    cached_tokens = getattr(details, "cached_tokens", 0) or 0

            if prompt_tokens > 0:
                hit_rate = (cached_tokens / prompt_tokens) * 100
                logger.info(
                    f"[{provider_name} Cache] Prompt: {prompt_tokens} tokens "
                    f"(Cached: {cached_tokens}, Hit-rate: {hit_rate:.1f}%), "
                    f"Completion: {completion_tokens} tokens."
                )
        except Exception as e:
            logger.debug(f"Failed to log cache metrics: {e}")

    async def execute_tool_turn(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: float = 0.2,
        max_tokens: int = 1536
    ) -> Any:
        """
        Executes a turn with tool calling support.
        Priority 1: ProxyAPI (e.g. qwen/qwen3.8-flash)
        Priority 2: Groq tool models (Llama 3.1 / GPT-OSS)
        Priority 3: Gemini fallback
        """
        last_error = None

        async with self._global_semaphore:
            # 1. Try ProxyAPI if configured
            if self.is_proxyapi_available() and self.proxyapi_client is not None:
                p_model = getattr(settings, "PROXYAPI_MODEL", "qwen/qwen3.8-flash")
                cooling, rem = self.is_model_on_cooldown(p_model)
                if not cooling:
                    try:
                        logger.info(f"[AIRequestManager] Trying ProxyAPI model '{p_model}' with {len(tools or [])} tools...")
                        kwargs: Dict[str, Any] = {
                            "model": p_model,
                            "messages": messages,
                            "temperature": temperature,
                            "max_tokens": max_tokens
                        }
                        if tools:
                            kwargs["tools"] = tools
                            kwargs["tool_choice"] = "auto"

                        response = await self.proxyapi_client.chat.completions.create(**kwargs)
                        self._log_cache_metrics(response, provider_name="ProxyAPI")
                        return response, f"proxyapi/{p_model}"
                    except (OpenAIRateLimitError, GroqRateLimitError) as rle:
                        cooldown = self.parse_rate_limit_cooldown(rle)
                        self.set_model_cooldown(p_model, cooldown, reason=f"ProxyAPI 429: {rle}")
                        last_error = rle
                    except Exception as pe:
                        logger.warning(f"[AIRequestManager] ProxyAPI call failed ({pe}). Falling back to secondary providers...")
                        last_error = pe
                else:
                    logger.info(f"[AIRequestManager] Skipping ProxyAPI model '{p_model}' (cooling down for {rem:.0f}s).")

            # 2. Fallback to Groq Tool Models
            for model in self.GROQ_TOOL_MODELS:
                cooling, rem = self.is_model_on_cooldown(model)
                if cooling:
                    logger.info(f"[AIRequestManager] Skipping Groq '{model}' (cooling down for {rem:.0f}s).")
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
                except (GroqRateLimitError, OpenAIRateLimitError) as rle:
                    cooldown = self.parse_rate_limit_cooldown(rle)
                    self.set_model_cooldown(model, cooldown, reason=f"429 RateLimit: {rle}")
                    last_error = rle
                except (GroqAPIError, OpenAIAPIError) as apie:
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

        # 3. Fallback to Gemini
        if self.is_gemini_available():
            logger.info("[AIRequestManager] All primary tool models failed or on cooldown. Falling back to Gemini...")
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
        Priority 1: ProxyAPI
        Priority 2: Groq text models
        Priority 3: Gemini fallback
        """
        last_error = None

        async with self._global_semaphore:
            # 1. Try ProxyAPI
            if self.is_proxyapi_available() and self.proxyapi_client is not None:
                p_model = getattr(settings, "PROXYAPI_MODEL", "qwen/qwen3.8-flash")
                cooling, rem = self.is_model_on_cooldown(p_model)
                if not cooling:
                    try:
                        logger.info(f"[AIRequestManager] Trying ProxyAPI text model '{p_model}'...")
                        kwargs: Dict[str, Any] = {
                            "model": p_model,
                            "messages": messages,
                            "temperature": temperature,
                            "max_tokens": max_tokens
                        }
                        if response_format:
                            kwargs["response_format"] = response_format

                        response = await self.proxyapi_client.chat.completions.create(**kwargs)
                        self._log_cache_metrics(response, provider_name="ProxyAPI")
                        content = response.choices[0].message.content or ""
                        return content, f"proxyapi/{p_model}"
                    except Exception as pe:
                        logger.warning(f"[AIRequestManager] ProxyAPI text completion failed ({pe}). Falling back...")
                        last_error = pe

            # 2. Try Groq text models
            for model in self.GROQ_TEXT_MODELS:
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
                except (GroqRateLimitError, OpenAIRateLimitError) as rle:
                    cooldown = self.parse_rate_limit_cooldown(rle)
                    self.set_model_cooldown(model, cooldown, reason=f"429 RateLimit: {rle}")
                    last_error = rle
                except (GroqAPIError, OpenAIAPIError) as apie:
                    if apie.status_code == 404:
                        logger.error(f"[AIRequestManager] Text model '{model}' not found (404). Disabling for 24h.")
                        self.set_model_cooldown(model, 86400, reason="Model not found (404)")
                    else:
                        logger.warning(f"[AIRequestManager] Groq text model '{model}' API error {apie.status_code}: {apie}")
                    last_error = apie
                except Exception as e:
                    logger.warning(f"[AIRequestManager] Groq text model '{model}' failed: {e}")
                    last_error = e

        # 3. Fallback to Gemini
        if self.is_gemini_available():
            logger.info("[AIRequestManager] All text models failed. Falling back to Gemini...")
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
