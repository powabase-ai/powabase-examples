import type { LucideIcon } from "lucide-react";

import { cn } from "@/lib/utils";

/**
 * The standard page chrome shared by every brand tab: a fixed header bar (icon +
 * title + optional meta + right-aligned actions) over a scrollable body. Using
 * one frame everywhere keeps the tabs feeling like one app even though their
 * bodies differ (master-detail vs centered single-column).
 *
 * Layout contract: render `<Page>` as the tab root (fills the panel height),
 * with a `<PageHeader>` first and either `<PageBody>` (centered) or a custom
 * full-bleed body second.
 */
export function Page({ children }: { children: React.ReactNode }) {
  return <div className="flex h-full flex-col">{children}</div>;
}

export function PageHeader({
  icon: Icon,
  title,
  meta,
  actions,
}: {
  icon: LucideIcon;
  title: string;
  meta?: React.ReactNode;
  actions?: React.ReactNode;
}) {
  return (
    <header className="flex h-14 shrink-0 items-center gap-2.5 border-b border-border bg-card px-6">
      <Icon className="size-[18px] shrink-0 text-muted-foreground" />
      <h1 className="font-display text-lg font-bold leading-none">{title}</h1>
      {meta != null && (
        <span className="truncate text-sm text-muted-foreground">{meta}</span>
      )}
      {actions && (
        <div className="ml-auto flex items-center gap-2">{actions}</div>
      )}
    </header>
  );
}

/** Scrollable, centered body for single-column tabs (Articles, Scouts, Team, …). */
export function PageBody({
  children,
  className,
}: {
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div className="min-h-0 flex-1 overflow-y-auto">
      <div className={cn("mx-auto w-full max-w-4xl px-6 py-8", className)}>
        {children}
      </div>
    </div>
  );
}
