import { NextResponse } from "next/server";
import { isAuthenticated } from "@/lib/auth";
import { requestUpload } from "@/lib/jobs";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

/**
 * Explicit, user-initiated YouTube upload of an already-rendered job.
 * Nothing uploads without this call — a render only ever lands on the
 * worker's disk until the user presses the button.
 */
export async function POST(
  _req: Request,
  { params }: { params: Promise<{ id: string }> }
) {
  if (!(await isAuthenticated())) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }
  const { id } = await params;
  const job = await requestUpload(id);
  if (!job) return NextResponse.json({ error: "Not found" }, { status: 404 });
  if (!job.uploadRequested) {
    return NextResponse.json(
      { error: "Job has no rendered output to upload" },
      { status: 409 }
    );
  }
  return NextResponse.json({ job });
}
