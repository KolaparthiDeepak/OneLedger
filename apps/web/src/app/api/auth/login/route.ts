import { NextResponse, type NextRequest } from "next/server";
import { API_URL, COOKIE_SECURE, SESSION_COOKIE, SESSION_MAX_AGE } from "@/lib/server/config";
import { forwardHeaders, originAllowed } from "@/lib/server/security";

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
  const upstream = await fetch(`${API_URL}/api/v1/auth/login`, { method: "POST", headers, body: JSON.stringify(body), cache: "no-store" });
  const data = (await upstream.json()) as { session_token?: string; mfa_required?: boolean; mfa_enrolled?: boolean; error?: unknown };
  if (!upstream.ok || !data.session_token) {
    return NextResponse.json({ error: data.error ?? { code: "LOGIN_FAILED", message: "Sign-in failed." } }, { status: upstream.status });
  }
  // The session token lives only in an HttpOnly cookie; it is never exposed to browser JavaScript.
  const res = NextResponse.json({ mfa_required: data.mfa_required, mfa_enrolled: data.mfa_enrolled });
  res.cookies.set(SESSION_COOKIE, data.session_token, {
    httpOnly: true,
    secure: COOKIE_SECURE,
    sameSite: "strict",
    path: "/",
    maxAge: SESSION_MAX_AGE,
  });
  res.headers.set("Cache-Control", "private, no-store");
  return res;
}
