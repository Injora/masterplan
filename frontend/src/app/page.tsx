"use client";

import React, { useEffect, useState } from "react";
import {
  Scissors,
  Sparkles,
  Radio,
  TrendingUp,
  AlertCircle,
  Clock,
  Zap,
} from "lucide-react";
import { fetchDashboardStats, type DashboardStats } from "@/lib/api";

/* ── Stat card configuration ─────────────────────────────────── */

interface StatCardProps {
  label: string;
  value: number | string;
  icon: React.ElementType;
  accent: string;       // badge-* class
  glowColor: string;    // tailwind arbitrary shadow color
}

function StatCard({ label, value, icon: Icon, accent, glowColor }: StatCardProps) {
  return (
    <div
      className={`stat-card glass-panel p-5 flex flex-col gap-3 transition-transform duration-200 hover:scale-[1.02]`}
    >
      <div className="flex items-center justify-between">
        <span className={`badge ${accent}`}>
          <Icon className="w-3 h-3" />
          {label}
        </span>
      </div>
      <p
        className="text-3xl font-display font-bold tracking-tight"
        style={{ textShadow: `0 0 24px ${glowColor}` }}
      >
        {value}
      </p>
    </div>
  );
}

/* ── Dashboard page ──────────────────────────────────────────── */

export default function DashboardPage() {
  const [stats, setStats] = useState<DashboardStats | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchDashboardStats()
      .then(setStats)
      .catch((err) => {
        setError(err.message ?? "Failed to connect to backend");
      });
  }, []);

  const cards: StatCardProps[] = stats
    ? [
        {
          label: "Total Clips",
          value: stats.total_clips,
          icon: Scissors,
          accent: "badge-cyan",
          glowColor: "rgba(6,182,212,0.25)",
        },
        {
          label: "Total Stories",
          value: stats.total_stories,
          icon: Sparkles,
          accent: "badge-purple",
          glowColor: "rgba(168,85,247,0.25)",
        },
        {
          label: "Published",
          value: (stats.clips_uploaded || 0) + (stats.stories_uploaded || 0),
          icon: TrendingUp,
          accent: "badge-green",
          glowColor: "rgba(16,185,129,0.25)",
        },
        {
          label: "Pending",
          value: (stats.clips_pending || 0) + (stats.stories_pending || 0),
          icon: Clock,
          accent: "badge-amber",
          glowColor: "rgba(245,158,11,0.25)",
        },
        {
          label: "Channels",
          value: stats.active_channels || 0,
          icon: Radio,
          accent: "badge-blue",
          glowColor: "rgba(59,130,246,0.25)",
        },
        {
          label: "Errors",
          value: (stats.clips_error || 0) + (stats.stories_error || 0),
          icon: AlertCircle,
          accent: "badge-red",
          glowColor: "rgba(239,68,68,0.25)",
        },
      ]
    : [];

  return (
    <section className="space-y-8">
      {/* ── Header ──────────────────────────────────────────── */}
      <div className="flex items-center gap-4">
        <div className="flex items-center justify-center w-12 h-12 rounded-2xl bg-gradient-to-br from-accent-500 to-status-cyan shadow-[0_0_28px_rgba(139,92,246,0.25)]">
          <Zap className="w-6 h-6 text-white" />
        </div>
        <div>
          <h1 className="text-2xl font-display font-bold text-gradient-brand">
            Command Center
          </h1>
          <p className="text-text-muted text-sm mt-0.5">
            Real-time overview of your Shorts factory
          </p>
        </div>
      </div>

      {/* ── Error banner ────────────────────────────────────── */}
      {error && (
        <div className="glass-panel-sm flex items-center gap-3 p-4 border-status-red/30 border">
          <AlertCircle className="w-4 h-4 text-status-red shrink-0" />
          <p className="text-sm text-status-red">{error}</p>
          <span className="text-text-muted text-xs ml-auto">
            Is the backend running?
          </span>
        </div>
      )}

      {/* ── Stat cards ──────────────────────────────────────── */}
      {stats ? (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4 stagger-children">
          {cards.map((card) => (
            <StatCard key={card.label} {...card} />
          ))}
        </div>
      ) : (
        !error && (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
            {Array.from({ length: 6 }).map((_, i) => (
              <div
                key={i}
                className="glass-panel p-5 h-28 animate-pulse"
              >
                <div className="h-4 w-24 bg-bg-hover rounded-full mb-4" />
                <div className="h-8 w-16 bg-bg-hover rounded-lg" />
              </div>
            ))}
          </div>
        )
      )}

      {/* ── Quick actions ───────────────────────────────────── */}
      <div className="glass-panel p-6 space-y-4">
        <h2 className="text-lg font-display font-semibold text-text-primary">
          Quick Actions
        </h2>
        <div className="flex flex-wrap gap-3">
          <a href="/clipper" className="btn-primary">
            <Scissors className="w-4 h-4" />
            Run Clipper
          </a>
          <a href="/generator" className="btn-primary">
            <Sparkles className="w-4 h-4" />
            Generate Story
          </a>
          <a href="/channels" className="btn-ghost">
            <Radio className="w-4 h-4" />
            Manage Channels
          </a>
        </div>
      </div>
    </section>
  );
}
