import { NextResponse } from "next/server";
import { isAuthenticated } from "@/lib/auth";
import { createJob, listJobs } from "@/lib/jobs";
import type { JobInput } from "@/lib/types";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const KINDS = new Set(["surah", "ayah", "music_removal", "search"]);

/** Reject malformed input before it reaches the worker. */
function validate(input: JobInput): string | null {
  if (!input || typeof input !== "object") return "Missing job input";
  if (!KINDS.has(input.kind)) return `Unknown job kind: ${String(input.kind)}`;

  switch (input.kind) {
    case "surah":
      if (!Array.isArray(input.surahNumbers) || input.surahNumbers.length === 0)
        return "Select at least one surah";
      if (input.surahNumbers.some((n) => !Number.isInteger(n) || n < 1 || n > 114))
        return "Surah numbers must be 1-114";
      if (input.surahNumbers.length > 500)
        return "That expands to more than 500 segments; reduce the repeats";
      if (!input.reciterSlug) return "Pick a reciter";
      if (!input.visual?.videoId) return "Pick a visual";
      return null;
    case "ayah":
      if (!Array.isArray(input.ranges) || input.ranges.length === 0)
        return "Add at least one ayah range";
      for (const r of input.ranges) {
        if (!Number.isInteger(r.surah) || r.surah < 1 || r.surah > 114)
          return "Ayah range has an invalid surah";
        if (r.start < 1 || r.end < r.start) return "Ayah range is inverted";
        if (r.repeats < 1 || r.repeats > 99) return "Repeats must be 1-99";
      }
      if (!input.reciterSlug) return "Pick a reciter";
      if (!input.visual?.videoId) return "Pick a visual";
      return null;
    case "music_removal":
      if (!input.visual?.videoId) return "Pick a video";
      return null;
    case "search":
      if (!input.query?.trim()) return "Enter a search term";
      return null;
  }
}

export async function POST(req: Request) {
  if (!(await isAuthenticated())) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  let input: JobInput;
  try {
    input = (await req.json()) as JobInput;
  } catch {
    return NextResponse.json({ error: "Invalid JSON body" }, { status: 400 });
  }

  const problem = validate(input);
  if (problem) return NextResponse.json({ error: problem }, { status: 400 });

  const job = await createJob(input);
  return NextResponse.json({ job }, { status: 201 });
}

export async function GET() {
  if (!(await isAuthenticated())) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }
  const jobs = await listJobs(20);
  return NextResponse.json({ jobs });
}
