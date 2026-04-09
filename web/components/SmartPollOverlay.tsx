"use client";

import { useState, useEffect, useRef, useCallback } from "react";
import type { SmartPoll } from "@/types/events";

interface SmartPollOverlayProps {
  poll: SmartPoll | null;
  onDismiss: () => void;
}

function generateResults(options: string[], votedOption?: string): Record<string, number> {
  const results: Record<string, number> = {};
  let remaining = 100;

  for (let i = 0; i < options.length; i++) {
    if (i === options.length - 1) {
      results[options[i]] = remaining;
    } else {
      const isBiased = options[i] === votedOption;
      const min = isBiased ? 40 : 10;
      const max = isBiased ? 70 : Math.floor(remaining / 2);
      const value = Math.floor(Math.random() * (max - min + 1)) + min;
      results[options[i]] = Math.min(value, remaining - (options.length - i - 1) * 5);
      remaining -= results[options[i]];
    }
  }
  return results;
}

export function SmartPollOverlay({ poll, onDismiss }: SmartPollOverlayProps) {
  const [visible, setVisible] = useState(false);
  const [voted, setVoted] = useState<string | null>(null);
  const [results, setResults] = useState<Record<string, number>>({});
  const [expired, setExpired] = useState(false);
  const [countdownPct, setCountdownPct] = useState(100);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const dismissTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const startTimeRef = useRef(0);

  const cleanup = useCallback(() => {
    if (timerRef.current) clearInterval(timerRef.current);
    if (dismissTimerRef.current) clearTimeout(dismissTimerRef.current);
  }, []);

  const dismiss = useCallback(() => {
    setVisible(false);
    cleanup();
    setTimeout(() => {
      setVoted(null);
      setResults({});
      setExpired(false);
      setCountdownPct(100);
      onDismiss();
    }, 300);
  }, [onDismiss, cleanup]);

  // Show results and auto-dismiss after 3 seconds
  const showResults = useCallback(
    (opts: string[], votedOpt?: string) => {
      cleanup();
      const res = generateResults(opts, votedOpt);
      setResults(res);
      dismissTimerRef.current = setTimeout(dismiss, 3000);
    },
    [cleanup, dismiss],
  );

  // Start poll when it changes
  useEffect(() => {
    if (!poll) return;
    setVisible(true);
    setVoted(null);
    setResults({});
    setExpired(false);
    setCountdownPct(100);
    startTimeRef.current = Date.now();

    const duration = poll.duration * 1000;
    timerRef.current = setInterval(() => {
      const elapsed = Date.now() - startTimeRef.current;
      const pct = Math.max(0, 100 - (elapsed / duration) * 100);
      setCountdownPct(pct);
      if (pct <= 0) {
        setExpired(true);
        showResults(poll.options);
      }
    }, 50);

    return cleanup;
  }, [poll, cleanup, showResults]);

  const handleVote = (option: string) => {
    if (voted || expired || !poll) return;
    setVoted(option);
    showResults(poll.options, option);
  };

  if (!poll) return null;

  const showingResults = Object.keys(results).length > 0;

  return (
    <div
      className={`absolute bottom-24 right-4 z-30 w-72 transition-all duration-300 ${
        visible ? "translate-x-0 opacity-100" : "translate-x-full opacity-0"
      }`}
    >
      <div className="bg-black/85 backdrop-blur-lg border border-brand-poll/40 rounded-xl overflow-hidden shadow-2xl">
        {/* Header */}
        <div className="px-4 pt-3 pb-2 border-b border-brand-poll/20">
          <div className="flex items-center gap-2 mb-1">
            <div className="w-2 h-2 rounded-full bg-brand-poll animate-pulse" />
            <span className="text-[10px] uppercase tracking-wider text-brand-poll font-semibold">
              Live Poll
            </span>
          </div>
          <p className="text-sm font-semibold text-white">{poll.question}</p>
        </div>

        {/* Options */}
        <div className="px-4 py-3 space-y-2">
          {poll.options.map((option) => {
            const pct = results[option] ?? 0;
            const isVoted = voted === option;

            return (
              <button
                key={option}
                onClick={() => handleVote(option)}
                disabled={showingResults}
                className={`w-full relative rounded-lg border text-left text-sm font-medium px-3 py-2 transition-all ${
                  showingResults
                    ? isVoted
                      ? "border-brand-poll/50 text-white"
                      : "border-white/10 text-brand-muted"
                    : "border-brand-poll/30 text-white hover:bg-brand-poll/20 hover:border-brand-poll/50 cursor-pointer"
                }`}
              >
                {/* Result bar */}
                {showingResults && (
                  <div
                    className={`absolute inset-0 rounded-lg transition-all duration-500 ${
                      isVoted ? "bg-brand-poll/25" : "bg-white/5"
                    }`}
                    style={{ width: `${pct}%` }}
                  />
                )}
                <span className="relative z-10 flex justify-between">
                  <span>{option}</span>
                  {showingResults && (
                    <span className={`text-xs ${isVoted ? "text-brand-poll" : "text-brand-muted"}`}>
                      {pct}%
                    </span>
                  )}
                </span>
              </button>
            );
          })}
        </div>

        {/* Countdown bar */}
        {!showingResults && (
          <div className="h-1 bg-white/5">
            <div
              className="h-full bg-brand-poll transition-all ease-linear"
              style={{
                width: `${countdownPct}%`,
                transitionDuration: "50ms",
              }}
            />
          </div>
        )}
      </div>
    </div>
  );
}
