from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from openai import APIError, APIStatusError, RateLimitError
from sqlalchemy.orm import Session

from app.agent import LLMNotConfiguredError, invoke_agent
from app.agent.schema import AgentTurn
from app.api.schemas import ChatIn, ChatOut
from app.db import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/agent", tags=["agent"])


def _log_turn(turn: AgentTurn) -> None:
    logger.info(
        "agent.chat associate=%s intent=%s clarify=%s tools=%s",
        turn.associate_email,
        turn.decision.intent.value,
        turn.decision.needs_clarification,
        len(turn.tool_results),
    )
    for result in turn.tool_results:
        logger.info(
            "agent.tool name=%s ok=%s error=%s",
            result.get("tool"),
            result.get("ok"),
            result.get("error"),
        )


def _chat_out(turn: AgentTurn, conversation_id: str) -> ChatOut:
    return ChatOut(
        reply=turn.final_message or turn.decision.assistant_message,
        conversation_id=conversation_id,
        associate_email=turn.associate_email,
        associate_name=turn.associate_name,
        intent=turn.decision.intent,
        needs_clarification=turn.decision.needs_clarification,
        clarification_question=turn.decision.clarification_question,
        entities=turn.decision.entities,
        tool_results=turn.tool_results,
    )


@router.post("/chat", response_model=ChatOut)
def post_chat(body: ChatIn, db: Session = Depends(get_db)) -> ChatOut:
    conversation_id = (body.conversation_id or "").strip() or str(uuid.uuid4())
    try:
        turn = invoke_agent(
            body.message.strip(),
            associate_email=str(body.associate_email).strip().lower(),
            associate_name=body.associate_name.strip(),
            session=db,
        )
    except LLMNotConfiguredError as exc:
        logger.warning("agent.chat llm_not_configured: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    except (RateLimitError, APIStatusError, APIError) as exc:
        logger.exception("agent.chat llm_provider_error")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"LLM provider error: {exc}",
        ) from exc
    except Exception as exc:
        # Catch-all for LangChain/provider wrappers that don't subclass openai errors.
        name = type(exc).__name__
        if "RateLimit" in name or "APIError" in name or "OpenAI" in name:
            logger.exception("agent.chat llm_provider_error")
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"LLM provider error: {exc}",
            ) from exc
        logger.exception("agent.chat unexpected_error")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Agent failed to process the message",
        ) from exc

    _log_turn(turn)
    return _chat_out(turn, conversation_id)
