from typing import Any, Dict, List, Optional, Callable
from pydantic import BaseModel, Field


class ToolExecutionContext(BaseModel):
    """
    Trusted application context passed to every tool execution.
    The LLM never controls or overrides these values.
    """
    user_id: int
    chat_id: int


class ToolResult(BaseModel):
    """
    Standard structured return object for all agent tools.
    """
    success: bool
    entity_type: Optional[str] = None  # 'task', 'reminder', 'calendar', 'note', 'goal', 'recurring', 'schedule', 'search'
    entity_id: Optional[Any] = None
    data: Optional[Any] = None
    error_code: Optional[str] = None
    message: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return self.model_dump(exclude_none=True)


class AgentResponse(BaseModel):
    """
    Final output of an agent interaction turn.
    """
    reply_text: str
    tool_calls_count: int = 0
    executed_tools: List[Dict[str, Any]] = Field(default_factory=list)
    requires_confirmation: bool = False
    pending_action: Optional[Dict[str, Any]] = None
