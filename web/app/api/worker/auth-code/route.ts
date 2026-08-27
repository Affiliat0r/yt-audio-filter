import { NextResponse } from "next/server";
import { checkWorkerToken } from "@/lib/auth";
import { takeAuthCode } from "@/lib/youtubeAuth";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

/**
 * The worker's poll for its authorisation code.
 *
 * Three outcomes, all reported plainly rather than as HTTP errors, because
 * "not yet" is the normal answer for the first few minutes:
 *
 *   pending — the consent screen has not been completed.
 *   ready   — here is the code, and it is now gone from Redis (single use).
 *   expired — no live request for that state; start again from the Studio.
 */
export async function GET(req: Request) {
  if (!checkWorkerToken(req)) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  const state = new URL(req.url).searchParams.get("state") ?? "";
  if (!state) {
    return NextResponse.json({ error: "state is required" }, { status: 400 });
  }

  const answer = await takeAuthCode(state);
  return NextResponse.json(answer, {
    headers: { "cache-control": "no-store" },
  });
}
