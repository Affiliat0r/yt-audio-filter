import { NextResponse } from "next/server";
import { isAuthenticated } from "@/lib/auth";
import { getCatalog } from "@/lib/jobs";
import channelsData from "@/data/channels.json";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET() {
  if (!(await isAuthenticated())) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  const { videos, updatedAt } = await getCatalog();
  return NextResponse.json({
    videos,
    updatedAt,
    channels: channelsData.channels,
  });
}
