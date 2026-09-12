"use client";

import { GitCommitHorizontal } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

export function CommitDialog({
  open,
  onOpenChange,
  onCommit,
  pending,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onCommit: (message: string) => void;
  pending: boolean;
}) {
  const [message, setMessage] = useState("");

  function submit(event: React.FormEvent) {
    event.preventDefault();
    onCommit(message.trim() || "Update");
    setMessage("");
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>Commit changes</DialogTitle>
          <DialogDescription>
            Describe what changed. The message, author and a line-by-line diff are stored with the
            revision.
          </DialogDescription>
        </DialogHeader>

        <form onSubmit={submit} className="space-y-4">
          <div className="space-y-1.5">
            <Label htmlFor="commit-message">Commit message</Label>
            <Input
              id="commit-message"
              autoFocus
              value={message}
              onChange={(event) => setMessage(event.target.value)}
              placeholder="Tighten the radiation requirement to 30 krad(Si)"
            />
          </div>

          <DialogFooter>
            <Button type="button" variant="ghost" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" loading={pending}>
              <GitCommitHorizontal />
              Commit
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
