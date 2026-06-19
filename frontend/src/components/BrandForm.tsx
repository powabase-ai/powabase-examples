"use client";

import * as React from "react";

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import {
  BrandFields,
  brandToForm,
  emptyBrandForm,
  formToPayload,
  type BrandFormState,
} from "@/components/brand/BrandFields";
import type { BusinessProfile, BusinessProfileInput } from "@/lib/api";

export function BrandForm({
  open,
  onOpenChange,
  brand,
  onSave,
  saving,
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  brand: BusinessProfile | null;
  onSave: (data: BusinessProfileInput) => Promise<void> | void;
  saving?: boolean;
}) {
  const [form, setForm] = React.useState<BrandFormState>(emptyBrandForm());

  React.useEffect(() => {
    if (!open) return;
    setForm(brand ? brandToForm(brand) : emptyBrandForm());
  }, [open, brand]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    await onSave(formToPayload(form));
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle className="font-display">
            {brand ? "Edit brand" : "New brand"}
          </DialogTitle>
          <DialogDescription>
            A brand profile scopes research, briefs, articles, and scouts to one
            business niche.
          </DialogDescription>
        </DialogHeader>

        <form onSubmit={submit} className="grid gap-4">
          <BrandFields value={form} onChange={(p) => setForm((f) => ({ ...f, ...p }))} />
          <DialogFooter className="mt-2">
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" variant="gold" disabled={saving || !form.name.trim()}>
              {saving ? "Saving…" : brand ? "Save changes" : "Create brand"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
