from __future__ import annotations

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph

from app.agent.llm import get_chat_model
from app.agent.prompts import build_system_prompt
from app.agent.schema import AgentDecision, AgentTurn, Intent
from app.agent.state import AgentState
from app.config import get_settings

_DEFAULT_CLARIFY = (
    "What start time should I use for that booking? "
    "For example: 2 PM or 14:00."
)


def apply_clarification_guard(decision: AgentDecision) -> AgentDecision:
    """Force clarification when book/extend lacks a start time."""
    if decision.intent not in {Intent.book, Intent.extend}:
        return decision
    if decision.entities.start_time:
        return decision

    question = decision.clarification_question or _DEFAULT_CLARIFY
    message = decision.assistant_message
    if not decision.needs_clarification:
        message = (
            "I need a start time before I can continue. " + question
            if not message
            else message
        )
    return decision.model_copy(
        update={
            "needs_clarification": True,
            "clarification_question": question,
            "assistant_message": message or question,
        }
    )


def _extract_node(state: AgentState, *, model: BaseChatModel) -> dict:
    structured = model.with_structured_output(AgentDecision)
    system = build_system_prompt(odc_timezone=state["odc_timezone"])
    user_bits = [
        f"Associate: {state['associate_name']} <{state['associate_email']}>",
        f"Message: {state['messages'][-1].content}",
    ]
    decision: AgentDecision = structured.invoke(
        [
            SystemMessage(content=system),
            HumanMessage(content="\n".join(user_bits)),
        ]
    )
    decision = apply_clarification_guard(decision)
    return {"decision": decision}


def build_agent_graph(*, model: BaseChatModel | None = None):
    chat_model = model or get_chat_model()

    def extract(state: AgentState) -> dict:
        return _extract_node(state, model=chat_model)

    graph = StateGraph(AgentState)
    graph.add_node("extract", extract)
    graph.add_edge(START, "extract")
    graph.add_edge("extract", END)
    return graph.compile()


def invoke_agent(
    message: str,
    *,
    associate_email: str,
    associate_name: str,
    odc_timezone: str | None = None,
    model: BaseChatModel | None = None,
) -> AgentTurn:
    """Run one agent turn: intent + entity extraction (no DB writes)."""
    timezone = odc_timezone or get_settings().odc_timezone
    graph = build_agent_graph(model=model)
    result = graph.invoke(
        {
            "messages": [HumanMessage(content=message)],
            "associate_email": associate_email,
            "associate_name": associate_name,
            "odc_timezone": timezone,
        }
    )
    decision = result["decision"]
    assert isinstance(decision, AgentDecision)
    return AgentTurn(
        decision=decision,
        associate_email=associate_email,
        associate_name=associate_name,
        user_message=message,
    )
