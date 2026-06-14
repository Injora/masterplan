"use client";

import React, { useState } from "react";
import { CalendarClock, Play, Square, UploadCloud, Loader2, CheckCircle2, AlertCircle } from "lucide-react";
import { post } from "@/lib/api";

export default function SchedulerPage() {
  const [isRunning, setIsRunning] = useState(false); // Optimistic UI state
  const [isLoading, setIsLoading] = useState(false);
  const [actionMsg, setActionMsg] = useState<{ type: 'success' | 'error', text: string } | null>(null);

  const handleAction = async (endpoint: string, actionName: string) => {
    setIsLoading(true);
    setActionMsg(null);
    try {
      const response = await post<{ status: string; message?: string }>(`/scheduler/${endpoint}`);
      const successMsg = response.message || `Successfully triggered: ${actionName}`;
      setActionMsg({ type: 'success', text: successMsg });
      
      if (endpoint === 'start') setIsRunning(true);
      if (endpoint === 'stop') setIsRunning(false);
    } catch (err: any) {
      setActionMsg({ type: 'error', text: err.message || `Failed to trigger ${actionName}` });
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="max-w-3xl space-y-8 animate-fade-in-up">
      <div className="flex items-center gap-4">
        <div className="flex items-center justify-center w-12 h-12 rounded-2xl bg-gradient-to-br from-status-amber to-status-red shadow-[0_0_28px_rgba(245,158,11,0.25)]">
          <CalendarClock className="w-6 h-6 text-white" />
        </div>
        <div>
          <h1 className="text-2xl font-display font-bold text-gradient-warm">
            Scheduler & Uploader
          </h1>
          <p className="text-text-muted text-sm mt-0.5">
            Manage background cron jobs and the YouTube upload queue
          </p>
        </div>
      </div>

      <div className="grid gap-6">
        {/* ── Engine Control ────────────────────────────────────── */}
        <div className="glass-panel p-6 sm:p-8 relative overflow-hidden">
          {/* Animated glow if running */}
          {isRunning && (
            <div className="absolute inset-0 bg-gradient-to-r from-status-green/5 to-transparent animate-pulse pointer-events-none" />
          )}
          
          <div className="relative z-10 flex flex-col sm:flex-row sm:items-center justify-between gap-6">
            <div>
              <div className="flex items-center gap-3 mb-2">
                <h2 className="text-xl font-display font-semibold text-text-primary">
                  Master Scheduler
                </h2>
                {isRunning ? (
                  <span className="badge badge-green animate-fade-in-up">Active</span>
                ) : (
                  <span className="badge badge-amber animate-fade-in-up">Paused</span>
                )}
              </div>
              <p className="text-sm text-text-secondary">
                Controls the automated interval triggers for the Clipper, Generator, and Uploader.
              </p>
            </div>
            
            <div className="flex gap-3 shrink-0">
              <button
                onClick={() => handleAction('start', 'Start Scheduler')}
                disabled={isLoading || isRunning}
                className={`btn-primary bg-gradient-to-br from-status-green to-emerald-600 border-emerald-500/40 shadow-[0_0_20px_rgba(16,185,129,0.15)] hover:from-emerald-600 hover:to-status-green ${isRunning ? 'opacity-50' : ''}`}
              >
                {isLoading && !isRunning ? <Loader2 className="w-4 h-4 animate-spin" /> : <Play className="w-4 h-4" />}
                Start
              </button>
              
              <button
                onClick={() => handleAction('stop', 'Stop Scheduler')}
                disabled={isLoading || !isRunning}
                className={`btn-ghost border-status-red/30 text-status-red hover:bg-status-red/10 hover:border-status-red/50 ${!isRunning ? 'opacity-50' : ''}`}
              >
                <Square className="w-4 h-4" />
                Stop
              </button>
            </div>
          </div>
        </div>

        {/* ── Manual Queue Trigger ──────────────────────────────── */}
        <div className="glass-panel p-6 sm:p-8 flex flex-col sm:flex-row sm:items-center justify-between gap-6">
          <div>
             <h2 className="text-xl font-display font-semibold text-text-primary mb-2">
                Upload Queue
              </h2>
              <p className="text-sm text-text-secondary">
                Manually push all 'ready' clips and stories to YouTube immediately.
              </p>
          </div>
          <button
            onClick={() => handleAction('trigger_upload', 'Upload Queue')}
            disabled={isLoading}
            className="btn-ghost shrink-0 bg-bg-input"
          >
             <UploadCloud className="w-4 h-4" />
             Trigger Uploads
          </button>
        </div>

        {/* ── Manual Daily Pipeline Trigger ─────────────────────── */}
        <div className="glass-panel p-6 sm:p-8 flex flex-col sm:flex-row sm:items-center justify-between gap-6">
          <div>
             <h2 className="text-xl font-display font-semibold text-text-primary mb-2">
                Daily Morning Automation Pipeline
              </h2>
              <p className="text-sm text-text-secondary">
                Manually trigger the full daily morning flow: discover trends, download videos, generate exactly 5 vertical clips, and queue uploads for active channels.
              </p>
          </div>
          <button
            onClick={() => handleAction('trigger_daily_pipeline', 'Daily Morning Shorts Pipeline')}
            disabled={isLoading}
            className="btn-primary shrink-0 bg-gradient-to-r from-accent-500 to-status-blue hover:from-accent-600 hover:to-accent-500 flex items-center gap-2 justify-center"
          >
             <CalendarClock className="w-4 h-4" />
             Run Daily Pipeline Now
          </button>
        </div>
      </div>

      {/* ── Feedback Messages ─────────────────────────────────── */}
      {actionMsg && (
        <div className={`p-4 rounded-xl border flex items-center gap-3 animate-fade-in-up ${
          actionMsg.type === 'success' 
            ? 'bg-status-green/10 border-status-green/30' 
            : 'bg-status-red/10 border-status-red/30'
        }`}>
          {actionMsg.type === 'success' ? (
             <CheckCircle2 className="w-5 h-5 text-status-green shrink-0" />
          ) : (
             <AlertCircle className="w-5 h-5 text-status-red shrink-0" />
          )}
          <p className={`text-sm ${actionMsg.type === 'success' ? 'text-status-green' : 'text-status-red'}`}>
            {actionMsg.text}
          </p>
        </div>
      )}
    </div>
  );
}
