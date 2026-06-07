"""
ContextManager — assembles everything that goes into a Claude API call.

Responsibilities:
- Fetch and hold conversation history (recent turns from SQLite)
- Fetch and hold task/category context (pending tasks, categories)
- Fetch relevant memories via vector search (always, upfront — not deferred)
- Build the final `messages` list for Claude
- Provide a human-readable debug dump of what's in context

Usage:
    ctx = await ContextManager.build(current_message="what was that server URL?")
    print(ctx.debug())
    messages = ctx.to_messages()
"""

import logging
from datetime import datetime, timezone
from dataclasses import dataclass, field

import brain
import history as history_module
import tasks as tasks_module

logger = logging.getLogger(__name__)


@dataclass
class ContextManager:
    # The current user message being processed
    current_message: str

    # Recent conversation turns: [{"role": "user"/"assistant", "content": "..."}]
    # Oldest first, does NOT include current_message yet
    history_turns: list[dict] = field(default_factory=list)

    # Task/brain context for the system prompt
    categories: list[dict] = field(default_factory=list)
    pending_tasks: list[dict] = field(default_factory=list)

    # Memories injected for question-answering (populated on demand)
    relevant_memories: list[dict] = field(default_factory=list)

    # Tunable limits
    history_limit: int = 10
    memory_limit: int = 5

    # -------------------------------------------------------------------------
    # Factory
    # -------------------------------------------------------------------------

    @classmethod
    async def build(
        cls,
        current_message: str,
        user_id: str,
        history_limit: int = 10,
        memory_limit: int = 8,
        history_after: str | None = None,
    ) -> "ContextManager":
        """Build a full context snapshot for one request.

        user_id:       Scopes all data (history, tasks, memories) to this user.
        history_after: ISO timestamp — skip messages before this time (cleared chat).
        """
        today = datetime.now(timezone.utc).date().isoformat()

        turns = history_module.get_recent_turns(
            limit=history_limit, after=history_after, user_id=user_id
        )

        # Strip the current message if it was already saved before build() was called
        if turns and turns[-1]["role"] == "user" and turns[-1]["content"] == current_message:
            turns = turns[:-1]

        categories    = brain.get_categories(user_id)
        pending_tasks = tasks_module.get_pending_tasks(today, user_id)
        relevant_memories = brain.search_memories(current_message, user_id, limit=memory_limit)

        ctx = cls(
            current_message=current_message,
            history_turns=turns,
            categories=categories,
            pending_tasks=pending_tasks,
            relevant_memories=relevant_memories,
            history_limit=history_limit,
            memory_limit=memory_limit,
        )
        logger.debug("ContextManager built: %s", ctx.summary())
        return ctx

    # -------------------------------------------------------------------------
    # Memory injection (manual override if caller has pre-fetched memories)
    # -------------------------------------------------------------------------

    def inject_memories(self, memories: list[dict]) -> None:
        """Replace memories with a pre-fetched list."""
        self.relevant_memories = memories

    # -------------------------------------------------------------------------
    # Message builders
    # -------------------------------------------------------------------------

    def to_messages(
        self,
        augment_with_memories: bool = False,
        history_window: int | None = None,
        user_turns_only: bool = False,
    ) -> list[dict]:
        """
        Return the messages list for a Claude API call.

        history_window: only include the last N history turns.
        user_turns_only: strip assistant turns — useful for intent classification
            so prior assistant responses don't anchor the intent.
        augment_with_memories: embed relevant memories into the user message.
        """
        turns = self.history_turns

        if user_turns_only:
            turns = [t for t in turns if t["role"] == "user"]

        if history_window is not None:
            turns = turns[-history_window:] if len(turns) > history_window else turns

        user_content = self.current_message

        if augment_with_memories and self.relevant_memories:
            context_block = "\n\n".join(
                f"[{m['category']}] {m['content']}" for m in self.relevant_memories
            )
            user_content = f"Saved notes:\n{context_block}\n\nQuestion: {self.current_message}"
        elif augment_with_memories and not self.relevant_memories:
            user_content = f"Saved notes:\n(none)\n\nQuestion: {self.current_message}"

        return turns + [{"role": "user", "content": user_content}]

    # -------------------------------------------------------------------------
    # Introspection
    # -------------------------------------------------------------------------

    def summary(self) -> str:
        """One-line summary of what's in context."""
        return (
            f"history={len(self.history_turns)} turns, "
            f"categories={len(self.categories)}, "
            f"pending_tasks={len(self.pending_tasks)}, "
            f"memories={len(self.relevant_memories)}"
        )

    def debug(self) -> str:
        """
        Human-readable breakdown of everything going into context.
        Useful for logging or a debug endpoint.
        """
        lines = ["=" * 60, "CONTEXT MANAGER DEBUG", "=" * 60]

        lines.append(f"\n[Current Message]\n  {self.current_message!r}")

        lines.append(f"\n[History Turns] ({len(self.history_turns)} / limit={self.history_limit})")
        if self.history_turns:
            for i, t in enumerate(self.history_turns):
                role = t["role"].upper()
                snippet = t["content"][:120].replace("\n", " ")
                ellipsis = "..." if len(t["content"]) > 120 else ""
                lines.append(f"  [{i+1}] {role}: {snippet}{ellipsis}")
        else:
            lines.append("  (none)")

        lines.append(f"\n[Categories] ({len(self.categories)})")
        if self.categories:
            for c in self.categories:
                lines.append(f"  - {c['name']} ({c['count']} items)")
        else:
            lines.append("  (none)")

        lines.append(f"\n[Pending Tasks Today] ({len(self.pending_tasks)})")
        if self.pending_tasks:
            for t in self.pending_tasks:
                lines.append(f"  - [{t['id'][:8]}...] {t['content']}")
        else:
            lines.append("  (none)")

        lines.append(f"\n[Relevant Memories] ({len(self.relevant_memories)} / limit={self.memory_limit})")
        if self.relevant_memories:
            for m in self.relevant_memories:
                score = f" score={m['score']:.3f}" if m.get("score") is not None else ""
                snippet = m["content"][:120].replace("\n", " ")
                lines.append(f"  [{m['category']}]{score} {snippet}")
        else:
            lines.append("  (none — not yet fetched or not needed)")

        lines.append("\n[Final messages[] sent to Claude]")
        msgs = self.to_messages()
        for i, m in enumerate(msgs):
            snippet = m["content"][:100].replace("\n", " ")
            ellipsis = "..." if len(m["content"]) > 100 else ""
            lines.append(f"  [{i+1}] {m['role'].upper()}: {snippet}{ellipsis}")

        lines.append("=" * 60)
        return "\n".join(lines)
