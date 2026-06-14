"use client";

import React, { useState, useEffect } from "react";
import { Sparkles, Loader2, AlertCircle, CheckCircle2 } from "lucide-react";
import { get, postHeavy } from "@/lib/api";

interface ThemeConfig {
  label: string;
  voice: string;
  bgm_file: string;
}

export default function GeneratorPage() {
  const [themes, setThemes] = useState<Record<string, ThemeConfig>>({});
  const [selectedTheme, setSelectedTheme] = useState<string>("");
  const [customPrompt, setCustomPrompt] = useState<string>("");
  
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [successMsg, setSuccessMsg] = useState<string | null>(null);

  useEffect(() => {
    get<{ themes: Record<string, ThemeConfig> }>("/generator/themes")
      .then((data) => {
        setThemes(data.themes);
        const keys = Object.keys(data.themes);
        if (keys.length > 0) setSelectedTheme(keys[0]);
      })
      .catch((err) => console.error("Failed to fetch themes:", err));
  }, []);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setIsLoading(true);
    setError(null);
    setSuccessMsg(null);

    try {
      const response = await postHeavy<{ status: string; message: string }>("/generator/run", {
        theme: selectedTheme,
        custom_prompt: customPrompt || null,
      });
      setSuccessMsg(response.message);
      setCustomPrompt(""); // clear prompt on success
    } catch (err: any) {
      setError(err.message || "Failed to start generator pipeline");
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="max-w-3xl space-y-8 animate-fade-in-up">
      <div className="flex items-center gap-4">
        <div className="flex items-center justify-center w-12 h-12 rounded-2xl bg-gradient-to-br from-status-purple to-accent-500 shadow-[0_0_28px_rgba(168,85,247,0.25)]">
          <Sparkles className="w-6 h-6 text-white" />
        </div>
        <div>
          <h1 className="text-2xl font-display font-bold text-gradient-brand">
            AI Story Generator
          </h1>
          <p className="text-text-muted text-sm mt-0.5">
            Create completely original history and horror Shorts from scratch
          </p>
        </div>
      </div>

      <div className="glass-panel p-6 sm:p-8">
        <form onSubmit={handleSubmit} className="space-y-6">
          <div className="space-y-4">
            <div>
              <label className="block text-sm font-medium text-text-secondary mb-1">
                Story Theme
              </label>
              <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                {Object.entries(themes).map(([key, config]) => (
                  <button
                    key={key}
                    type="button"
                    onClick={() => setSelectedTheme(key)}
                    disabled={isLoading}
                    className={`
                      flex flex-col text-left p-4 rounded-xl border transition-all duration-200
                      ${
                        selectedTheme === key
                          ? "bg-accent-500/10 border-accent-500 shadow-[0_0_15px_rgba(139,92,246,0.15)]"
                          : "bg-bg-input border-border-default hover:border-border-accent hover:bg-bg-hover"
                      }
                    `}
                  >
                    <span className={`font-semibold ${selectedTheme === key ? "text-accent-400" : "text-text-primary"}`}>
                      {config.label}
                    </span>
                    <span className="text-xs text-text-muted mt-1 truncate">
                      {config.voice.split("-").pop()?.replace("Neural", "")} Voice
                    </span>
                  </button>
                ))}
              </div>
            </div>

            <div>
              <label className="block text-sm font-medium text-text-secondary mb-1">
                Custom Direction (Optional)
              </label>
              <textarea
                value={customPrompt}
                onChange={(e) => setCustomPrompt(e.target.value)}
                placeholder="E.g., Tell a story about the Library of Alexandria burning down, focusing on the lost knowledge..."
                rows={3}
                className="w-full bg-bg-input border border-border-default rounded-xl px-4 py-3 text-text-primary focus:outline-none focus:border-accent-500 focus:ring-1 focus:ring-accent-500 transition-colors resize-none"
                disabled={isLoading}
              />
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
              disabled={isLoading || !selectedTheme}
              className="btn-primary w-full py-3 text-base"
            >
              {isLoading ? (
                <>
                  <Loader2 className="w-5 h-5 animate-spin" />
                  Generating Story... (This takes 1-2 mins)
                </>
              ) : (
                <>
                  <Sparkles className="w-5 h-5" />
                  Generate Story Video
                </>
              )}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
