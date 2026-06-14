"use client";

import React, { useState, useEffect } from "react";
import { Radio, Plus, Loader2, AlertCircle, CheckCircle2, Tv as Youtube, ShieldAlert, Key } from "lucide-react";
import { get, post } from "@/lib/api";

interface Channel {
  id: number;
  name: string;
  channel_id: string;
  target_niche: string;
  authenticated: boolean;
  created_at: string;
}

export default function ChannelsPage() {
  const [channels, setChannels] = useState<Channel[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Form state
  const [isAdding, setIsAdding] = useState(false);
  const [newName, setNewName] = useState("");
  const [newNiche, setNewNiche] = useState("");
  const [addError, setAddError] = useState<string | null>(null);
  const [addSuccess, setAddSuccess] = useState<string | null>(null);

  // Auth flow state
  const [authLoadingId, setAuthLoadingId] = useState<number | null>(null);
  const [authError, setAuthError] = useState<string | null>(null);
  const [authSuccess, setAuthSuccess] = useState<string | null>(null);

  const fetchChannels = async () => {
    setIsLoading(true);
    try {
      const data = await get<{ channels: Channel[] }>("/channels");
      setChannels(data.channels);
      setError(null);
    } catch (err: any) {
      setError(err.message || "Failed to fetch channels");
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    fetchChannels();
  }, []);

  const handleAddChannel = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newName || !newNiche) return;

    setIsAdding(true);
    setAddError(null);
    setAddSuccess(null);

    try {
      await post("/channels", { name: newName, target_niche: newNiche });
      setAddSuccess("Channel added successfully!");
      setNewName("");
      setNewNiche("");
      await fetchChannels(); // Refresh the list
    } catch (err: any) {
      setAddError(err.message || "Failed to add channel");
    } finally {
      setIsAdding(false);
    }
  };

  const handleAuthenticate = async (channelId: number) => {
    setAuthLoadingId(channelId);
    setAuthError(null);
    setAuthSuccess(null);
    try {
      const response = await post<{ status: string; message: string }>(`/channels/${channelId}/authenticate`);
      setAuthSuccess(response.message || "Authentication initiated! A browser window has opened on your host machine.");
      
      // Start polling/refreshing channel list in the background to catch the status change
      let attempts = 0;
      const interval = setInterval(async () => {
        attempts++;
        try {
          const res = await get<{ channels: Channel[] }>("/channels");
          const target = res.channels.find(c => c.id === channelId);
          if (target && target.authenticated) {
            setChannels(res.channels);
            setAuthSuccess("Channel successfully authenticated and sync'd!");
            clearInterval(interval);
          }
        } catch (e) {
          console.error("Error checking auth status:", e);
        }
        if (attempts > 10) clearInterval(interval);
      }, 5000);
    } catch (err: any) {
      setAuthError(err.message || "Failed to start authentication flow");
    } finally {
      setAuthLoadingId(null);
    }
  };

  return (
    <div className="max-w-4xl space-y-8 animate-fade-in-up">
      <div className="flex items-center gap-4">
        <div className="flex items-center justify-center w-12 h-12 rounded-2xl bg-gradient-to-br from-status-blue to-accent-500 shadow-[0_0_28px_rgba(59,130,246,0.25)]">
          <Radio className="w-6 h-6 text-white" />
        </div>
        <div>
          <h1 className="text-2xl font-display font-bold text-gradient-cool">
            YouTube Channels
          </h1>
          <p className="text-text-muted text-sm mt-0.5">
            Manage and authenticate your automated upload destinations
          </p>
        </div>
      </div>

      {/* Auth Feedbacks */}
      {(authError || authSuccess) && (
        <div className={`p-4 rounded-xl border flex items-center gap-3 animate-fade-in-up ${
          authSuccess ? 'bg-status-green/10 border-status-green/30' : 'bg-status-red/10 border-status-red/30'
        }`}>
          {authSuccess ? (
            <CheckCircle2 className="w-5 h-5 text-status-green shrink-0" />
          ) : (
            <AlertCircle className="w-5 h-5 text-status-red shrink-0" />
          )}
          <p className={`text-sm ${authSuccess ? 'text-status-green' : 'text-status-red'}`}>
            {authSuccess || authError}
          </p>
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
        {/* ── Channel List ──────────────────────────────────────── */}
        <div className="lg:col-span-2 space-y-4">
          {isLoading ? (
            <div className="glass-panel p-8 flex justify-center items-center h-48">
              <Loader2 className="w-8 h-8 text-accent-500 animate-spin" />
            </div>
          ) : error ? (
            <div className="glass-panel p-6 border-status-red/30 border">
              <div className="flex items-center gap-3">
                <AlertCircle className="w-5 h-5 text-status-red" />
                <p className="text-status-red">{error}</p>
              </div>
            </div>
          ) : channels.length === 0 ? (
            <div className="glass-panel p-12 text-center flex flex-col items-center justify-center border border-dashed border-border-default h-48">
              <Youtube className="w-10 h-10 text-text-muted mb-3 opacity-50" />
              <p className="text-text-secondary font-medium">No channels configured</p>
              <p className="text-text-muted text-sm mt-1">Add your first channel to start uploading</p>
            </div>
          ) : (
            <div className="space-y-3 stagger-children">
              {channels.map((channel) => (
                <div key={channel.id} className="glass-panel p-5 flex items-center justify-between group transition-all hover:border-border-accent">
                  <div className="flex items-center gap-4">
                    <div className="w-10 h-10 rounded-full bg-bg-input border border-border-subtle flex items-center justify-center">
                      <Youtube className={`w-5 h-5 ${channel.authenticated ? 'text-status-red' : 'text-text-muted'}`} />
                    </div>
                    <div>
                      <h3 className="text-text-primary font-semibold">{channel.name}</h3>
                      <div className="flex flex-col sm:flex-row sm:items-center gap-1 sm:gap-3 mt-1">
                        <p className="text-text-muted text-xs">
                          Niche: <span className="text-accent-400 font-medium">{channel.target_niche}</span>
                        </p>
                        <p className="text-[10px] text-text-muted">
                          ID: <span className="font-mono text-text-secondary">{channel.channel_id}</span>
                        </p>
                      </div>
                    </div>
                  </div>
                  <div className="text-right flex flex-col items-end gap-2">
                    {channel.authenticated ? (
                      <span className="badge badge-green">Authenticated</span>
                    ) : (
                      <span className="badge badge-amber flex items-center gap-1">
                        <ShieldAlert className="w-3 h-3 text-status-amber" />
                        Needs Auth
                      </span>
                    )}

                    {!channel.authenticated && (
                      <button
                        onClick={() => handleAuthenticate(channel.id)}
                        disabled={authLoadingId !== null}
                        className="btn-ghost text-xs py-1 px-2.5 bg-bg-hover/40 border-border-default flex items-center gap-1 text-accent-400 hover:text-white"
                      >
                        {authLoadingId === channel.id ? (
                          <Loader2 className="w-3 h-3 animate-spin" />
                        ) : (
                          <Key className="w-3 h-3" />
                        )}
                        Authenticate
                      </button>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* ── Add Channel Form ──────────────────────────────────── */}
        <div className="lg:col-span-1">
          <div className="glass-panel p-6 sticky top-24">
            <h2 className="text-lg font-display font-semibold text-text-primary mb-4 flex items-center gap-2">
              <Plus className="w-4 h-4 text-accent-500" />
              Add Channel
            </h2>
            
            <form onSubmit={handleAddChannel} className="space-y-4">
              <div>
                <label className="block text-sm font-medium text-text-secondary mb-1">
                  Channel Name
                </label>
                <input
                  type="text"
                  required
                  value={newName}
                  onChange={(e) => setNewName(e.target.value)}
                  placeholder="e.g. History Hub"
                  className="w-full bg-bg-input border border-border-default rounded-xl px-4 py-2.5 text-sm text-text-primary focus:outline-none focus:border-accent-500 transition-colors"
                  disabled={isAdding}
                />
              </div>

              <div>
                <label className="block text-sm font-medium text-text-secondary mb-1">
                  Target Niche
                </label>
                <input
                  type="text"
                  required
                  value={newNiche}
                  onChange={(e) => setNewNiche(e.target.value)}
                  placeholder="e.g. world_history"
                  className="w-full bg-bg-input border border-border-default rounded-xl px-4 py-2.5 text-sm text-text-primary focus:outline-none focus:border-accent-500 transition-colors"
                  disabled={isAdding}
                />
              </div>

              {addError && (
                <div className="flex items-center gap-2 p-3 bg-status-red/10 border border-status-red/30 rounded-lg">
                  <AlertCircle className="w-4 h-4 text-status-red shrink-0" />
                  <p className="text-xs text-status-red">{addError}</p>
                </div>
              )}

              {addSuccess && (
                <div className="flex items-center gap-2 p-3 bg-status-green/10 border border-status-green/30 rounded-lg">
                  <CheckCircle2 className="w-4 h-4 text-status-green shrink-0" />
                  <p className="text-xs text-status-green">{addSuccess}</p>
                </div>
              )}

              <button
                type="submit"
                disabled={isAdding || !newName || !newNiche}
                className="btn-primary w-full mt-2"
              >
                {isAdding ? (
                  <Loader2 className="w-4 h-4 animate-spin" />
                ) : (
                  "Save Channel"
                )}
              </button>
              
              <p className="text-[10px] text-text-muted mt-3 text-center leading-relaxed">
                Adding a channel registers it in SQLite. 
                Clicking <strong>Authenticate</strong> triggers the interactive OAuth2 login in your local browser to generate the offline refresh token.
              </p>
            </form>
          </div>
        </div>
      </div>
    </div>
  );
}
