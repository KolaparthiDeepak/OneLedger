import { NextResponse, type NextRequest } from "next/server";
import { API_URL } from "@/lib/server/config";
import { forwardHeaders, originAllowed } from "@/lib/server/security";

/** Is this invite link still usable? (Public: the person opening it is not signed in.) */
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
  const upstream = await fetch(`${API_URL}/api/v1/invites/check`, { method: "POST", headers, body: JSON.stringify(body), cache: "no-store" });
  const res = NextResponse.json(await upstream.json(), { status: upstream.status });
  res.headers.set("Cache-Control", "private, no-store");
  return res;
}
