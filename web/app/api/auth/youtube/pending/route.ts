import { NextResponse } from "next/server";
import { isAuthenticated } from "@/lib/auth";
import { listPendingAuth } from "@/lib/youtubeAuth";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

/**
 * Machines waiting for someone to sign in to YouTube for them.
 *
 * Drives the Studio's prompt. The `state` is withheld from the response — the
 * Authorise button only needs a `workerId`, and the value that authorises
 * Google's callback has no business in a page.
 */
export async function GET() {
  if (!(await isAuthenticated())) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }
  const pending = await listPendingAuth();
  return NextResponse.json({ pending }, {
    headers: { "cache-control": "no-store" },
  });
}
