"use client";

import { useState } from "react";
import { Loader2, Sparkles } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { useRefineArticle } from "@/lib/hooks/useArticles";
import { cn } from "@/lib/utils";

/** The "Refine with instructions" modal, opened from the article page's action
 *  bar. Free-text instructions + refine/rework, using the same useRefineArticle
 *  mutation as the rest of the app. On success it clears the draft and closes; on
 *  error (or a plain cancel/outside click) the draft text is kept so the user's
 *  wording isn't lost. Give this a `key={articleId}` where it's rendered so the
 *  draft resets when the shown article changes. */
export function RefineInstructionsDialog({
  open,
  onOpenChange,
  articleId,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  articleId: string;
}) {
  const [text, setText] = useState("");
  const [mode, setMode] = useState<"refine" | "rework">("refine");
  const refine = useRefineArticle(articleId);

  function run() {
    refine.mutate(
      { instructions: text, mode },
      {
        onSuccess: () => {
          toast.success(mode === "refine" ? "Refining…" : "Reworking…");
          setText("");
          onOpenChange(false);
        },
        onError: (e) => toast.error(e instanceof Error ? e.message : "Failed"),
      }
    );
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>Refine with instructions</DialogTitle>
          <DialogDescription>
            Tell the writer what to change, in your own words.
          </DialogDescription>
        </DialogHeader>

        <div>
          <Textarea
            autoFocus
            value={text}
            maxLength={4000}
            rows={6}
            placeholder="e.g. Rewrite the intro around agent memory and cut the history section."
            onChange={(e) => setText(e.target.value)}
          />
          <div className="mt-1.5 flex items-center justify-between text-xs text-muted-foreground">
            <span>{text.length}/4000</span>
            <div className="inline-flex rounded-md border">
              {(["refine", "rework"] as const).map((m) => (
                <button
                  key={m}
                  type="button"
                  onClick={() => setMode(m)}
                  className={cn(
                    "px-3 py-1",
                    mode === m && "bg-muted font-semibold text-foreground"
                  )}
                >
                  {m === "refine" ? "Refine" : "Rework"}
                </button>
              ))}
            </div>
          </div>
          <p className="mt-1.5 text-xs text-muted-foreground">
            {mode === "refine"
              ? "Light edit: keeps the outline and headings, changes only what you ask."
              : "May restructure, re-outline or change the angle, grounded in the article's sources."}
          </p>
        </div>

        <DialogFooter>
          <Button variant="outline" size="sm" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button
            size="sm"
            variant="gold"
            disabled={refine.isPending || !text.trim()}
            onClick={run}
          >
            {refine.isPending ? <Loader2 className="animate-spin" /> : <Sparkles />}
            Run
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
