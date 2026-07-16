"use client";

import * as React from "react";
// Share2 (not a brand "Linkedin" icon — lucide has been deprecating brand icons).
import { Copy, Loader2, Share2, Sparkles, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { ANGLES, type Angle, type LinkedInPost } from "@/lib/api";
import {
  useDeleteLinkedInPost,
  useGenerateLinkedInPost,
  useLinkedInPosts,
  useUpdateLinkedInPost,
} from "@/lib/hooks/useLinkedIn";

const FOLD_CHARS = 210; // LinkedIn hides everything after ~this behind "…see more"
const MAX_CHARS = 3000;

const angleLabel = (slug: string) =>
  ANGLES.find((a) => a.slug === slug)?.label ?? slug;

export function LinkedInPanel({
  articleId,
  articleReady,
}: {
  articleId: string;
  articleReady: boolean;
}) {
  const posts = useLinkedInPosts(articleId);
  const generate = useGenerateLinkedInPost(articleId);
  const [angle, setAngle] = React.useState<Angle>("key_insight");

  function onGenerate() {
    generate.mutate(angle, {
      onSuccess: () => toast.success("LinkedIn post generated"),
      onError: (e) =>
        toast.error(e instanceof Error ? e.message : "Generation failed"),
    });
  }

  return (
    <div className="space-y-4">
      <div className="space-y-2 rounded-md border border-border p-3">
        <label className="text-xs font-medium text-muted-foreground">
          Generate a LinkedIn post
        </label>
        <div className="flex gap-2">
          <select
            value={angle}
            onChange={(e) => setAngle(e.target.value as Angle)}
            className="h-9 flex-1 rounded-md border border-input bg-background px-2 text-sm"
          >
            {ANGLES.map((a) => (
              <option key={a.slug} value={a.slug}>
                {a.label}
              </option>
            ))}
          </select>
          <Button
            variant="gold"
            size="sm"
            onClick={onGenerate}
            disabled={generate.isPending || !articleReady}
          >
            {generate.isPending ? (
              <Loader2 className="animate-spin" />
            ) : (
              <Sparkles />
            )}
            Generate variant
          </Button>
        </div>
        <p className="text-[11px] text-muted-foreground">
          {articleReady
            ? "Uses credits. Each generation adds a new variant you can edit or delete."
            : "Generate the article draft first — there's no content to repurpose yet."}
        </p>
      </div>

      {posts.isLoading ? (
        <p className="text-sm text-muted-foreground">Loading…</p>
      ) : (posts.data ?? []).length === 0 ? (
        <p className="text-sm text-muted-foreground">
          No posts yet — pick an angle and generate one.
        </p>
      ) : (
        <ul className="space-y-3">
          {(posts.data ?? []).map((p) => (
            <li key={p.id}>
              <PostCard articleId={articleId} post={p} />
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function PostCard({
  articleId,
  post,
}: {
  articleId: string;
  post: LinkedInPost;
}) {
  const update = useUpdateLinkedInPost(articleId);
  const del = useDeleteLinkedInPost(articleId);
  const [body, setBody] = React.useState(post.body);
  // eslint-disable-next-line react-hooks/set-state-in-effect -- sync local edit buffer when the server value changes (existing pattern, see InternalLinksPanel.tsx)
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
    <div className="space-y-2 rounded-md border border-border p-3">
      <div className="flex items-center gap-2">
        <Share2 className="size-3.5 text-[rgb(var(--ember))]" />
        <span className="rounded bg-secondary px-1.5 py-0.5 text-xs text-muted-foreground">
          {angleLabel(post.angle)}
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
