"use client";

import * as React from "react";
// Share2 (not a brand "Linkedin" icon — lucide has been deprecating brand icons).
import { Copy, Loader2, Share2, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { ANGLES, type LinkedInPost } from "@/lib/api";
import {
  useDeleteLinkedInPost,
  useUpdateLinkedInPost,
} from "@/lib/hooks/useLinkedIn";

export const FOLD_CHARS = 210; // LinkedIn hides everything after ~this behind "…see more"
export const MAX_CHARS = 3000;

export const angleLabel = (slug: string) =>
  ANGLES.find((a) => a.slug === slug)?.label ?? slug;

/** One editable LinkedIn post variant: angle badge + created date, an above-the-fold
 *  preview (what shows before "…see more"), the editable body with a char counter,
 *  and Copy / Save / Delete. Used by the Social page. */
export function LinkedInPostCard({
  articleId,
  post,
}: {
  articleId: string;
  post: LinkedInPost;
}) {
  const update = useUpdateLinkedInPost(articleId);
  const del = useDeleteLinkedInPost(articleId);
  const [body, setBody] = React.useState(post.body);
  React.useEffect(() => setBody(post.body), [post.body]);

  const dirty = body !== post.body;
  const overFold = body.length > FOLD_CHARS;
  const overMax = body.length > MAX_CHARS;

  function onCopy() {
    navigator.clipboard.writeText(body).then(
      () => toast.success("Copied to clipboard"),
      () => toast.error("Couldn't copy — select the text and copy manually")
    );
  }

  function onSave() {
    update.mutate(
      { postId: post.id, body },
      {
        onSuccess: () => toast.success("Saved"),
        onError: (e) =>
          toast.error(e instanceof Error ? e.message : "Save failed"),
      }
    );
  }

  function onDelete() {
    if (!window.confirm("Delete this variant?")) return;
    del.mutate(post.id, {
      onSuccess: () => toast.success("Deleted"),
      onError: (e) =>
        toast.error(e instanceof Error ? e.message : "Delete failed"),
    });
  }

  return (
    <div className="space-y-2 rounded-md border border-border bg-card p-3">
      <div className="flex items-center gap-2">
        <Share2 className="size-3.5 text-[rgb(var(--ember))]" />
        <span className="rounded bg-secondary px-1.5 py-0.5 text-xs text-muted-foreground">
          {angleLabel(post.angle)}
        </span>
        <span className="text-[11px] text-muted-foreground">
          {new Date(post.created_at).toLocaleDateString()}
        </span>
      </div>

      {/* Above-the-fold preview: what shows before "…see more". */}
      <div className="rounded border border-dashed border-border bg-muted/40 p-2 text-xs">
        <span className="text-foreground">{body.slice(0, FOLD_CHARS)}</span>
        {overFold && <span className="text-muted-foreground">… see more</span>}
        <div className="mt-1 text-[10px] uppercase tracking-wide text-muted-foreground">
          Above the fold ({Math.min(body.length, FOLD_CHARS)}/{FOLD_CHARS})
        </div>
      </div>

      <Textarea
        value={body}
        onChange={(e) => setBody(e.target.value)}
        rows={10}
        className="text-sm"
      />

      <div className="flex items-center gap-2">
        <span
          className={
            overMax
              ? "text-[11px] text-destructive"
              : "text-[11px] text-muted-foreground"
          }
        >
          {body.length}/{MAX_CHARS}
        </span>
        <div className="ml-auto flex items-center gap-1">
          <Button variant="outline" size="sm" onClick={onCopy}>
            <Copy /> Copy
          </Button>
          <Button
            variant="gold"
            size="sm"
            onClick={onSave}
            disabled={!dirty || overMax || body.trim().length === 0 || update.isPending}
          >
            {update.isPending && <Loader2 className="animate-spin" />}
            Save
          </Button>
          <Button
            variant="ghost"
            size="sm"
            className="text-muted-foreground hover:text-destructive"
            onClick={onDelete}
            disabled={del.isPending}
          >
            {del.isPending ? <Loader2 className="animate-spin" /> : <Trash2 />}
          </Button>
        </div>
      </div>
    </div>
  );
}
