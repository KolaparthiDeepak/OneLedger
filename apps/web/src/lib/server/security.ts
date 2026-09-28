import "server-only";
import type { NextRequest } from "next/server";
import { allowedOrigins, BFF_SECRET } from "./config";

const SAFE = new Set(["GET", "HEAD", "OPTIONS"]);

/** Cookie-authenticated mutations must come from our own origin. */
export function originAllowed(req: NextRequest): boolean {
  if (SAFE.has(req.method)) return true;
  const origin = req.headers.get("origin");
  if (!origin) return false;
  return allowedOrigins().includes(origin);
}

export function forwardHeaders(req: NextRequest, token?: string): Headers {
  const h = new Headers();
  if (token) h.set("authorization", `Bearer ${token}`);
  const rid = req.headers.get("x-request-id");
  if (rid && /^[A-Za-z0-9-]{1,64}$/.test(rid)) h.set("x-request-id", rid);
  const ua = req.headers.get("user-agent");
  if (ua) h.set("user-agent", ua.slice(0, 200));
  if (BFF_SECRET) {
    h.set("x-oneledger-bff", BFF_SECRET);
    const ip = req.headers.get("x-forwarded-for")?.split(",")[0]?.trim() ?? "";
    if (ip) h.set("x-oneledger-client-ip", ip.slice(0, 64));
  }
  const idem = req.headers.get("idempotency-key");
  if (idem && idem.length <= 128) h.set("idempotency-key", idem);
  return h;
}
