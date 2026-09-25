"use client";

import { useSearchParams } from "next/navigation";

import { ChatWorkspace } from "@/components/chat/chat-workspace";

export default function ChatPage() {
  // `/chat?project=<id>` scopes the conversation, and anything it writes, to
  // that project. Plain `/chat` is the global surface.
  const projectId = useSearchParams().get("project");
  return <ChatWorkspace projectId={projectId ?? undefined} />;
}
