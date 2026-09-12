"use client";

import { useParams } from "next/navigation";

import { ChatWorkspace } from "@/components/chat/chat-workspace";

export default function ChatSessionPage() {
  const { sessionId } = useParams<{ sessionId: string }>();
  return <ChatWorkspace sessionId={sessionId} />;
}
