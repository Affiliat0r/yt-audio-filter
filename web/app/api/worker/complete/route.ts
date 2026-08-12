import { NextResponse } from "next/server";
import { checkWorkerToken } from "@/lib/auth";
import { completeJob, failJob } from "@/lib/jobs";
import type { JobResult } from "@/lib/types";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST(req: Request) {
  if (!checkWorkerToken(req)) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  const body = (await req.json()) as {
    jobId?: string;
    ok?: boolean;
    result?: JobResult;
    error?: string;
    errorDetails?: string | null;
  };

  if (!body.jobId) {
    return NextResponse.json({ error: "jobId is required" }, { status: 400 });
  }

  const job = body.ok
    ? await completeJob(body.jobId, body.result ?? {})
    : await failJob(
        body.jobId,
        body.error ?? "Worker reported an unspecified failure",
        body.errorDetails
      );

  if (!job) return NextResponse.json({ error: "Not found" }, { status: 404 });
  return NextResponse.json({ job });
}
