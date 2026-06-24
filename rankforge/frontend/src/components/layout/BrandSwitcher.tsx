"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import * as Select from "@radix-ui/react-select";
import { Check, ChevronsUpDown, Plus } from "lucide-react";
import { toast } from "sonner";

import { BrandForm } from "@/components/BrandForm";
import { useBrands, useCreateBrand } from "@/lib/hooks/useBrands";
import type { BusinessProfileInput } from "@/lib/api";

// Choosing this sentinel opens the create-brand dialog instead of switching.
const NEW_BRAND = "__new__";

function initial(name: string | undefined): string {
  return (name?.trim()?.[0] ?? "?").toUpperCase();
}

/** Fancy brand/workspace switcher: an avatar + name trigger that opens a styled
 * popover of brands (checkmark on the current one) plus a "New brand" action. */
export function BrandSwitcher({ brandId }: { brandId: string }) {
  const router = useRouter();
  const { data: brands } = useBrands();
  const createBrand = useCreateBrand();
  const [createOpen, setCreateOpen] = useState(false);
  const current = brands?.find((b) => b.id === brandId);

  function onValueChange(value: string) {
    if (value === NEW_BRAND) setCreateOpen(true);
    else router.push(`/brands/${value}`);
  }

  async function handleCreate(data: BusinessProfileInput) {
    try {
      const b = await createBrand.mutateAsync(data);
      setCreateOpen(false);
      router.push(`/brands/${b.id}`);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Could not create brand");
    }
  }

  return (
    <>
      <Select.Root value={brandId} onValueChange={onValueChange}>
        <Select.Trigger
          aria-label="Switch brand"
          className="group mt-4 flex h-12 w-full items-center gap-2.5 rounded-lg border border-[rgb(var(--iron-line))] bg-[rgb(var(--iron-hover))] px-2.5 text-left outline-none transition-colors hover:border-[rgb(var(--ember))]/50 focus-visible:ring-2 focus-visible:ring-[rgb(var(--ember))]"
        >
          <span className="flex size-8 shrink-0 items-center justify-center rounded-md bg-gradient-to-br from-[rgb(var(--ember))] to-[rgb(var(--ember-bright))] text-sm font-bold text-white shadow-sm">
            {initial(current?.name)}
          </span>
          <span className="min-w-0 flex-1">
            <span className="block text-[10px] font-medium uppercase tracking-wide text-[rgb(var(--iron-text))]">
              Brand
            </span>
            <span className="block truncate text-sm font-semibold text-[rgb(var(--iron-strong))]">
              {current?.name ?? "Select brand"}
            </span>
          </span>
          <ChevronsUpDown className="size-4 shrink-0 text-[rgb(var(--iron-text))] transition-colors group-hover:text-[rgb(var(--iron-strong))]" />
        </Select.Trigger>

        <Select.Portal>
          <Select.Content
            position="popper"
            sideOffset={6}
            className="z-50 max-h-[60vh] w-[var(--radix-select-trigger-width)] overflow-hidden rounded-lg border border-[rgb(var(--iron-line))] bg-[rgb(var(--iron))] p-1 text-[rgb(var(--iron-text))] shadow-xl"
          >
            <Select.Viewport>
              <div className="px-2 py-1.5 text-[10px] font-medium uppercase tracking-wide text-[rgb(var(--iron-text))]">
                Brands
              </div>
              {brands?.map((b) => (
                <Select.Item
                  key={b.id}
                  value={b.id}
                  className="flex cursor-pointer items-center gap-2.5 rounded-md px-2 py-2 text-sm outline-none data-[highlighted]:bg-[rgb(var(--iron-hover))] data-[state=checked]:text-[rgb(var(--iron-strong))]"
                >
                  <span className="flex size-6 shrink-0 items-center justify-center rounded bg-[rgb(var(--iron-hover))] text-[10px] font-bold text-[rgb(var(--iron-strong))]">
                    {initial(b.name)}
                  </span>
                  <Select.ItemText>{b.name}</Select.ItemText>
                  <Select.ItemIndicator className="ml-auto">
                    <Check className="size-4 text-[rgb(var(--ember-bright))]" />
                  </Select.ItemIndicator>
                </Select.Item>
              ))}

              <Select.Separator className="my-1 h-px bg-[rgb(var(--iron-line))]" />

              <Select.Item
                value={NEW_BRAND}
                className="flex cursor-pointer items-center gap-2.5 rounded-md px-2 py-2 text-sm text-[rgb(var(--ember-bright))] outline-none data-[highlighted]:bg-[rgb(var(--iron-hover))]"
              >
                <span className="flex size-6 shrink-0 items-center justify-center rounded border border-dashed border-[rgb(var(--ember))]/60">
                  <Plus className="size-3.5" />
                </span>
                <Select.ItemText>New brand</Select.ItemText>
              </Select.Item>
            </Select.Viewport>
          </Select.Content>
        </Select.Portal>
      </Select.Root>

      <BrandForm
        open={createOpen}
        onOpenChange={setCreateOpen}
        brand={null}
        onSave={handleCreate}
        saving={createBrand.isPending}
      />
    </>
  );
}
