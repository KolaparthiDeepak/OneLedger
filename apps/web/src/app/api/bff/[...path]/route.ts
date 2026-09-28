import { NextResponse, type NextRequest } from "next/server";
import { API_URL, SESSION_COOKIE } from "@/lib/server/config";
import { forwardHeaders, originAllowed } from "@/lib/server/security";

/** Approved API route prefixes. This is not a general proxy: anything else is rejected. */
const ALLOWED = [
  "me", "auth/mfa", "auth/sessions", "accounts", "transactions", "categories", "rules", "merchants", "tags",
  "review", "transfers", "relations", "imports", "analytics", "invites",
  "cards", "loans", "investments", "recurring", "budgets", "goals", "forecast", "anomalies", "tokens", "audit",
  "export", "ai", "jobs",
];

const PASS_RESPONSE_HEADERS = ["content-type", "content-disposition", "x-request-id"];

async function handle(req: NextRequest, ctx: { params: Promise<{ path: string[] }> }) {
  const { path } = await ctx.params;
  const joined = path.map(encodeURIComponent).join("/");
  if (path.some((p) => p === ".." || p === "." || p === "") || !ALLOWED.some((a) => joined === a || joined.startsWith(`${a}/`))) {
    return NextResponse.json({ error: { code: "NOT_FOUND", message: "Not found." } }, { status: 404 });
  }
  if (!originAllowed(req)) {
    return NextResponse.json({ error: { code: "ORIGIN_REJECTED", message: "Cross-site request rejected." } }, { status: 403 });
  }
  const token = req.cookies.get(SESSION_COOKIE)?.value;
  if (!token) {
    return NextResponse.json({ error: { code: "UNAUTHENTICATED", message: "Sign in again." } }, { status: 401 });
  }
  const headers = forwardHeaders(req, token);
  const contentType = req.headers.get("content-type");
  if (contentType) headers.set("content-type", contentType);
  const init: RequestInit & { duplex?: string } = { method: req.method, headers, cache: "no-store" };
  if (!["GET", "HEAD"].includes(req.method)) {
    init.body = req.body;
    init.duplex = "half";
  }
  const upstream = await fetch(`${API_URL}/api/v1/${joined}${req.nextUrl.search}`, init);
  const out = new Headers({ "Cache-Control": "private, no-store" });
  for (const h of PASS_RESPONSE_HEADERS) {
    const v = upstream.headers.get(h);
    if (v) out.set(h, v);
  }
  const res = new NextResponse(upstream.body, { status: upstream.status, headers: out });
  if (upstream.status === 401) res.cookies.delete(SESSION_COOKIE);
  return res;
}

export const GET = handle;
export const POST = handle;
export const PUT = handle;
export const PATCH = handle;
export const DELETE = handle;
