from __future__ import annotations

from langchain_openai import AzureChatOpenAI, ChatOpenAI

from app.config import get_settings

GROQ_OPENAI_BASE_URL = "https://api.groq.com/openai/v1"


class LLMNotConfiguredError(RuntimeError):
    """Raised when no supported LLM credentials are set."""


def get_chat_model(*, temperature: float = 0.0):
    """Return a chat model: Groq, OpenAI, or Azure OpenAI."""
    settings = get_settings()

    groq_key = (settings.groq_api_key or "").strip()
    if groq_key:
        return ChatOpenAI(
            model=(settings.groq_model or "llama-3.3-70b-versatile").strip(),
            api_key=groq_key,
            base_url=GROQ_OPENAI_BASE_URL,
            temperature=temperature,
        )

    openai_key = (settings.openai_api_key or "").strip()
    if openai_key:
        return ChatOpenAI(
            model="gpt-4o-mini",
            api_key=openai_key,
            temperature=temperature,
        )

    azure_key = (settings.azure_openai_api_key or "").strip()
    if (
        azure_key
        and settings.azure_openai_endpoint
        and settings.azure_openai_deployment
    ):
        return AzureChatOpenAI(
            api_key=azure_key,
            azure_endpoint=settings.azure_openai_endpoint,
            azure_deployment=settings.azure_openai_deployment,
            api_version=settings.azure_openai_api_version,
            temperature=temperature,
        )

    raise LLMNotConfiguredError(
        "Set GROQ_API_KEY, OPENAI_API_KEY, or Azure OpenAI settings "
        "(AZURE_OPENAI_API_KEY, AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_DEPLOYMENT)."
    )
