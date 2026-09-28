"use client";

import { ChevronRight, Cpu, Plus, Trash2 } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { api } from "@/lib/api";
import type {
  NodeInterface,
  NodeParameter,
  NodeDocumentLink,
  ProductNode,
} from "@/lib/types";

interface Props {
  projectId: string;
  canEdit: boolean;
}

type NodeKind = "assembly" | "subassembly" | "component" | "part";

const KIND_LABELS: Record<NodeKind, string> = {
  assembly: "Assembly",
  subassembly: "Subassembly",
  component: "Component",
  part: "Part",
};

// Build a display tree from a flat node list
function buildTree(nodes: ProductNode[]): Map<string | null, ProductNode[]> {
  const byParent = new Map<string | null, ProductNode[]>();
  for (const n of nodes) {
    const key = n.parent_node_id ?? null;
    if (!byParent.has(key)) byParent.set(key, []);
    byParent.get(key)!.push(n);
  }
  return byParent;
}

function NodeRow({
  node,
  depth,
  selected,
  onSelect,
  byParent,
}: {
  node: ProductNode;
  depth: number;
  selected: string | null;
  onSelect: (id: string) => void;
  byParent: Map<string | null, ProductNode[]>;
}) {
  const [expanded, setExpanded] = useState(depth === 0);
  const children = byParent.get(node.id) ?? [];

  return (
    <div>
      <button
        className={[
          "flex w-full items-center gap-1.5 rounded px-2 py-1.5 text-left text-[0.8125rem] transition-colors hover:bg-accent",
          selected === node.id ? "bg-accent font-medium" : "",
        ].join(" ")}
        style={{ paddingLeft: `${8 + depth * 16}px` }}
        onClick={() => onSelect(node.id)}
      >
        {children.length > 0 ? (
          <ChevronRight
            className={[
              "size-3 shrink-0 text-muted-foreground transition-transform",
              expanded ? "rotate-90" : "",
            ].join(" ")}
            onClick={(e) => {
              e.stopPropagation();
              setExpanded((v) => !v);
            }}
          />
        ) : (
          <span className="size-3 shrink-0" />
        )}
        <Cpu className="size-3 shrink-0 text-muted-foreground" />
        <span className="truncate">{node.name}</span>
        <span className="ml-auto shrink-0 text-[0.75rem] text-muted-foreground">
          {KIND_LABELS[node.kind as NodeKind] ?? node.kind}
        </span>
      </button>
      {expanded &&
        children.map((child) => (
          <NodeRow
            key={child.id}
            node={child}
            depth={depth + 1}
            selected={selected}
            onSelect={onSelect}
            byParent={byParent}
          />
        ))}
    </div>
  );
}

function ParameterValue({ param }: { param: NodeParameter }) {
  if (param.value_kind === "text") return <span>{param.value_text}</span>;
  const parts = [param.value_num?.toString() ?? ""];
  if (param.unit) parts.push(param.unit);
  if (param.tolerance_num != null) parts.push(`±${param.tolerance_num}`);
  return <span>{parts.join(" ")}</span>;
}

