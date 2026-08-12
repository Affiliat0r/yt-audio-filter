import { NextResponse } from "next/server";
import { checkWorkerToken } from "@/lib/auth";
import { claimNextJob } from "@/lib/jobs";
import { KEYS, redis } from "@/lib/redis";
import type { WorkerHeartbeat } from "@/lib/types";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

/**
 * The worker long-polls this. Each call also records a heartbeat so the UI can
 * show whether the render box is online.
 */
export async function POST(req: Request) {
  if (!checkWorkerToken(req)) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  let beat: Partial<WorkerHeartbeat> = {};
  try {
    beat = (await req.json()) as Partial<WorkerHeartbeat>;
  } catch {
    // Heartbeat body is optional.
  }

  const job = await claimNextJob();

  const heartbeat: WorkerHeartbeat = {
    at: Date.now(),
    hostname: beat.hostname ?? "unknown",
    gpu: beat.gpu ?? null,
    version: beat.version ?? "unknown",
    currentJobId: job?.id ?? null,
  };
  await redis().set(KEYS.heartbeat, heartbeat, { ex: 300 });

  return NextResponse.json({ job });
}
