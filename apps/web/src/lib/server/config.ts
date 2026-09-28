import "server-only";

export const API_URL = (process.env.ONELEDGER_API_INTERNAL_URL ?? "http://localhost:8000").replace(/\/$/, "");
export const BFF_SECRET = process.env.BFF_SHARED_SECRET ?? "";
export const COOKIE_SECURE = process.env.COOKIE_SECURE === "true";
export const SESSION_COOKIE = COOKIE_SECURE ? "__Host-ol_session" : "ol_session";
export const SESSION_MAX_AGE = Number(process.env.SESSION_TTL_HOURS ?? "12") * 3600;

/** Trusted origins for state-changing requests (CSRF defence in addition to SameSite=Strict). */
export function allowedOrigins(): string[] {
  return (process.env.ALLOWED_ORIGINS ?? process.env.APP_BASE_URL ?? "http://localhost:3000")
    .split(",")
    .map((o) => o.trim())
    .filter(Boolean);
}
