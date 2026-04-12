"""API key authentication for REST and gRPC.

Keys are loaded from the `VIO_API_KEYS` env var (comma-separated). Each key can
optionally carry a client label via `KEY:label` syntax (for logging).

If `VIO_API_KEYS` is empty, authentication is DISABLED (dev mode).
This is intentional for local development — production should always set it.

Usage:
  VIO_API_KEYS=viaplay_abc123:viaplay,tv2_xyz789:tv2

REST:   send as `Authorization: Bearer <key>` or `x-api-key: <key>` header.
gRPC:   send as `x-api-key` metadata entry.
"""

import os
from dataclasses import dataclass
from typing import Dict, Optional

from fastapi import Depends, HTTPException, Request


@dataclass
class ApiClient:
    key: str
    label: str


class ApiKeyRegistry:
    """Parses the VIO_API_KEYS env var into a lookup table."""

    def __init__(self):
        self._keys: Dict[str, ApiClient] = {}
        raw = os.getenv("VIO_API_KEYS", "").strip()
        if not raw:
            print("[auth] disabled (VIO_API_KEYS not set) — DEV MODE")
            return

        for entry in raw.split(","):
            entry = entry.strip()
            if not entry:
                continue
            if ":" in entry:
                key, label = entry.split(":", 1)
            else:
                key, label = entry, "unlabeled"
            self._keys[key.strip()] = ApiClient(key=key.strip(), label=label.strip())

        print(f"[auth] enabled — {len(self._keys)} key(s) loaded: "
              f"{[c.label for c in self._keys.values()]}")

    @property
    def enabled(self) -> bool:
        return len(self._keys) > 0

    def resolve(self, key: Optional[str]) -> Optional[ApiClient]:
        if not self.enabled:
            # Auth disabled → anonymous dev client
            return ApiClient(key="", label="dev")
        if not key:
            return None
        return self._keys.get(key.strip())


registry = ApiKeyRegistry()


# ─── FastAPI dependency ─────────────────────────────────────────────────

def require_api_key(request: Request) -> ApiClient:
    if not registry.enabled:
        return ApiClient(key="", label="dev")

    # Try both common headers
    key = request.headers.get("x-api-key")
    if not key:
        auth = request.headers.get("authorization", "")
        if auth.startswith("Bearer "):
            key = auth[7:]

    client = registry.resolve(key)
    if not client:
        raise HTTPException(401, "Invalid or missing API key")
    return client


# ─── gRPC interceptor ───────────────────────────────────────────────────

async def grpc_auth_interceptor(context, key_extractor):
    """Called from grpc servicer before handling each RPC."""
    if not registry.enabled:
        return ApiClient(key="", label="dev")

    metadata = dict(context.invocation_metadata())
    key = metadata.get("x-api-key")
    client = registry.resolve(key)
    if not client:
        await context.abort(16, "Unauthenticated (x-api-key required)")  # 16 = UNAUTHENTICATED
        return None
    return client
