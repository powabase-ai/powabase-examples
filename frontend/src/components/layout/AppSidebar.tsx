"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { ChevronLeft, Layers, Search, Settings, type LucideIcon } from "lucide-react";

import { useBrands } from "@/lib/hooks/useBrands";
import { cn } from "@/lib/utils";

interface NavItem {
  title: string;
  href: string;
  icon: LucideIcon;
  exact?: boolean;
}

export function AppSidebar({ brandId }: { brandId: string }) {
  const pathname = usePathname();
  const router = useRouter();
  const { data: brands } = useBrands();

  const nav: NavItem[] = [
    { title: "Workspace", href: `/brands/${brandId}`, icon: Search, exact: true },
    { title: "Sources", href: `/brands/${brandId}/sources`, icon: Layers },
    { title: "Settings", href: `/brands/${brandId}/settings`, icon: Settings },
  ];

  return (
    <aside className="sticky top-0 flex h-screen w-60 shrink-0 flex-col border-r border-border bg-card">
      <div className="p-4">
        <Link href="/" className="font-display text-lg font-bold tracking-tight">
          Rank<span className="text-[rgb(var(--accent-gold))]">Forge</span>
        </Link>
        <select
          value={brandId}
          onChange={(e) => router.push(`/brands/${e.target.value}`)}
          className="mt-3 h-9 w-full rounded-md border border-input bg-card px-2 text-sm shadow-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          {brands?.map((b) => (
            <option key={b.id} value={b.id}>
              {b.name}
            </option>
          ))}
        </select>
      </div>

      <nav className="flex flex-col gap-0.5 px-3">
        {nav.map((item) => {
          const active = item.exact
            ? pathname === item.href
            : pathname.startsWith(item.href);
          return (
            <Link
              key={item.href}
              href={item.href}
              className={cn(
                "flex items-center gap-2.5 rounded-md px-3 py-2 text-sm font-medium transition-colors",
                active
                  ? "bg-secondary text-foreground"
                  : "text-muted-foreground hover:bg-secondary hover:text-foreground"
              )}
            >
              <item.icon className="size-4 shrink-0" />
              {item.title}
            </Link>
          );
        })}
      </nav>

      <div className="mt-auto p-4">
        <Link
          href="/"
          className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
        >
          <ChevronLeft className="size-3.5" /> All brands
        </Link>
      </div>
    </aside>
  );
}
