"use client";

import React, { useState, useEffect } from "react";
import { 
  Scissors, 
  Loader2, 
  AlertCircle, 
  CheckCircle2, 
  Video, 
  Download, 
  RefreshCw, 
  FileVideo, 
  Tag 
} from "lucide-react";
import { get, postHeavy } from "@/lib/api";

interface Clip {
  id: number;
  source_video_id: number;
  start_time: number;
  end_time: number;
  play_url?: string;
  status: 'pending' | 'processing' | 'ready' | 'uploading' | 'uploaded' | 'error';
  title: string;
  description: string;
  tags: string;
  error_message?: string;
  created_at: string;
}

export default function ClipperPage() {
  const [niches, setNiches] = useState<string[]>([]);
  const [selectedNiche, setSelectedNiche] = useState<string>("");
  const [targetUrl, setTargetUrl] = useState<string>("");
  const [searchCount, setSearchCount] = useState<number>(5);
  const [maxClips, setMaxClips] = useState<number>(3);
  const [minScore, setMinScore] = useState<number>(6);
  
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [successMsg, setSuccessMsg] = useState<string | null>(null);

  const [clips, setClips] = useState<Clip[]>([]);

  useEffect(() => {
    get<{ niches: string[] }>("/clipper/niches")
      .then((data) => {
        setNiches(data.niches);
        if (data.niches.length > 0) setSelectedNiche(data.niches[0]);
      })
      .catch((err) => console.error("Failed to fetch niches:", err));
  }, []);

  const loadClips = () => {
    get<{ clips: Clip[] }>("/clipper/clips")
      .then((data) => setClips(data.clips))
      .catch((err) => console.error("Failed to fetch clips:", err));
  };

  useEffect(() => {
    loadClips();
  }, []);

  // Poll for updates if there are clips actively processing or if we just started a pipeline run
  useEffect(() => {
    const hasActiveClips = clips.some(
      (c) => c.status === "pending" || c.status === "processing"
    );
    if (hasActiveClips || isLoading) {
      const interval = setInterval(loadClips, 4000);
      return () => clearInterval(interval);
    }
  }, [clips, isLoading]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setIsLoading(true);
    setError(null);
    setSuccessMsg(null);

    try {
      const response = await postHeavy<{ status: string; message: string }>("/clipper/run", {
        niche: selectedNiche,
        url: targetUrl.trim() || undefined,
        search_count: searchCount,
        max_clips_per_video: maxClips,
        min_virality_score: minScore,
      });
      setSuccessMsg(response.message);
      // Immediately refresh list
      loadClips();
    } catch (err: any) {
      setError(err.message || "Failed to start clipper pipeline");
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="max-w-4xl space-y-12 animate-fade-in-up">
      {/* ── Header ──────────────────────────────────────────── */}
      <div className="flex items-center gap-4">
        <div className="flex items-center justify-center w-12 h-12 rounded-2xl bg-gradient-to-br from-status-cyan to-status-blue shadow-[0_0_28px_rgba(6,182,212,0.25)]">
          <Scissors className="w-6 h-6 text-white" />
        </div>
        <div>
          <h1 className="text-2xl font-display font-bold text-gradient-cool">
            Viral Clipper
          </h1>
          <p className="text-text-muted text-sm mt-0.5">
            Auto-discover trending videos and extract viral moments
          </p>
        </div>
      </div>

      {/* ── Clipper Settings Form ───────────────────────────── */}
      <div className="glass-panel p-6 sm:p-8">
        <form onSubmit={handleSubmit} className="space-y-6">
          <div className="space-y-4">
            <div>
              <label className="block text-sm font-medium text-text-secondary mb-1">
                Direct YouTube URL (Optional)
              </label>
              <input
                type="url"
                placeholder="https://www.youtube.com/watch?v=..."
                value={targetUrl}
                onChange={(e) => setTargetUrl(e.target.value)}
                className="w-full bg-bg-input border border-border-default rounded-xl px-4 py-3 text-text-primary focus:outline-none focus:border-accent-500 focus:ring-1 focus:ring-accent-500 transition-colors"
                disabled={isLoading}
              />
            </div>
            
            <div className="relative flex items-center gap-4 py-2">
              <div className="h-px bg-border-default flex-1"></div>
              <span className="text-xs text-text-muted font-medium uppercase tracking-wider">OR SEARCH BY</span>
              <div className="h-px bg-border-default flex-1"></div>
            </div>

            <div>
              <label className="block text-sm font-medium text-text-secondary mb-1">
                Target Niche
              </label>
              <select
                value={selectedNiche}
                onChange={(e) => setSelectedNiche(e.target.value)}
                className="w-full bg-bg-input border border-border-default rounded-xl px-4 py-3 text-text-primary focus:outline-none focus:border-accent-500 focus:ring-1 focus:ring-accent-500 transition-colors disabled:opacity-50"
                disabled={isLoading || targetUrl.length > 0}
              >
                {niches.map((n) => (
                  <option key={n} value={n}>
                    {n.charAt(0).toUpperCase() + n.slice(1)}
                  </option>
                ))}
              </select>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
              <div>
                <label className="block text-sm font-medium text-text-secondary mb-1">
                  Videos to Search
                </label>
                <input
                  type="number"
                  min="1"
                  max="15"
                  value={searchCount}
                  onChange={(e) => setSearchCount(parseInt(e.target.value))}
                  className="w-full bg-bg-input border border-border-default rounded-xl px-4 py-3 text-text-primary focus:outline-none focus:border-accent-500 transition-colors"
                  disabled={isLoading}
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-text-secondary mb-1">
                  Max Clips / Video
                </label>
                <input
                  type="number"
                  min="1"
                  max="10"
                  value={maxClips}
                  onChange={(e) => setMaxClips(parseInt(e.target.value))}
                  className="w-full bg-bg-input border border-border-default rounded-xl px-4 py-3 text-text-primary focus:outline-none focus:border-accent-500 transition-colors"
                  disabled={isLoading}
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-text-secondary mb-1">
                  Min Virality Score
                </label>
                <input
                  type="number"
                  min="1"
                  max="10"
                  value={minScore}
                  onChange={(e) => setMinScore(parseInt(e.target.value))}
                  className="w-full bg-bg-input border border-border-default rounded-xl px-4 py-3 text-text-primary focus:outline-none focus:border-accent-500 transition-colors"
                  disabled={isLoading}
                />
              </div>
            </div>
          </div>

          {error && (
            <div className="flex items-center gap-3 p-4 bg-status-red/10 border border-status-red/30 rounded-xl">
              <AlertCircle className="w-5 h-5 text-status-red shrink-0" />
              <p className="text-sm text-status-red">{error}</p>
            </div>
          )}

          {successMsg && (
            <div className="flex items-center gap-3 p-4 bg-status-green/10 border border-status-green/30 rounded-xl">
              <CheckCircle2 className="w-5 h-5 text-status-green shrink-0" />
              <p className="text-sm text-status-green">{successMsg}</p>
            </div>
          )}

          <div className="pt-2">
            <button
              type="submit"
              disabled={isLoading || (!selectedNiche && !targetUrl)}
              className="btn-primary w-full py-3 text-base"
            >
              {isLoading ? (
                <>
                  <Loader2 className="w-5 h-5 animate-spin" />
                  Starting Pipeline...
                </>
              ) : (
                <>
                  <Scissors className="w-5 h-5" />
                  Run Clipper Pipeline
                </>
              )}
            </button>
          </div>
        </form>
      </div>

      {/* ── Clips Library ───────────────────────────────────── */}
      <div className="space-y-6">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Video className="w-5 h-5 text-status-cyan" />
            <h2 className="text-xl font-display font-bold text-text-primary">
              Generated Clips Library
            </h2>
          </div>
          <button 
            onClick={loadClips}
            className="flex items-center gap-1.5 text-xs text-text-muted hover:text-text-primary transition-colors bg-bg-hover/30 border border-border-default/50 rounded-lg px-2.5 py-1.5"
          >
            <RefreshCw className="w-3.5 h-3.5" />
            Refresh
          </button>
        </div>

        {clips.length === 0 ? (
          <div className="glass-panel p-8 text-center text-text-muted">
            <FileVideo className="w-12 h-12 text-text-muted/40 mx-auto mb-3" />
            <p className="text-sm">No clips generated yet. Paste a link above to start!</p>
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            {clips.map((clip) => {
              const videoUrl = clip.play_url
                ? (process.env.NEXT_PUBLIC_API_URL?.replace(/\/+$/, "") ?? "http://localhost:8000") + clip.play_url
                : "";
              
              let tagList: string[] = [];
              try {
                tagList = JSON.parse(clip.tags);
              } catch (e) {
                tagList = [];
              }

              return (
                <div key={clip.id} className="glass-panel flex flex-col overflow-hidden group">
                  {/* Video Player Box */}
                  <div className="aspect-[9/16] max-h-[400px] w-full bg-black relative flex items-center justify-center overflow-hidden border-b border-border-default/30">
                    {clip.status === "ready" && videoUrl ? (
                      <video 
                        src={videoUrl} 
                        controls 
                        className="h-full w-full object-contain"
                        preload="metadata"
                      />
                    ) : clip.status === "error" ? (
                      <div className="text-center p-4">
                        <AlertCircle className="w-10 h-10 text-status-red mx-auto mb-2" />
                        <p className="text-xs text-status-red font-medium">Failed to process clip</p>
                        <p className="text-[10px] text-text-muted mt-1 max-w-[200px] mx-auto truncate">
                          {clip.error_message || "Unknown error during FFmpeg execution"}
                        </p>
                      </div>
                    ) : (
                      <div className="text-center space-y-3">
                        <Loader2 className="w-8 h-8 text-status-cyan animate-spin mx-auto" />
                        <p className="text-xs text-text-muted font-medium uppercase tracking-wider animate-pulse">
                          {clip.status === "processing" ? "Cropping & Encoding..." : "Queued in pipeline..."}
                        </p>
                      </div>
                    )}
                    
                    {/* Status Badge */}
                    <div className="absolute top-3 right-3">
                      <span className={`badge ${
                        clip.status === "ready" ? "badge-green" :
                        clip.status === "processing" ? "badge-blue" :
                        clip.status === "error" ? "badge-red" : "badge-amber"
                      } shadow-lg`}>
                        {clip.status}
                      </span>
                    </div>
                  </div>

                  {/* Metadata and Details */}
                  <div className="p-5 flex-1 flex flex-col justify-between space-y-4">
                    <div className="space-y-2">
                      <h3 className="font-display font-semibold text-text-primary text-base leading-snug line-clamp-2">
                        {clip.title || "Untitled Clip"}
                      </h3>
                      <p className="text-xs text-text-muted line-clamp-3 leading-relaxed">
                        {clip.description}
                      </p>
                    </div>

                    {tagList.length > 0 && (
                      <div className="flex flex-wrap gap-1">
                        {tagList.slice(0, 5).map((t) => (
                          <span key={t} className="text-[10px] bg-bg-hover text-text-secondary rounded px-1.5 py-0.5 border border-border-default/30 flex items-center gap-0.5">
                            <Tag className="w-2.5 h-2.5 text-text-muted" />
                            {t}
                          </span>
                        ))}
                      </div>
                    )}

                    {clip.status === "ready" && videoUrl && (
                      <a
                        href={videoUrl}
                        download
                        target="_blank"
                        rel="noopener noreferrer"
                        className="btn-primary py-2.5 text-sm justify-center flex items-center gap-2 mt-auto"
                      >
                        <Download className="w-4 h-4" />
                        Download MP4
                      </a>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}

