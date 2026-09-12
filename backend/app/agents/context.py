"""Context window strategy.

Three layers, in the order they take effect:

1. Offloading  — deepagents' FilesystemMiddleware (installed by create_deep_agent)
                 lets the agent stage long drafts on the backend instead of in
                 the transcript; the summarizer also parks pre-summary history there.
2. Summarization — a SummarizationMiddleware instance that *replaces* the built-in
                 one. Since deepagents 0.7, middleware is overridden by matching
                 `.name` rather than duplicated.
3. Trimming    — TrimMessagesMiddleware below, a hard backstop before every call.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from deepagents.backends import BackendProtocol
from deepagents.middleware import SummarizationMiddleware
from langchain.agents.middleware import AgentMiddleware, ModelRequest
from langchain_core.messages import AnyMessage, ToolMessage, trim_messages
from langchain_core.messages.utils import count_tokens_approximately

from app.agents.prompts import SUMMARY_PROMPT
from app.config import settings


def build_summarization(model, backend: BackendProtocol) -> SummarizationMiddleware:
    """Replace the default summarizer with one that protects evidence.

    The trigger is expressed in tokens rather than the more idiomatic
    ("fraction", 0.85): a fraction resolves against the model profile's
    max_input_tokens, which Azure deployments do not reliably expose. The model
    is tagged "summary" so the runtime can keep its tokens out of the answer.
    """
    return SummarizationMiddleware(
        model=model,
        backend=backend,
        trigger=("tokens", settings.agent_summary_trigger_tokens),
        keep=("messages", settings.agent_summary_keep_messages),
        summary_prompt=SUMMARY_PROMPT,
    )


def _drop_orphan_tool_messages(messages: list[AnyMessage]) -> list[AnyMessage]:
    """Azure rejects a tool result whose originating tool_call is gone."""
    known: set[str] = set()
    for message in messages:
        for call in getattr(message, "tool_calls", None) or []:
            call_id = call.get("id") if isinstance(call, dict) else getattr(call, "id", None)
            if call_id:
                known.add(call_id)

    return [
        m
        for m in messages
        if not (isinstance(m, ToolMessage) and m.tool_call_id not in known)
    ]


class TrimMessagesMiddleware(AgentMiddleware):
    """Bound what is sent to Azure, without touching persisted history.

    This hooks `wrap_model_call`, not `before_model`, on purpose. Node-style hooks
    merge their return value into state through the graph reducers, so returning a
    shortened list from `before_model` would *append* and grow the transcript.
    Overriding the request bounds the outbound payload only.

    The system prompt cannot be dropped here: in LangChain 1.x it travels on
    ModelRequest.system_message, outside `.messages`. `include_system=True` is
    passed anyway, so the behaviour holds if that ever changes.
    """

    def __init__(self, max_tokens: int | None = None) -> None:
        super().__init__()
        self.max_tokens = max_tokens or settings.agent_trim_tokens

    def _trim(self, request: ModelRequest) -> ModelRequest:
        messages = list(request.messages)
        if count_tokens_approximately(messages) <= self.max_tokens:
            return request

        trimmed = trim_messages(
            messages,
            max_tokens=self.max_tokens,
            token_counter=count_tokens_approximately,
            strategy="last",
            start_on="human",
            include_system=True,
            allow_partial=False,
        )
        return request.override(messages=_drop_orphan_tool_messages(trimmed))

    def wrap_model_call(self, request: ModelRequest, handler: Callable):
        return handler(self._trim(request))

    async def awrap_model_call(
        self, request: ModelRequest, handler: Callable[[ModelRequest], Awaitable]
    ):
        return await handler(self._trim(request))
