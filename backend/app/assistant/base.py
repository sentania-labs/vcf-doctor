"""LLM provider interface. Implementations: anthropic, mock (Agent E)."""

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator

from app.models import AssistantRequest, AssistantStatus


class LLMProvider(ABC):
    name: str

    @abstractmethod
    async def status(self) -> AssistantStatus: ...

    @abstractmethod
    def stream(self, request: AssistantRequest) -> AsyncIterator[str]:
        """Yield text deltas. Raise ProviderUnavailable for config/network faults."""


class ProviderUnavailable(Exception):
    pass
