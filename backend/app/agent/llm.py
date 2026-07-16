from __future__ import annotations

from langchain_openai import AzureChatOpenAI, ChatOpenAI

from app.config import get_settings


class LLMNotConfiguredError(RuntimeError):
    """Raised when neither OpenAI nor Azure OpenAI credentials are set."""


def get_chat_model(*, temperature: float = 0.0):
    """Return a chat model: OpenAI if keyed, else Azure OpenAI."""
    settings = get_settings()

    if settings.openai_api_key:
        return ChatOpenAI(
            model="gpt-4o-mini",
            api_key=settings.openai_api_key,
            temperature=temperature,
        )

    if (
        settings.azure_openai_api_key
        and settings.azure_openai_endpoint
        and settings.azure_openai_deployment
    ):
        return AzureChatOpenAI(
            api_key=settings.azure_openai_api_key,
            azure_endpoint=settings.azure_openai_endpoint,
            azure_deployment=settings.azure_openai_deployment,
            api_version=settings.azure_openai_api_version,
            temperature=temperature,
        )

    raise LLMNotConfiguredError(
        "Set OPENAI_API_KEY or Azure OpenAI settings "
        "(AZURE_OPENAI_API_KEY, AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_DEPLOYMENT)."
    )
