"use client";

import { useState } from "react";

interface Preset {
  label: string;
  url: string;
  badge: string;
}

const PRESETS: Preset[] = [
  {
    label: "Bar vs PSG (UCL)",
    url: "https://firebasestorage.googleapis.com/v0/b/tipio-1ec97.appspot.com/o/bar.v.psg.1.ucl.01.10.2025.fullmatchsports.com.1080p.mp4?alt=media&token=593ce8a1-0462-4c37-98c3-e399f25e3853",
    badge: "MP4",
  },
  {
    label: "Apple HLS Test",
    url: "https://devstreaming-cdn.apple.com/videos/streaming/examples/bipbop_adv_example_ts/master.m3u8",
    badge: "HLS",
  },
  {
    label: "Custom URL...",
    url: "",
    badge: "",
  },
];

interface PresetUrlPickerProps {
  value: string;
  onChange: (url: string) => void;
}

export function PresetUrlPicker({ value, onChange }: PresetUrlPickerProps) {
  const [custom, setCustom] = useState(false);
  const [customUrl, setCustomUrl] = useState(value);

  const handlePreset = (p: Preset) => {
    if (p.label === "Custom URL...") {
      setCustom(true);
      return;
    }
    setCustom(false);
    setCustomUrl(p.url);
    onChange(p.url);
  };

  return (
    <div className="flex-1 flex items-center gap-2">
      <div className="flex gap-1 flex-wrap">
        {PRESETS.map((p) => (
          <button
            key={p.label}
            onClick={() => handlePreset(p)}
            className={`px-2.5 py-1 rounded text-[11px] font-medium transition-colors flex items-center gap-1.5 ${
              !custom && customUrl === p.url
                ? "bg-brand-primary/20 text-brand-primary border border-brand-primary/40"
                : "bg-brand-panel border border-brand-border text-brand-muted hover:text-white"
            }`}
          >
            {p.badge && (
              <span className="text-[8px] px-1 py-0.5 rounded bg-white/10">
                {p.badge}
              </span>
            )}
            {p.label}
          </button>
        ))}
      </div>

      {custom && (
        <input
          type="text"
          value={customUrl}
          onChange={(e) => {
            setCustomUrl(e.target.value);
            onChange(e.target.value);
          }}
          placeholder="srt://... rtmp://... https://...m3u8 or MP4 URL"
          className="flex-1 bg-brand-panel/50 border border-brand-border rounded py-1 px-3 text-xs text-white placeholder-brand-muted focus:outline-none focus:border-brand-primary/50"
        />
      )}
    </div>
  );
}
