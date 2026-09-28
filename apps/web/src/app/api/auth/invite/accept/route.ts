import { NextResponse, type NextRequest } from "next/server";
import { API_URL, COOKIE_SECURE, SESSION_COOKIE, SESSION_MAX_AGE } from "@/lib/server/config";
import { forwardHeaders, originAllowed } from "@/lib/server/security";

/** Create the invited person's ledger and sign them in (session token stays in an HttpOnly cookie). */
export async function POST(req: NextRequest) {
  if (!originAllowed(req)) {
    return NextResponse.json({ error: { code: "ORIGIN_REJECTED", message: "Cross-site request rejected." } }, { status: 403 });
  }
  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: { code: "VALIDATION_FAILED", message: "Invalid request." } }, { status: 422 });
  }
  const headers = forwardHeaders(req);
  headers.set("content-type", "application/json");
  const upstream = await fetch(`${API_URL}/api/v1/invites/accept`, { method: "POST", headers, body: JSON.stringify(body), cache: "no-store" });
  const data = (await upstream.json()) as { session_token?: string; error?: unknown };
  if (!upstream.ok || !data.session_token) {
    return NextResponse.json({ error: data.error ?? { code: "INVITE_FAILED", message: "Could not create your ledger." } }, { status: upstream.status });
  }
  const res = NextResponse.json({ ok: true }, { status: 201 });
  res.cookies.set(SESSION_COOKIE, data.session_token, { httpOnly: true, secure: COOKIE_SECURE, sameSite: "strict", path: "/", maxAge: SESSION_MAX_AGE });
  res.headers.set("Cache-Control", "private, no-store");
  return res;
}
