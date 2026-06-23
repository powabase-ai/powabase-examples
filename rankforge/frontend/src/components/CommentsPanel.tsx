"use client";

import { useState } from "react";
import { Check, Loader2, Send, Trash2, Undo2 } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  useAddComment,
  useComments,
  useRemoveComment,
  useUpdateComment,
} from "@/lib/hooks/useComments";
import { useAuth } from "@/lib/auth/AuthProvider";
import { cn } from "@/lib/utils";

export function CommentsPanel({ articleId }: { articleId: string }) {
  const { data: comments, isLoading } = useComments(articleId);
  const add = useAddComment(articleId);
  const update = useUpdateComment(articleId);
  const remove = useRemoveComment(articleId);
  const { profile } = useAuth();
  const [draft, setDraft] = useState("");
  const canModerate = profile?.role === "editor" || profile?.role === "admin";

  function submit() {
    const body = draft.trim();
    if (!body) return;
    add.mutate(
      { body },
      {
        onSuccess: () => setDraft(""),
        onError: (e) =>
          toast.error(e instanceof Error ? e.message : "Failed to comment"),
      }
    );
  }

  return (
    <div className="flex h-full flex-col">
      <div className="min-h-0 flex-1 space-y-3 overflow-y-auto">
        {isLoading && (
          <p className="text-sm text-muted-foreground">Loading…</p>
        )}
        {comments?.length === 0 && (
          <p className="text-sm text-muted-foreground">
            No comments yet. Leave a note for reviewers.
          </p>
        )}
        {comments?.map((c) => {
          const mine = c.author_id === profile?.id;
          return (
            <div
              key={c.id}
              className={cn(
                "rounded-lg border border-border p-3",
                c.resolved && "opacity-60"
              )}
            >
              <div className="mb-1 flex items-center justify-between gap-2">
                <span className="truncate text-xs font-medium">
                  {c.author_email ?? "Someone"}
                </span>
                <span className="shrink-0 text-[10px] text-muted-foreground">
                  {new Date(c.created_at).toLocaleDateString()}
                </span>
              </div>
              <p
                className={cn(
                  "whitespace-pre-wrap text-sm",
                  c.resolved && "line-through"
                )}
              >
                {c.body}
              </p>
              <div className="mt-2 flex items-center gap-3">
                <button
                  onClick={() =>
                    update.mutate({ id: c.id, data: { resolved: !c.resolved } })
                  }
                  className="inline-flex items-center gap-1 text-xs text-muted-foreground transition-colors hover:text-foreground"
                >
                  {c.resolved ? (
                    <>
                      <Undo2 className="size-3" /> Reopen
                    </>
                  ) : (
                    <>
                      <Check className="size-3" /> Resolve
                    </>
                  )}
                </button>
                {(mine || canModerate) && (
                  <button
                    onClick={() => remove.mutate(c.id)}
                    className="inline-flex items-center gap-1 text-xs text-muted-foreground transition-colors hover:text-destructive"
                  >
                    <Trash2 className="size-3" /> Delete
                  </button>
                )}
              </div>
            </div>
          );
        })}
      </div>

      <div className="mt-3 border-t border-border pt-3">
        <textarea
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="Add a comment…"
          rows={2}
          className="w-full resize-none rounded-md border border-input bg-card px-2.5 py-2 text-sm outline-none focus:ring-1 focus:ring-[rgb(var(--ember))]"
        />
        <Button
          size="sm"
          variant="gold"
          className="mt-2 w-full"
          onClick={submit}
          disabled={add.isPending || !draft.trim()}
        >
          {add.isPending ? <Loader2 className="animate-spin" /> : <Send />}
          Comment
        </Button>
      </div>
    </div>
  );
}
