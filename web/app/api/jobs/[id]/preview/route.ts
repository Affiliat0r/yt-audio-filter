import { NextResponse } from "next/server";
import { isAuthenticated } from "@/lib/auth";
import { getJob } from "@/lib/jobs";
import { presignPreviewUrl } from "@/lib/blob";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

/**
 * Session-gated redirect to a short-lived presigned URL for a job's rendered
 * MP4. The blob store is private, so this route is the only way to view a
 * render — signing in to the Studio is a precondition for playback.
 *
 * `?download=1` swaps the inline player for a save-to-disk response.
 */
export async function GET(
  req: Request,
  { params }: { params: Promise<{ id: string }> }
) {
  if (!(await isAuthenticated())) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  const { id } = await params;
  const job = await getJob(id);
  if (!job) return NextResponse.json({ error: "Not found" }, { status: 404 });

  const pathname = job.result?.blobPathname;
  if (!pathname) {
    return NextResponse.json(
      { error: "This job has no stored render" },
      { status: 404 }
    );
  }

  let signed: string;
  try {
    signed = await presignPreviewUrl(pathname);
  } catch (e) {
    return NextResponse.json(
      { error: e instanceof Error ? e.message : "Could not sign the blob URL" },
      { status: 500 }
    );
  }

  const wantsDownload = new URL(req.url).searchParams.get("download") === "1";
  if (wantsDownload) signed += (signed.includes("?") ? "&" : "?") + "download=1";

  // 302 rather than proxying the bytes: a render is hundreds of MB and would
  // blow past the function response limit and duration.
  return NextResponse.redirect(signed, 302);
}
