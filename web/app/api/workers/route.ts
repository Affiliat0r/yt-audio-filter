import { NextResponse } from "next/server";
import { isAuthenticated } from "@/lib/auth";
import { listWorkers } from "@/lib/jobs";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

/**
 * Every machine that has polled recently. The Studio pairs this with the
 * worker's own localhost discovery endpoint to work out which of them is the
 * machine the browser is running on.
 */
export async function GET() {
  if (!(await isAuthenticated())) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }
  const workers = await listWorkers();
  return NextResponse.json({ workers });
}
