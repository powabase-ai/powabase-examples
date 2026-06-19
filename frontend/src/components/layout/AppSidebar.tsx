"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import {
  ChevronLeft,
  Layers,
  PenLine,
  Search,
  Settings,
  type LucideIcon,
} from "lucide-react";

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
    { title: "Research", href: `/brands/${brandId}`, icon: Search, exact: true },
    { title: "Sources", href: `/brands/${brandId}/sources`, icon: Layers },
    { title: "Articles", href: `/brands/${brandId}/articles`, icon: PenLine },
    { title: "Settings", href: `/brands/${brandId}/settings`, icon: Settings },
  ];

  return (
    <aside className="sticky top-0 flex h-screen w-60 shrink-0 flex-col bg-[rgb(var(--iron))] text-[rgb(var(--iron-text))]">
      <div className="px-5 pb-4 pt-5">
        <Link
          href="/"
          className="font-display text-lg font-bold tracking-tight text-[rgb(var(--iron-strong))]"
        >
          Rank<span className="text-[rgb(var(--ember))]">Forge</span>
        </Link>
        <select
          value={brandId}
          onChange={(e) => router.push(`/brands/${e.target.value}`)}
          className="mt-4 h-9 w-full rounded-md border border-[rgb(var(--iron-line))] bg-[rgb(var(--iron-hover))] px-2.5 text-sm text-[rgb(var(--iron-strong))] outline-none focus-visible:ring-2 focus-visible:ring-[rgb(var(--ember))]"
        >
          {brands?.map((b) => (
            <option key={b.id} value={b.id} className="bg-[rgb(var(--iron))]">
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
                "flex items-center gap-2.5 border-l-2 py-2 pl-3.5 pr-3 text-sm font-medium transition-colors",
                active
                  ? "border-[rgb(var(--ember))] bg-[rgb(var(--iron-hover))] text-[rgb(var(--iron-strong))]"
                  : "border-transparent text-[rgb(var(--iron-text))] hover:bg-[rgb(var(--iron-hover))]/60 hover:text-[rgb(var(--iron-strong))]"
              )}
            >
              <item.icon
                className={cn(
                  "size-4 shrink-0",
                  active && "text-[rgb(var(--ember-bright))]"
                )}
              />
              {item.title}
            </Link>
          );
        })}
      </nav>

      <div className="mt-auto px-5 pb-5">
        <Link
          href="/"
          className="inline-flex items-center gap-1 text-xs text-[rgb(var(--iron-text))] transition-colors hover:text-[rgb(var(--iron-strong))]"
        >
          <ChevronLeft className="size-3.5" /> All brands
        </Link>
      </div>
    </aside>
  );
}
