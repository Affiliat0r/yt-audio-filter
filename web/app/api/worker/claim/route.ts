import { NextResponse } from "next/server";
import { checkWorkerToken } from "@/lib/auth";
import { claimNextJob, recordHeartbeat } from "@/lib/jobs";
import type { WorkerHeartbeat } from "@/lib/types";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

/**
 * The worker long-polls this. Each call also records a heartbeat, which is how
 * the UI knows which machines are online and can offer them as render targets.
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

  // Untagged workers (an un-upgraded install) can still claim untargeted jobs.
  const workerId = typeof beat.workerId === "string" ? beat.workerId : undefined;

  const job = await claimNextJob(workerId);

  // An idle worker polls with just its id. Writing a heartbeat that says the
  // same thing on every poll is what exhausted the Redis free tier, so it is
  // only recorded when the worker actually sends one (about once a minute).
  if (beat.hostname === undefined && beat.version === undefined) {
    return NextResponse.json({ job });
  }

  await recordHeartbeat({
    at: Date.now(),
    workerId: workerId ?? "legacy",
    hostname: beat.hostname ?? "unknown",
    gpu: beat.gpu ?? null,
    version: beat.version ?? "unknown",
    currentJobId: job?.id ?? null,
    // Absent on older workers; assume capable rather than hiding the feature.
    canRemoveMusic: beat.canRemoveMusic ?? true,
  });

  return NextResponse.json({ job });
}
