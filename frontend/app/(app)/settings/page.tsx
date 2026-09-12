"use client";

import { Check, Plus, Trash2 } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { PageHeader } from "@/components/layout/page-header";
import { ThemeToggle } from "@/components/layout/theme-toggle";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Separator } from "@/components/ui/separator";
import { Textarea } from "@/components/ui/textarea";
import { API_URL, api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useWorkspace } from "@/lib/workspace";

function Section({
  title,
  description,
  children,
}: {
  title: string;
  description: string;
  children: React.ReactNode;
}) {
  return (
    <section className="grid gap-6 py-7 md:grid-cols-[232px_1fr]">
      <div>
        <h2 className="text-[0.8125rem] font-semibold">{title}</h2>
        <p className="mt-1 text-2xs leading-relaxed text-muted-foreground">{description}</p>
      </div>
      <div className="min-w-0">{children}</div>
    </section>
  );
}

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-4 border-b border-border py-2.5 last:border-b-0">
      <span className="text-[0.8125rem] text-muted-foreground">{label}</span>
      <span className="truncate text-[0.8125rem] font-medium">{value}</span>
    </div>
  );
}

function NewTemplateDialog({ onCreated }: { onCreated: () => Promise<void> }) {
  const [open, setOpen] = useState(false);
  const [pending, setPending] = useState(false);
  const [form, setForm] = useState({ name: "", standard: "", description: "", sections: "" });

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    const sections = form.sections
      .split("\n")
      .map((line) => line.trim())
      .filter(Boolean)
      .map((line, index) => {
        const [titlePart, guidance = ""] = line.split("|");
        const match = /^(\d+(?:\.\d+)*)\s+(.*)$/.exec(titlePart.trim());
        return {
          number: match ? match[1] : String(index + 1),
          title: (match ? match[2] : titlePart).trim(),
          guidance: guidance.trim(),
        };
      });

    if (sections.length === 0) {
      toast.error("Add at least one section");
      return;
    }

    setPending(true);
    try {
      await api.createTemplate({ ...form, sections });
      await onCreated();
      setOpen(false);
      setForm({ name: "", standard: "", description: "", sections: "" });
      toast.success("Template added");
    } catch (error) {
      toast.error("Could not add the template", {
        description: error instanceof Error ? error.message : undefined,
      });
    } finally {
      setPending(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button variant="outline" size="sm">
          <Plus />
          Add template
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Add a template</DialogTitle>
          <DialogDescription>
            One section per line, as <code>1.2 Section title | guidance for the writer</code>. The
            guidance is what the agent uses as its retrieval query for that section.
          </DialogDescription>
        </DialogHeader>

        <form onSubmit={submit} className="space-y-4">
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <Label htmlFor="template-name">Name</Label>
              <Input
                id="template-name"
                required
                value={form.name}
                onChange={(event) => setForm({ ...form, name: event.target.value })}
                placeholder="Verification Control Document"
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="template-standard">Standard</Label>
              <Input
                id="template-standard"
                value={form.standard}
                onChange={(event) => setForm({ ...form, standard: event.target.value })}
                placeholder="ECSS-E-ST-10-02C"
              />
            </div>
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="template-description">Description</Label>
            <Input
              id="template-description"
              value={form.description}
              onChange={(event) => setForm({ ...form, description: event.target.value })}
              placeholder="What this deliverable is for"
            />
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="template-sections">Sections</Label>
            <Textarea
              id="template-sections"
              rows={7}
              required
              value={form.sections}
              onChange={(event) => setForm({ ...form, sections: event.target.value })}
              placeholder={"1 Scope | What this document governs\n2 Verification matrix | Method per requirement"}
              className="font-mono text-2xs"
            />
          </div>

          <DialogFooter>
            <Button type="button" variant="ghost" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button type="submit" loading={pending}>
              Add template
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

export default function SettingsPage() {
  const { user, health } = useAuth();
  const { templates, refreshTemplates } = useWorkspace();

  async function removeTemplate(id: string) {
    try {
      await api.deleteTemplate(id);
      await refreshTemplates();
    } catch (error) {
      toast.error("Could not remove that template", {
        description: error instanceof Error ? error.message : undefined,
      });
    }
  }

  return (
    <>
      <PageHeader title="Settings" description="Account, appearance and templates" />

      <div className="min-h-0 flex-1 overflow-y-auto px-6 py-2">
        <div className="mx-auto max-w-3xl divide-y divide-border">
          <Section title="Account" description="Your identity on shared documents and commits.">
            <div className="rounded-lg border border-border bg-surface px-4 py-1">
              <Row label="Name" value={user?.full_name || "—"} />
              <Row label="Email" value={user?.email} />
              <Row label="Organisation" value={user?.organisation || "—"} />
              <Row
                label="Member since"
                value={user ? new Date(user.created_at).toLocaleDateString() : "—"}
              />
            </div>
          </Section>

          <Section
            title="Appearance"
            description="Light, dark, or follow the operating system."
          >
            <div className="flex items-center justify-between rounded-lg border border-border bg-surface px-4 py-3">
              <div>
                <p className="text-[0.8125rem] font-medium">Theme</p>
                <p className="text-2xs text-muted-foreground">
                  Applies immediately and is remembered on this device.
                </p>
              </div>
              <ThemeToggle />
            </div>
          </Section>

          <Section
            title="Templates"
            description="The skeletons the document-generation skill fills in. The six built-in ones cover the common ECSS deliverables."
          >
            <div className="space-y-3">
              <div className="flex justify-end">
                <NewTemplateDialog onCreated={refreshTemplates} />
              </div>

              <div className="overflow-hidden rounded-lg border border-border bg-surface">
                {templates.map((template) => (
                  <div
                    key={template.id}
                    className="group flex items-start justify-between gap-3 border-b border-border px-4 py-3 last:border-b-0"
                  >
                    <div className="min-w-0">
                      <p className="flex items-center gap-2 text-[0.8125rem] font-medium">
                        <span className="truncate">{template.name}</span>
                        {template.is_builtin ? (
                          <Badge variant="default">built-in</Badge>
                        ) : (
                          <Badge variant="brand">custom</Badge>
                        )}
                      </p>
                      {template.standard ? (
                        <p className="mt-0.5 text-2xs text-muted-foreground">
                          {template.standard}
                        </p>
                      ) : null}
                      <p className="mt-1 text-2xs leading-relaxed text-muted-foreground">
                        {template.sections.length} sections · {template.description}
                      </p>
                    </div>
                    {!template.is_builtin ? (
                      <Button
                        variant="ghost"
                        size="icon-sm"
                        className="shrink-0 opacity-0 transition-opacity group-hover:opacity-100"
                        onClick={() => void removeTemplate(template.id)}
                        aria-label={`Remove ${template.name}`}
                      >
                        <Trash2 />
                      </Button>
                    ) : null}
                  </div>
                ))}
              </div>
            </div>
          </Section>

          <Section
            title="Workspace"
            description="Which backend this frontend is talking to, and how the agent is configured."
          >
            <div className="rounded-lg border border-border bg-surface px-4 py-1">
              <Row label="API endpoint" value={<code className="font-mono text-2xs">{API_URL}</code>} />
              <Row
                label="Backend"
                value={
                  health?.mock ? (
                    <Badge variant="warning">mock mode</Badge>
                  ) : (
                    <Badge variant="success">
                      <Check />
                      live
                    </Badge>
                  )
                }
              />
              <Row
                label="Model provider"
                value={
                  health?.mock
                    ? "scripted — no inference"
                    : health?.azure_configured
                      ? "Azure OpenAI"
                      : "not configured"
                }
              />
              <Row label="Retrieval" value="one page = one chunk" />
            </div>

            {health?.mock ? (
              <p className="mt-3 text-2xs leading-relaxed text-muted-foreground">
                The mock backend serves the same HTTP contract with in-memory state and a scripted
                agent. Parsing, retrieval and page citations are real; the prose and the verdicts are
                not. Run <code className="font-mono">docker compose up --build</code> for the live
                stack.
              </p>
            ) : null}
          </Section>

          <Separator className="opacity-0" />
        </div>
      </div>
    </>
  );
}
