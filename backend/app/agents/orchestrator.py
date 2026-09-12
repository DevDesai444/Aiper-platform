"""The supervisor. Plans, reads its skill, delegates to a specialist, assembles.

Skills are SKILL.md files under `/skills/<name>/` (deepagents' progressive
disclosure: only name + description enter the system prompt; the agent reads the
full file when it decides the skill applies). Everything else the agent writes
lives in graph state and disappears with the turn.
"""

from __future__ import annotations

from typing import Any

from deepagents import create_deep_agent
from deepagents.backends import CompositeBackend, FilesystemBackend, StateBackend
from langchain.agents.middleware import TodoListMiddleware
from langchain_openai import AzureChatOpenAI

from app.agents import prompts
from app.agents.context import TrimMessagesMiddleware, build_summarization
from app.agents.skills import SkillContext, build_tools
from app.config import settings

SKILLS = ["/skills/"]

# The supervisor plans; the specialists write. Temperature differs by job.
_SUPERVISOR_TEMPERATURE = 0.1
_WRITER_TEMPERATURE = 0.25
_ANALYST_TEMPERATURE = 0.0


def chat_model(temperature: float = _SUPERVISOR_TEMPERATURE, *, tags: list[str] | None = None) -> AzureChatOpenAI:
    return AzureChatOpenAI(
        azure_deployment=settings.azure_openai_chat_deployment_name,
        azure_endpoint=settings.azure_openai_endpoint,
        api_key=settings.azure_openai_api_key,
        api_version=settings.azure_openai_api_version,
        temperature=temperature,
        streaming=True,
        tags=tags,
    )


def build_backend() -> CompositeBackend:
    """Ephemeral state by default; `/skills/` served read-through from the image."""
    return CompositeBackend(
        default=StateBackend(),
        routes={"/skills/": FilesystemBackend(root_dir=settings.skills_root, virtual_mode=True)},
    )


def _subagents(tools: dict[str, Any], mode: str) -> list[dict[str, Any]]:
    """Declarative SubAgent specs. Each runs isolated, with its own context window.

    Filesystem tools are inherited from the default stack. The specialists also
    get the skills so they can read the house style before writing.
    """
    research = {
        "name": "research",
        "description": (
            "Gather and quote evidence from the indexed pages. Use for any question "
            "of the form 'what do the sources say about X'."
        ),
        "system_prompt": prompts.RESEARCH_SUBAGENT,
        "tools": [tools["search_pages"], tools["load_target_document"], tools["list_attachments"]],
    }
    writer = {
        "name": "document_writer",
        "description": (
            "Draft one section of a compliant space-sector deliverable from supplied "
            "evidence. Pass the excerpts verbatim in the task description."
        ),
        "system_prompt": prompts.DOCUMENT_WRITER_SUBAGENT,
        "tools": [tools["get_template"], tools["search_pages"]],
        "model": chat_model(_WRITER_TEMPERATURE),
        "skills": SKILLS,
    }
    analyst = {
        "name": "comparison_analyst",
        "description": (
            "Assign compliance verdicts to a batch of 10-15 target items against the "
            "evidence supplied. Returns matrix rows."
        ),
        "system_prompt": prompts.COMPARISON_ANALYST_SUBAGENT,
        "tools": [tools["find_evidence_in_sources"]],
        "model": chat_model(_ANALYST_TEMPERATURE),
        "skills": SKILLS,
    }
    return [research, analyst if mode == "feature_comparison" else writer]


def build_agent(ctx: SkillContext):
    """Assemble the graph for one turn."""
    model = chat_model()
    backend = build_backend()
    tools = build_tools(ctx)

    if ctx.mode == "feature_comparison":
        exposed = ["comparison_scope", "load_target_document", "find_evidence_in_sources",
                   "list_attachments", "build_compliance_matrix"]
    else:
        exposed = ["list_templates", "get_template", "search_pages", "list_attachments"]

    return create_deep_agent(
        model=model,
        tools=[tools[name] for name in exposed],
        system_prompt=prompts.supervisor_prompt(ctx.mode),
        subagents=_subagents(tools, ctx.mode),
        skills=SKILLS,
        backend=backend,
        middleware=[
            # Planning became opt-in in deepagents 0.7; the activity feed needs it.
            TodoListMiddleware(),
            # Same `.name` as the built-in, so it replaces rather than duplicates.
            build_summarization(chat_model(tags=["summary"]), backend),
            TrimMessagesMiddleware(),
        ],
    )
