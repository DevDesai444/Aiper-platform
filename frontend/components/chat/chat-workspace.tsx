"use client";

import { FolderKanban } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";

import { Composer } from "@/components/chat/composer";
import { EmptyState } from "@/components/chat/empty-state";
import { AssistantMessage, UserMessage } from "@/components/chat/message";
import { PageHeader } from "@/components/layout/page-header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { api, streamChat } from "@/lib/api";
import type { AgentEvent, ChatMessage, Mode } from "@/lib/types";
import { useWorkspace } from "@/lib/workspace";

interface InFlight {
  question: string;
  answer: string;
  events: AgentEvent[];
}

/**
 * Chat is a global surface that can also be scoped to a project.
 *
 * `projectId` comes from `/chat?project=<id>` — opened from inside a project,
 * the conversation is created against it and anything the agent writes lands
 * there. Without it, drafts go to the caller's own Workspace project, which the
 * backend creates on first use.
 */
export function ChatWorkspace({
  sessionId,
  projectId,
}: {
  sessionId?: string;
  projectId?: string;
}) {
  const router = useRouter();
  const { templates, projects, refreshSessions } = useWorkspace();

  const [mode, setMode] = useState<Mode>("document_generation");
  const [templateKey, setTemplateKey] = useState<string | null>(null);

  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [title, setTitle] = useState("New conversation");
  const [loading, setLoading] = useState(Boolean(sessionId));
  const [inFlight, setInFlight] = useState<InFlight | null>(null);

  /**
   * The turn accumulates into a ref and is published as a fresh object on every
   * event. Keeping the accumulation out of the state updater keeps the updater
   * pure, which matters under StrictMode's double invocation.
   */
  const turnRef = useRef<InFlight | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  /* Default the template to the first available one. */
  useEffect(() => {
    if (!templateKey && templates.length > 0) setTemplateKey(templates[0].key);
  }, [templates, templateKey]);

  /* Load an existing conversation, including its persisted activity traces. */
  useEffect(() => {
    if (!sessionId) {
      setMessages([]);
      setTitle("New conversation");
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    api
      .getSession(sessionId)
      .then((session) => {
        if (cancelled) return;
        setMessages(session.messages);
        setTitle(session.title);
        setMode(session.mode);
      })
      .catch(() => {
        // A stale link (for example after the mock restarted) starts a new
        // conversation instead of stranding the user on an error.
        if (!cancelled) router.replace("/chat");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [sessionId, router]);

  /* Keep the newest content in view while a turn streams. */
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: inFlight ? "smooth" : "auto", block: "end" });
  }, [messages, inFlight]);

  const send = useCallback(
    async (message: string) => {
      const controller = new AbortController();
      abortRef.current = controller;

      turnRef.current = { question: message, answer: "", events: [] };
      const publish = () => setInFlight(turnRef.current ? { ...turnRef.current } : null);
      publish();

      // Mutable across the stream callback, so no closure-narrowing games.
      const outcome: { sessionId: string | null } = { sessionId: sessionId ?? null };

      try {
        await streamChat(
          {
            message,
            mode,
            session_id: sessionId ?? null,
            project_id: projectId ?? null,
            template_key: mode === "document_generation" ? templateKey : null,
          },
          (raw) => {
            const event = raw as AgentEvent;
            const turn = turnRef.current;
            if (!turn) return;

            if (event.type === "session") {
              outcome.sessionId = event.session_id;
              setTitle(event.title);
              return;
            }
            if (event.type === "token") {
              turn.answer += event.value;
            } else if (event.type === "final") {
              // The persisted answer is authoritative over the streamed tokens.
              turn.answer = event.content || turn.answer;
              publish();
              return;
            } else {
              turn.events = [...turn.events, event];
            }
            publish();
          },
          controller.signal,
        );
      } catch (error) {
        if ((error as Error).name !== "AbortError") {
          toast.error("The agent run failed", {
            description: error instanceof Error ? error.message : undefined,
          });
        }
      } finally {
        abortRef.current = null;
      }

      /* Commit the turn locally, then let the sidebar catch up. */
      const turn = turnRef.current;
      const stamp = new Date().toISOString();
      setMessages((existing) => [
        ...existing,
        {
          id: `local-user-${stamp}`,
          role: "user",
          content: message,
          mode,
          activity: [],
          created_at: stamp,
        },
        {
          id: `local-assistant-${stamp}`,
          role: "assistant",
          content: turn?.answer ?? "",
          mode,
          activity: turn?.events ?? [],
          created_at: stamp,
        },
      ]);
      turnRef.current = null;
      setInFlight(null);

      await refreshSessions();
      if (!sessionId && outcome.sessionId) router.replace(`/chat/${outcome.sessionId}`);
    },
    [mode, projectId, refreshSessions, router, sessionId, templateKey],
  );

  const activeTemplate = useMemo(
    () => templates.find((template) => template.key === templateKey),
    [templates, templateKey],
  );

  const project = useMemo(
    () => projects.find((candidate) => candidate.id === projectId),
    [projects, projectId],
  );

  const running = inFlight !== null;
  const isEmpty = !loading && messages.length === 0 && !running;

  return (
    <>
      <PageHeader
        title={sessionId ? title : "Chat"}
        description={
          mode === "feature_comparison"
            ? "N source documents checked against one target"
            : activeTemplate
              ? `${activeTemplate.name}${activeTemplate.standard ? ` · ${activeTemplate.standard}` : ""}`
              : "Compliant deliverables from your own sources"
        }
        actions={
          <>
            {project ? (
              <Button asChild variant="subtle" size="sm" title={`Scoped to ${project.name}`}>
                <Link href={`/projects/${project.id}`}>
                  <FolderKanban />
                  <span className="max-w-[160px] truncate">{project.name}</span>
                </Link>
              </Button>
            ) : null}
            <Badge variant={mode === "feature_comparison" ? "warning" : "brand"}>
              {mode === "feature_comparison" ? "Comparison" : "Generation"}
            </Badge>
            {sessionId ? (
              <Button variant="outline" size="sm" onClick={() => router.push("/chat")}>
                New
              </Button>
            ) : null}
          </>
        }
      />

      <div className="min-h-0 flex-1 overflow-y-auto">
        {loading ? (
          <div className="mx-auto w-full max-w-3xl space-y-6 px-6 py-8">
            <Skeleton className="ml-auto h-14 w-2/3" />
            <Skeleton className="h-9 w-full" />
            <Skeleton className="h-40 w-full" />
          </div>
        ) : isEmpty ? (
          <EmptyState mode={mode} />
        ) : (
          <div className="mx-auto w-full max-w-3xl space-y-8 px-6 py-8">
            {messages.map((message) =>
              message.role === "user" ? (
                <UserMessage key={message.id} message={message} />
              ) : (
                <AssistantMessage
                  key={message.id}
                  content={message.content}
                  events={message.activity}
                  running={false}
                  projectId={projectId}
                />
              ),
            )}

            {inFlight ? (
              <>
                <UserMessage
                  message={{
                    id: "in-flight-user",
                    role: "user",
                    content: inFlight.question,
                    mode,
                    activity: [],
                    created_at: new Date().toISOString(),
                  }}
                />
                <AssistantMessage
                  content={inFlight.answer}
                  events={inFlight.events}
                  running
                  projectId={projectId}
                />
              </>
            ) : null}

            <div ref={bottomRef} />
          </div>
        )}
      </div>

      <Composer
        mode={mode}
        onModeChange={setMode}
        templates={templates}
        templateKey={templateKey}
        onTemplateChange={setTemplateKey}
        onSubmit={(message) => void send(message)}
        onStop={() => abortRef.current?.abort()}
        running={running}
      />
    </>
  );
}
