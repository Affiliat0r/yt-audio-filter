import { NextResponse } from "next/server";
import { isAuthenticated } from "@/lib/auth";
import { getCatalog } from "@/lib/jobs";
import channelsData from "@/data/channels.json";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(req: Request) {
  if (!(await isAuthenticated())) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  // Badges describe one machine's disk, so they follow the render target.
  const workerId = new URL(req.url).searchParams.get("workerId");
  const { videos, updatedAt } = await getCatalog(workerId);
  return NextResponse.json({
    videos,
    updatedAt,
    channels: channelsData.channels,
  });
}
