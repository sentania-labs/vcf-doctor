"""Anthropic provider. Official `anthropic` SDK, streaming, adaptive thinking.

Failure modes degrade to ProviderUnavailable with a human-readable reason so
the rest of VCF Doctor keeps working. The API key is never logged.
"""

import logging
from collections.abc import AsyncIterator

import anthropic

from app.assistant.base import LLMProvider, ProviderUnavailable
from app.assistant.prompt import build_system_prompt, build_user_message
from app.models import AssistantRequest, AssistantStatus

log = logging.getLogger(__name__)

NO_KEY_REASON = (
    "No Anthropic API key configured. Set ANTHROPIC_API_KEY or enter a key on the Settings page."
)
REFUSAL_NOTICE = (
    "\n\n[The model declined to answer this request (stop reason: refusal). "
    "No answer was produced. Rephrase the question or narrow the evidence and try again.]"
)
MAX_TOKENS = 16000
# Server-side refusal fallback: on a policy decline the API re-runs the request
# on Anthropic's recommended fallback model inside the same call.
FALLBACK_BETA = "server-side-fallback-2026-07-01"


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(self, model: str, api_key: str | None) -> None:
        self.model = model
        self._api_key = api_key
        self.last_stop_reason: str | None = None

    def _client(self) -> anthropic.AsyncAnthropic:
        if not self._api_key:
            raise ProviderUnavailable(NO_KEY_REASON)
        return anthropic.AsyncAnthropic(api_key=self._api_key, max_retries=2, timeout=600)

    async def status(self) -> AssistantStatus:
        if not self._api_key:
            return AssistantStatus(
                available=False, provider=self.name, model=self.model, reason=NO_KEY_REASON
            )
        return AssistantStatus(available=True, provider=self.name, model=self.model)

    async def stream(self, request: AssistantRequest) -> AsyncIterator[str]:
        client = self._client()
        self.last_stop_reason = None
        try:
            kwargs = dict(
                model=self.model,
                max_tokens=MAX_TOKENS,
                system=build_system_prompt(request),
                messages=[{"role": "user", "content": build_user_message(request)}],
                thinking={"type": "adaptive"},
            )
            use_fallbacks = supports_fallbacks(self.model)
            try:
                async with client.beta.messages.stream(
                    **kwargs, **(fallback_kwargs() if use_fallbacks else {})
                ) as stream:
                    async for text in stream.text_stream:
                        yield text
                    final = await stream.get_final_message()
            except anthropic.BadRequestError as e:
                # A model we thought supported fallbacks said otherwise: retry once without.
                if not (use_fallbacks and "fallbacks" in _api_error_detail(e)):
                    raise
                log.warning("assistant: %s rejected fallbacks; retrying without", self.model)
                async with client.beta.messages.stream(**kwargs) as stream:
                    async for text in stream.text_stream:
                        yield text
                    final = await stream.get_final_message()
        except anthropic.AuthenticationError as e:
            raise ProviderUnavailable(
                "Anthropic rejected the API key. Check the key on the Settings page."
            ) from e
        except anthropic.RateLimitError as e:
            raise ProviderUnavailable("Anthropic rate limit reached. Try again shortly.") from e
        except anthropic.APIConnectionError as e:
            raise ProviderUnavailable(
                "Could not reach the Anthropic API. Check outbound network access."
            ) from e
        except anthropic.APIStatusError as e:
            detail = _api_error_detail(e)
            log.warning("assistant: Anthropic API error HTTP %s: %s", e.status_code, detail)
            raise ProviderUnavailable(
                f"Anthropic API error (HTTP {e.status_code}): {detail}"
            ) from e

        self.last_stop_reason = final.stop_reason or "end_turn"
        if final.stop_reason == "refusal":
            log.info("assistant: model refused request")
            yield REFUSAL_NOTICE
        elif final.stop_reason == "max_tokens":
            yield "\n\n[Answer truncated: the response reached the output token limit.]"

    async def ping(self) -> tuple[bool, str]:
        """Tiny live call used by POST /test. Never returns the key."""
        try:
            client = self._client()
            msg = await client.messages.create(
                model=self.model,
                max_tokens=16,
                messages=[{"role": "user", "content": "Reply with the single word OK."}],
            )
        except ProviderUnavailable as e:
            return False, str(e)
        except anthropic.AuthenticationError:
            return False, "Anthropic rejected the API key."
        except anthropic.NotFoundError:
            return False, f"Model '{self.model}' was not found. Check the model name."
        except anthropic.RateLimitError:
            return False, "Anthropic rate limit reached. Try again shortly."
        except anthropic.APIConnectionError:
            return False, "Could not reach the Anthropic API."
        except anthropic.APIStatusError as e:
            detail = _api_error_detail(e)
            log.warning("assistant: Anthropic API error HTTP %s: %s", e.status_code, detail)
            return False, f"Anthropic API error (HTTP {e.status_code}): {detail}"
        if msg.stop_reason == "refusal":
            return False, "The model refused the test prompt."
        return True, f"Connected. Model {msg.model} answered."


# Server-side refusal fallbacks exist on the Opus 5 and Fable 5 tier only;
# Sonnet 5 and older models reject the parameter with a 400.
FALLBACK_MODEL_PREFIXES = ("claude-opus-5", "claude-fable-5", "claude-mythos-5")


def supports_fallbacks(model: str) -> bool:
    return model.startswith(FALLBACK_MODEL_PREFIXES)


def fallback_kwargs() -> dict:
    return {"betas": [FALLBACK_BETA], "fallbacks": "default"}


def _api_error_detail(e: anthropic.APIStatusError) -> str:
    """The API's own reason, e.g. 'model: claude-opus-5 is not available' or
    'fallbacks: Extra inputs are not permitted'. Error bodies never carry the
    key, so this is safe to show to the operator and to log."""
    body = getattr(e, "body", None)
    if isinstance(body, dict):
        err = body.get("error") or {}
        if isinstance(err, dict) and err.get("message"):
            return f"{err.get('type', 'error')}: {err['message']}"[:300]
    return (getattr(e, "message", None) or str(e))[:300]
