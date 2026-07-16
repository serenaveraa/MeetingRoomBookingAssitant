from __future__ import annotations

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from sqlalchemy.orm import Session

from app.agent.llm import get_chat_model
from app.agent.prompts import build_system_prompt
from app.agent.schema import AgentDecision, AgentTurn, Intent
from app.agent.state import AgentState
from app.agent.tools import ToolContext, ToolResult, compose_reply, run_tools_for_intent
from app.config import get_settings

_DEFAULT_CLARIFY_BOOK = (
    "What start time should I use for that booking? "
    "For example: 2 PM or 14:00."
)
_DEFAULT_CLARIFY_EXTEND = (
    "How many minutes should I extend your meeting? For example: 15 or 30."
)


def apply_clarification_guard(decision: AgentDecision) -> AgentDecision:
    """Force clarification when book lacks start_time or extend lacks duration."""
    if decision.intent == Intent.book and not decision.entities.start_time:
        question = decision.clarification_question or _DEFAULT_CLARIFY_BOOK
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

    if decision.intent == Intent.extend:
        minutes = decision.entities.duration_minutes
        if minutes is None or minutes <= 0:
            question = decision.clarification_question or _DEFAULT_CLARIFY_EXTEND
            message = decision.assistant_message
            if not decision.needs_clarification:
                message = (
                    "I need a duration before I can extend. " + question
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

    return decision


def _extract_node(state: AgentState, *, model: BaseChatModel) -> dict:
    # function_calling works across OpenAI and Groq; json_schema is not
    # supported on all Groq models.
    structured = model.with_structured_output(
        AgentDecision, method="function_calling"
    )
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


def build_agent_graph(
    *,
    model: BaseChatModel | None = None,
    session: Session | None = None,
):
    chat_model = model or get_chat_model()

    def extract(state: AgentState) -> dict:
        return _extract_node(state, model=chat_model)

    def should_run_tools(state: AgentState) -> str:
        decision = state.get("decision")
        if decision is None:
            return "compose"
        if decision.needs_clarification or decision.intent == Intent.other:
            return "compose"
        if session is None:
            return "compose"
        return "run_tools"

    def run_tools(state: AgentState) -> dict:
        decision = state["decision"]
        assert isinstance(decision, AgentDecision)
        assert session is not None
        ctx = ToolContext(
            session=session,
            associate_email=state["associate_email"],
            associate_name=state["associate_name"],
        )
        results = run_tools_for_intent(decision, ctx)
        return {"tool_results": [r.model_dump() for r in results]}

    def compose(state: AgentState) -> dict:
        decision = state.get("decision")
        assert isinstance(decision, AgentDecision)
        raw_results = state.get("tool_results") or []
        results = [ToolResult.model_validate(r) for r in raw_results]
        return {"final_message": compose_reply(decision, results)}

    graph = StateGraph(AgentState)
    graph.add_node("extract", extract)
    graph.add_node("run_tools", run_tools)
    graph.add_node("compose", compose)
    graph.add_edge(START, "extract")
    graph.add_conditional_edges(
        "extract",
        should_run_tools,
        {"run_tools": "run_tools", "compose": "compose"},
    )
    graph.add_edge("run_tools", "compose")
    graph.add_edge("compose", END)
    return graph.compile()


def invoke_agent(
    message: str,
    *,
    associate_email: str,
    associate_name: str,
    session: Session | None = None,
    odc_timezone: str | None = None,
    model: BaseChatModel | None = None,
) -> AgentTurn:
    """Run one agent turn: extract intent, run tools when possible, compose reply."""
    timezone = odc_timezone or get_settings().odc_timezone
    graph = build_agent_graph(model=model, session=session)
    result = graph.invoke(
        {
            "messages": [HumanMessage(content=message)],
            "associate_email": associate_email,
            "associate_name": associate_name,
            "odc_timezone": timezone,
            "tool_results": [],
            "final_message": "",
        }
    )
    decision = result["decision"]
    assert isinstance(decision, AgentDecision)
    tool_results = result.get("tool_results") or []
    final_message = result.get("final_message") or decision.assistant_message
    return AgentTurn(
        decision=decision,
        associate_email=associate_email,
        associate_name=associate_name,
        user_message=message,
        tool_results=tool_results,
        final_message=final_message,
    )
