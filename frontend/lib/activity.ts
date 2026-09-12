import type { ActivityRow, AgentEvent } from "@/lib/types";

const PLAN_ROW_ID = "plan";

/**
 * Fold the raw event stream into feed rows.
 *
 * `tool_start` / `tool_end` are one row, not two. Every plan update collapses
 * into a single checklist row. A tool call carrying `parent` was made by a
 * sub-agent, so it nests under that delegation row — the feed reads as a tree
 * of who did what, not a flat log.
 */
export function toRows(events: AgentEvent[]): ActivityRow[] {
  const rows: ActivityRow[] = [];
  const byId = new Map<string, ActivityRow>();

  const place = (row: ActivityRow, parent?: string) => {
    byId.set(row.id, row);
    const host = parent ? byId.get(parent) : undefined;
    if (host) (host.children ??= []).push(row);
    else rows.push(row);
  };

  for (const event of events) {
    switch (event.type) {
      case "plan": {
        const existing = byId.get(PLAN_ROW_ID);
        const running = event.items.some((item) => item.status !== "completed");
        if (existing) {
          existing.items = event.items;
          existing.running = running;
        } else {
          place({ id: PLAN_ROW_ID, kind: "plan", label: "Plan", items: event.items, running });
        }
        break;
      }
      case "skill":
        place({ id: event.id, kind: "skill", label: `Loaded skill`, agent: event.skill, running: false });
        break;
      case "tool_start":
        place(
          {
            id: event.id,
            kind: "tool",
            label: event.label || event.tool,
            detail: event.detail,
            running: true,
          },
          event.parent,
        );
        break;
      case "delegation":
        place({
          id: event.id,
          kind: "delegation",
          label: "Delegating to",
          agent: event.agent,
          detail: event.task,
          running: true,
        });
        break;
      case "tool_end":
      case "delegation_end": {
        const row = byId.get(event.id);
        if (row) {
          row.output = event.output;
          row.running = false;
        }
        break;
      }
      case "error":
        place({ id: `error-${rows.length}`, kind: "error", label: event.message, running: false });
        break;
      default:
        break;
    }
  }

  return rows;
}