export function ProductTreeTab({ projectId, canEdit }: Props) {
  const [nodes, setNodes] = useState<ProductNode[] | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [interfaces, setInterfaces] = useState<NodeInterface[]>([]);
  const [parameters, setParameters] = useState<NodeParameter[]>([]);
  const [docLinks, setDocLinks] = useState<NodeDocumentLink[]>([]);
  const [loadingDetail, setLoadingDetail] = useState(false);

  const loadTree = useCallback(async () => {
    try {
      const data = await api.listNodes(projectId);
      setNodes(data.nodes);
    } catch (error) {
      toast.error("Could not load the product tree", {
        description: error instanceof Error ? error.message : undefined,
      });
    }
  }, [projectId]);

  useEffect(() => {
    void loadTree();
  }, [loadTree]);

  const loadDetail = useCallback(
    async (nodeId: string) => {
      setLoadingDetail(true);
      try {
        const [ifaces, params, links] = await Promise.all([
          api.listInterfaces(projectId, nodeId),
          api.listParameters(projectId, nodeId),
          api.listNodeDocuments(projectId, nodeId),
        ]);
        setInterfaces(ifaces);
        setParameters(params);
        setDocLinks(links);
      } catch (error) {
        toast.error("Could not load node details", {
          description: error instanceof Error ? error.message : undefined,
        });
      } finally {
        setLoadingDetail(false);
      }
    },
    [projectId],
  );

  function handleSelect(id: string) {
    setSelectedId(id);
    void loadDetail(id);
  }

  async function handleAddNode() {
    const name = window.prompt("Node name");
    if (!name) return;
    try {
      await api.createNode(projectId, { kind: "component", name, parent_node_id: selectedId });
      await loadTree();
    } catch (error) {
      toast.error("Could not create node", {
        description: error instanceof Error ? error.message : undefined,
      });
    }
  }

  async function handleDeleteNode() {
    if (!selectedId) return;
    const node = nodes?.find((n) => n.id === selectedId);
    if (!node) return;
    if (!window.confirm(`Delete "${node.name}" and all its children?`)) return;
    try {
      await api.deleteNode(projectId, selectedId);
      setSelectedId(null);
      setInterfaces([]);
      setParameters([]);
      setDocLinks([]);
      await loadTree();
    } catch (error) {
      toast.error("Could not delete node", {
        description: error instanceof Error ? error.message : undefined,
      });
    }
  }

  if (nodes === null) {
    return (
      <div className="space-y-2 px-6 py-6">
        <Skeleton className="h-5 w-48" />
        <Skeleton className="h-5 w-40" />
        <Skeleton className="h-5 w-52" />
      </div>
    );
  }

  const byParent = buildTree(nodes);
  const roots = byParent.get(null) ?? [];
  const selectedNode = selectedId ? nodes.find((n) => n.id === selectedId) : null;

  return (
    <div className="flex min-h-0 flex-1 overflow-hidden">
      {/* Left pane: tree */}
      <div className="flex w-64 shrink-0 flex-col border-r border-border">
        <div className="flex items-center justify-between border-b border-border px-3 py-2">
          <span className="text-[0.75rem] font-medium uppercase tracking-wide text-muted-foreground">
            Nodes
          </span>
          {canEdit && (
            <Button
              size="sm"
              variant="ghost"
              className="h-6 px-1.5 text-xs"
              onClick={() => void handleAddNode()}
            >
              <Plus className="size-3" />
              Add
            </Button>
          )}
        </div>
        <div className="flex-1 overflow-y-auto py-1">
          {roots.length === 0 ? (
            <p className="px-4 py-4 text-[0.8125rem] text-muted-foreground">
              No nodes yet.{canEdit ? " Click Add to create one." : ""}
            </p>
          ) : (
            roots.map((root) => (
              <NodeRow
                key={root.id}
                node={root}
                depth={0}
                selected={selectedId}
                onSelect={handleSelect}
                byParent={byParent}
              />
            ))
          )}
        </div>
      </div>

      {/* Right pane: detail */}
      <div className="flex-1 overflow-y-auto px-6 py-4">
        {selectedNode == null ? (
          <p className="text-[0.8125rem] text-muted-foreground">
            Select a node to see its details.
          </p>
        ) : (
          <div className="space-y-6">
            <div className="flex items-start justify-between gap-4">
              <div>
                <h2 className="text-base font-semibold">{selectedNode.name}</h2>
                <p className="mt-0.5 text-[0.8125rem] text-muted-foreground">
                  {KIND_LABELS[selectedNode.kind as NodeKind] ?? selectedNode.kind}
                  {selectedNode.part_number ? ` · ${selectedNode.part_number}` : ""}
                  {selectedNode.supplier ? ` · ${selectedNode.supplier}` : ""}
                </p>
                {selectedNode.description ? (
                  <p className="mt-1 text-[0.8125rem] text-muted-foreground">
                    {selectedNode.description}
                  </p>
                ) : null}
              </div>
              {canEdit && (
                <Button
                  size="sm"
                  variant="outline"
                  className="shrink-0"
                  onClick={() => void handleDeleteNode()}
                >
                  <Trash2 className="size-3.5" />
                  Delete
                </Button>
              )}
            </div>

            {loadingDetail ? (
              <div className="space-y-2">
                <Skeleton className="h-4 w-40" />
                <Skeleton className="h-4 w-56" />
              </div>
            ) : (
              <>
                {/* Interfaces */}
                <section>
                  <h3 className="mb-2 text-[0.75rem] font-medium uppercase tracking-wide text-muted-foreground">
                    Interfaces ({interfaces.length})
                  </h3>
                  {interfaces.length === 0 ? (
                    <p className="text-[0.8125rem] text-muted-foreground">None.</p>
                  ) : (
                    <div className="space-y-1">
                      {interfaces.map((iface) => (
                        <div
                          key={iface.id}
                          className="flex items-center justify-between rounded-md bg-surface px-3 py-2 text-[0.8125rem]"
                        >
                          <span className="font-medium">{iface.name}</span>
                          <span className="text-muted-foreground">{iface.kind}</span>
                        </div>
                      ))}
                    </div>
                  )}
                </section>

                {/* Parameters */}
                <section>
                  <h3 className="mb-2 text-[0.75rem] font-medium uppercase tracking-wide text-muted-foreground">
                    Parameters ({parameters.length})
                  </h3>
                  {parameters.length === 0 ? (
                    <p className="text-[0.8125rem] text-muted-foreground">None.</p>
                  ) : (
                    <div className="divide-y divide-border rounded-md border border-border">
                      {parameters.map((param) => (
                        <div
                          key={param.id}
                          className="flex items-center justify-between px-3 py-2 text-[0.8125rem]"
                        >
                          <span className="font-medium">{param.raw_name}</span>
                          <span className="text-muted-foreground">
                            <ParameterValue param={param} />
                            {param.role !== "bidirectional" ? (
                              <span className="ml-1.5 rounded bg-accent px-1 py-0.5 text-[0.7rem]">
                                {param.role}
                              </span>
                            ) : null}
                          </span>
                        </div>
                      ))}
                    </div>
                  )}
                </section>

                {/* Linked documents */}
                <section>
                  <h3 className="mb-2 text-[0.75rem] font-medium uppercase tracking-wide text-muted-foreground">
                    Linked documents ({docLinks.length})
                  </h3>
                  {docLinks.length === 0 ? (
                    <p className="text-[0.8125rem] text-muted-foreground">None.</p>
                  ) : (
                    <div className="space-y-1">
                      {docLinks.map((link) => (
                        <div
                          key={link.id}
                          className="flex items-center justify-between rounded-md bg-surface px-3 py-2 text-[0.8125rem]"
                        >
                          <span className="font-mono text-[0.75rem] text-muted-foreground">
                            {link.document_id.slice(0, 8)}…
                          </span>
                          <span className="text-muted-foreground">{link.relation}</span>
                        </div>
                      ))}
                    </div>
                  )}
                </section>
              </>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
