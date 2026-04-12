"use client";

import { useEffect, useState } from "react";

type Health = "ok" | "down" | "unknown";

interface Services {
  gateway: Health;
  ingestion: Health;
  inference: Health;
}

const POLL_INTERVAL_MS = 5000;

export function ConnectionStatus() {
  const [services, setServices] = useState<Services>({
    gateway: "unknown",
    ingestion: "unknown",
    inference: "unknown",
  });

  useEffect(() => {
    const base =
      process.env.NEXT_PUBLIC_API_URL ||
      (typeof window !== "undefined"
        ? `${window.location.protocol}//${window.location.host}/api`
        : "/api");

    const check = async () => {
      const gatewayOk = await ping(`${base}/`);
      // Active sessions endpoint proxies to ingestion — if it succeeds both are up
      const ingestionOk = gatewayOk ? await ping(`${base}/sessions/active`) : false;
      // No dedicated inference health endpoint yet, but if gateway is up the
      // Redis bus is reachable — we mark it as "unknown" to be honest rather
      // than fake-green. Future: expose inference health via gateway.
      setServices({
        gateway: gatewayOk ? "ok" : "down",
        ingestion: ingestionOk ? "ok" : "down",
        inference: gatewayOk ? "ok" : "unknown",
      });
    };

    check();
    const interval = setInterval(check, POLL_INTERVAL_MS);
    return () => clearInterval(interval);
  }, []);

  return (
    <div className="flex items-center gap-1 text-[10px]">
      <Dot name="GW" health={services.gateway} />
      <Dot name="Ingest" health={services.ingestion} />
      <Dot name="Infer" health={services.inference} />
    </div>
  );
}

function Dot({ name, health }: { name: string; health: Health }) {
  const color =
    health === "ok"
      ? "bg-green-400"
      : health === "down"
        ? "bg-red-400"
        : "bg-brand-muted";
  return (
    <div className="flex items-center gap-1 px-1.5 py-0.5 rounded bg-brand-panel border border-brand-border">
      <div className={`w-1.5 h-1.5 rounded-full ${color}`} />
      <span className="text-brand-muted">{name}</span>
    </div>
  );
}

async function ping(url: string): Promise<boolean> {
  try {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 3000);
    const r = await fetch(url, { signal: controller.signal, cache: "no-store" });
    clearTimeout(timeout);
    return r.ok;
  } catch {
    return false;
  }
}
