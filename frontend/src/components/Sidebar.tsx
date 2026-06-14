"use client";

import React, { useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "@/lib/utils";
import {
  LayoutDashboard,
  Scissors,
  Sparkles,
  Radio,
  CalendarClock,
  ChevronLeft,
  ChevronRight,
  Zap,
} from "lucide-react";

/* ── Navigation items ──────────────────────────────────────────── */

interface NavItem {
  label: string;
  href: string;
  icon: React.ElementType;
}

const NAV_ITEMS: NavItem[] = [
  { label: "Dashboard",  href: "/",           icon: LayoutDashboard },
  { label: "Clipper",    href: "/clipper",     icon: Scissors },
  { label: "Generator",  href: "/generator",   icon: Sparkles },
  { label: "Channels",   href: "/channels",    icon: Radio },
  { label: "Scheduler",  href: "/scheduler",   icon: CalendarClock },
];

/* ── Sidebar component ─────────────────────────────────────────── */

export default function Sidebar() {
  const pathname = usePathname();
  const [collapsed, setCollapsed] = useState(false);

  return (
    <aside
      className={cn(
        "glass-sidebar relative flex flex-col h-screen sticky top-0 z-40",
        "transition-all duration-300 ease-in-out",
        collapsed ? "w-[72px]" : "w-[260px]",
      )}
    >
      {/* ── Brand ─────────────────────────────────────────────── */}
      <div className="flex items-center gap-3 px-5 h-[72px] shrink-0">
        <div
          className={cn(
            "flex items-center justify-center rounded-xl",
            "w-10 h-10 shrink-0",
            "bg-gradient-to-br from-accent-500 to-status-cyan",
            "shadow-[0_0_20px_rgba(139,92,246,0.3)]",
          )}
        >
          <Zap className="w-5 h-5 text-white" />
        </div>

        <div
          className={cn(
            "overflow-hidden transition-all duration-300",
            collapsed ? "w-0 opacity-0" : "w-auto opacity-100",
          )}
        >
          <h1 className="text-gradient-brand font-display font-bold text-lg whitespace-nowrap leading-tight">
            Antigravity
          </h1>
          <p className="text-text-muted text-[10px] font-medium tracking-wider uppercase whitespace-nowrap">
            Shorts Factory
          </p>
        </div>
      </div>

      {/* ── Divider ───────────────────────────────────────────── */}
      <div className="mx-4 h-px bg-border-default" />

      {/* ── Navigation ────────────────────────────────────────── */}
      <nav className="flex-1 flex flex-col gap-1 px-3 py-4 overflow-y-auto">
        {NAV_ITEMS.map((item) => {
          const isActive =
            item.href === "/"
              ? pathname === "/"
              : pathname.startsWith(item.href);

          return (
            <Link
              key={item.href}
              href={item.href}
              className={cn(
                "group relative flex items-center gap-3 rounded-xl px-3 py-2.5",
                "text-sm font-medium transition-all duration-200",
                isActive
                  ? "bg-accent-500/10 text-accent-400"
                  : "text-text-secondary hover:bg-bg-hover hover:text-text-primary",
              )}
            >
              {/* Active indicator bar */}
              {isActive && (
                <span className="absolute left-0 top-1/2 -translate-y-1/2 w-[3px] h-5 rounded-r-full bg-accent-500" />
              )}

              <item.icon
                className={cn(
                  "w-[18px] h-[18px] shrink-0 transition-colors",
                  isActive
                    ? "text-accent-400"
                    : "text-text-muted group-hover:text-text-secondary",
                )}
              />

              <span
                className={cn(
                  "overflow-hidden transition-all duration-300 whitespace-nowrap",
                  collapsed ? "w-0 opacity-0" : "w-auto opacity-100",
                )}
              >
                {item.label}
              </span>
            </Link>
          );
        })}
      </nav>

      {/* ── Collapse toggle ───────────────────────────────────── */}
      <div className="px-3 pb-4">
        <button
          onClick={() => setCollapsed((c) => !c)}
          className={cn(
            "flex items-center justify-center w-full rounded-xl py-2.5",
            "text-text-muted hover:text-text-secondary hover:bg-bg-hover",
            "transition-all duration-200 cursor-pointer",
          )}
          aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
        >
          {collapsed ? (
            <ChevronRight className="w-4 h-4" />
          ) : (
            <ChevronLeft className="w-4 h-4" />
          )}
        </button>
      </div>
    </aside>
  );
}
