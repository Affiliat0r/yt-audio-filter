import { NextResponse } from "next/server";
import { checkWorkerToken } from "@/lib/auth";
import { createAuthRequest, validateAuthRequest } from "@/lib/youtubeAuth";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

/**
 * A worker announcing that it needs a human to sign in to YouTube for it.
 *
 * It sends the *public* half of a PKCE exchange — a random `state` and the
 * SHA-256 challenge — and keeps the verifier. The Studio can therefore relay
 * an authorisation code but never redeem one.
 *
 * Answers with the URL to open, because only the Studio knows its own public
 * origin; the worker puts that URL straight into the job's progress log.
 */
export async function POST(req: Request) {
  if (!checkWorkerToken(req)) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  let body: Record<string, unknown>;
  try {
    body = (await req.json()) as Record<string, unknown>;
  } catch {
    return NextResponse.json({ error: "Invalid JSON body" }, { status: 400 });
  }

  const problem = validateAuthRequest(body);
  if (problem) return NextResponse.json({ error: problem }, { status: 400 });

  const record = await createAuthRequest({
    workerId: String(body.workerId).trim(),
    hostname: String(body.hostname).trim(),
    state: String(body.state),
    codeChallenge: String(body.codeChallenge),
  });

  const authorizeUrl = new URL("/api/auth/youtube/start", req.url);
  authorizeUrl.searchParams.set("workerId", record.workerId);

  return NextResponse.json({
    ok: true,
    authorizeUrl: authorizeUrl.toString(),
    expiresAt: record.expiresAt,
  });
}
