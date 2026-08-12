import { NextResponse } from "next/server";
import { checkWorkerToken } from "@/lib/auth";
import { updateProgress } from "@/lib/jobs";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST(req: Request) {
  if (!checkWorkerToken(req)) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  const body = (await req.json()) as {
    jobId?: string;
    stage?: string;
    percent?: number | null;
    logLines?: string[];
  };

  if (!body.jobId) {
    return NextResponse.json({ error: "jobId is required" }, { status: 400 });
  }

  const job = await updateProgress(body.jobId, {
    stage: body.stage,
    percent: body.percent,
    logLines: body.logLines,
  });
  if (!job) return NextResponse.json({ error: "Not found" }, { status: 404 });

  // Tell the worker to stop if the user cancelled mid-render.
  return NextResponse.json({ ok: true, cancelled: job.status === "cancelled" });
}
