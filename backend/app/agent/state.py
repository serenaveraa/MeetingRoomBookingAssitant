from __future__ import annotations

from typing import Annotated, NotRequired, TypedDict

from langgraph.graph.message import add_messages

from app.agent.schema import AgentDecision


class AgentState(TypedDict):
    messages: Annotated[list, add_messages]
    associate_email: str
    associate_name: str
    odc_timezone: str
    decision: NotRequired[AgentDecision | None]
