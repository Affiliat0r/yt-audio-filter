import { NextResponse } from "next/server";
import { isAuthenticated } from "@/lib/auth";
import { KEYS, redis } from "@/lib/redis";
import type { WorkerHeartbeat } from "@/lib/types";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

/** Worker counts as online if it polled within this window. */
const ONLINE_WINDOW_MS = 90_000;

export async function GET() {
  if (!(await isAuthenticated())) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  const beat = await redis().get<WorkerHeartbeat>(KEYS.heartbeat);
  const online = !!beat && Date.now() - beat.at < ONLINE_WINDOW_MS;
  const queueDepth = await redis().llen(KEYS.queue);

  return NextResponse.json({ online, heartbeat: beat ?? null, queueDepth });
}
