"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  Layers,
  Library,
  LogOut,
  PenLine,
  Radar,
  Search,
  Settings,
  Users,
  type LucideIcon,
} from "lucide-react";

import { BrandSwitcher } from "@/components/layout/BrandSwitcher";
import { useAuth } from "@/lib/auth/AuthProvider";
import { cn } from "@/lib/utils";

interface NavItem {
  title: string;
  href: string;
  icon: LucideIcon;
  exact?: boolean;
}

export function AppSidebar({ brandId }: { brandId: string }) {
  const pathname = usePathname();
  const { profile, signOut } = useAuth();

  const nav: NavItem[] = [
    { title: "Scouts", href: `/brands/${brandId}/scouts`, icon: Radar },
    { title: "Research", href: `/brands/${brandId}`, icon: Search, exact: true },
    { title: "Sources", href: `/brands/${brandId}/sources`, icon: Layers },
    { title: "Articles", href: `/brands/${brandId}/articles`, icon: PenLine },
    { title: "Materials", href: `/brands/${brandId}/materials`, icon: Library },
    { title: "Team", href: `/brands/${brandId}/team`, icon: Users },
    { title: "Settings", href: `/brands/${brandId}/settings`, icon: Settings },
  ];

  return (
    <aside className="flex h-full w-full flex-col bg-[rgb(var(--iron))] text-[rgb(var(--iron-text))]">
      <div className="px-5 pb-4 pt-5">
        <Link
          href="/"
          className="font-display text-lg font-bold tracking-tight text-[rgb(var(--iron-strong))]"
        >
          Rank<span className="text-[rgb(var(--ember))]">Forge</span>
        </Link>
        <BrandSwitcher brandId={brandId} />
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

      <div className="mt-auto border-t border-[rgb(var(--iron-line))]">
        <div className="flex items-center justify-between gap-2 px-5 py-3.5">
          <div className="min-w-0">
            <div className="truncate text-xs font-medium text-[rgb(var(--iron-strong))]">
              {profile?.email ?? "—"}
            </div>
            {profile && (
              <div className="text-[10px] uppercase tracking-wide text-[rgb(var(--iron-text))]">
                {profile.role}
              </div>
            )}
          </div>
          <button
            onClick={() => signOut()}
            title="Sign out"
            aria-label="Sign out"
            className="shrink-0 rounded-md p-1.5 text-[rgb(var(--iron-text))] transition-colors hover:bg-[rgb(var(--iron-hover))] hover:text-[rgb(var(--iron-strong))]"
          >
            <LogOut className="size-4" />
          </button>
        </div>
      </div>
    </aside>
  );
}
