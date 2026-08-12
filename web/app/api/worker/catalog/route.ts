import { NextResponse } from "next/server";
import { checkWorkerToken } from "@/lib/auth";
import { putCatalog } from "@/lib/jobs";
import type { CatalogVideo } from "@/lib/types";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

/**
 * The worker owns `cache/cartoon_catalog.json`; it pushes the merged catalog
 * here on startup and after every refresh so Vercel can serve the gallery
 * without touching yt-dlp.
 */
export async function POST(req: Request) {
  if (!checkWorkerToken(req)) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  const body = (await req.json()) as {
    videos?: CatalogVideo[];
    workerId?: string;
  };
  if (!Array.isArray(body.videos)) {
    return NextResponse.json(
      { error: "videos must be an array" },
      { status: 400 }
    );
  }

  // Cache badges are stored against the reporting worker; the video list
  // itself is shared.
  await putCatalog(body.videos, body.workerId ?? null);
  return NextResponse.json({ ok: true, count: body.videos.length });
}
