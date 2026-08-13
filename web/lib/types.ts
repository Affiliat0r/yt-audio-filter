/**
 * Shared job contract between the Vercel frontend and the Python worker.
 *
 * The worker mirrors these shapes in `worker/contract.py`. Any change here
 * MUST be mirrored there — the two sides only ever exchange JSON matching
 * these types.
 */

export type JobKind = "surah" | "ayah" | "music_removal" | "search" | "probe";

export type JobStatus =
  | "queued"
  | "claimed"
  | "running"
  | "done"
  | "error"
  | "cancelled";

/** Mirrors `cartoon_catalog.CatalogVideo`. */
export interface CatalogVideo {
  videoId: string;
  url: string;
  title: string;
  duration: number;
  viewCount: number;
  uploadDate: string;
  thumbnailUrl: string;
  channelSlug: string;
  /** "new" | "downloaded" | "upscaled" — worker-reported cache state. */
  cacheState?: "new" | "downloaded" | "upscaled";
}

/** Mirrors `ayah_repeater.AyahRange`. */
export interface AyahRangeSpec {
  surah: number;
  start: number;
  end: number;
  repeats: number;
  gapSeconds: number;
}

/** Settings that live in the old Streamlit sidebar. */
export interface RenderSettings {
  presetSlug: string;
  burnSubtitles: boolean;
  upscale: boolean;
  metadataPath: string;
  playlistId: string | null;
}

export interface SurahJobInput {
  kind: "surah";
  /** Already repeat-expanded, in play order. */
  surahNumbers: number[];
  reciterSlug: string;
  visual: CatalogVideo;
  settings: RenderSettings;
}

export interface AyahJobInput {
  kind: "ayah";
  ranges: AyahRangeSpec[];
  reciterSlug: string;
  visual: CatalogVideo;
  settings: RenderSettings;
}

export interface MusicRemovalJobInput {
  kind: "music_removal";
  visual: CatalogVideo;
  privacy: "private" | "unlisted" | "public";
  playlistId: string | null;
}

export interface SearchJobInput {
  kind: "search";
  query: string;
  maxResults: number;
}

/**
 * How good the source visual actually is.
 * kind "measured": ffprobe on the file already in the worker's cache — ground truth.
 * kind "listed": yt-dlp format listing for a not-yet-downloaded video. YouTube
 *   lists formats SABR may never deliver (protected videos usually deliver
 *   format 18 = 360p), so listed numbers are an optimistic upper bound.
 * "We don't know yet" = the field is absent (no probe has completed), OR
 * kind "listed" with height === null (probe ran but extraction failed).
 */
export interface SourceQuality {
  kind: "measured" | "listed";
  width: number | null;
  height: number | null;
  fps: number | null;
  codec: string | null;
  probedAt: number;
}

export interface ProbeJobInput {
  kind: "probe";
  visual: CatalogVideo;
}

export type JobInput =
  | SurahJobInput
  | AyahJobInput
  | MusicRemovalJobInput
  | SearchJobInput
  | ProbeJobInput;

/**
 * Real-ESRGAN scale factor. Fixed at 2 because the worker always runs
 * `realesr-animevideov3-x2` (`upscale.DEFAULT_MODEL`), so a 360p source tops
 * out at 720p of genuine detail no matter what the render preset asks for.
 */
export const UPSCALE_FACTOR = 2;

export interface JobProgress {
  /** Human-readable current stage, e.g. "Rendering overlay". */
  stage: string;
  /** 0-100, or null when the stage reports no percentage. */
  percent: number | null;
  /** Rolling tail of worker log lines (most recent last, capped). */
  log: string[];
  updatedAt: number;
}

export interface JobResult {
  /**
   * Raw Vercel Blob URL of the rendered MP4. The store is private, so this is
   * NOT directly playable — it 403s without a signature. Kept for debugging
   * and as the "a render exists" flag; the browser uses
   * `/api/jobs/:id/preview` instead.
   */
  blobUrl?: string;
  /** Blob pathname, used to mint presigned preview URLs. */
  blobPathname?: string;
  fileName?: string;
  sizeBytes?: number;
  /** Set when the job also uploaded to YouTube. */
  youtubeVideoId?: string;
  /** Populated for `kind: "search"` jobs. */
  searchResults?: CatalogVideo[];
  /** Populated for `kind: "probe"` jobs. */
  sourceQuality?: SourceQuality;
}

export interface Job {
  id: string;
  kind: JobKind;
  status: JobStatus;
  /**
   * Restricts this job to one worker. Set when the browser detects a worker
   * running on the same machine, so work stays on the laptop you are sitting
   * at instead of being grabbed by whichever worker polls first. `null` means
   * any worker may claim it.
   */
  targetWorkerId: string | null;
  /** Worker that actually claimed it, once claimed. */
  claimedBy: string | null;
  input: JobInput;
  progress: JobProgress;
  result: JobResult | null;
  error: string | null;
  /** Extra detail line for OverlayError-style failures. */
  errorDetails: string | null;
  createdAt: number;
  startedAt: number | null;
  finishedAt: number | null;
  /** Set once the worker has requested upload of an already-rendered job. */
  uploadRequested: boolean;
}

export interface WorkerHeartbeat {
  at: number;
  /** Stable per-machine id. The browser matches this against what the local
   *  discovery endpoint reports to decide which worker is "this machine". */
  workerId: string;
  hostname: string;
  gpu: string | null;
  version: string;
  /** Job id the worker is currently executing, if any. */
  currentJobId: string | null;
  /**
   * False on a light install with no Demucs/PyTorch. The UI uses this to grey
   * out music removal rather than letting a job be queued that can only fail.
   * Optional so an older worker (which never sends it) is assumed capable.
   */
  canRemoveMusic?: boolean;
}

/** What `/api/workers` returns for the target picker. */
export interface WorkerSummary extends WorkerHeartbeat {
  online: boolean;
}

/** Port the worker's local discovery endpoint listens on. */
export const DISCOVERY_PORT = 7717;

/** A short-lived label for a job, used in listings. */
export function jobLabel(job: Job): string {
  switch (job.input.kind) {
    case "surah":
      return `${job.input.surahNumbers.length} surah segment(s)`;
    case "ayah":
      return `${job.input.ranges.length} ayah range(s)`;
    case "music_removal":
      return `Music removal — ${job.input.visual.title}`;
    case "search":
      return `Search "${job.input.query}"`;
    case "probe":
      return `Quality probe — ${job.input.visual.title}`;
  }
}

export const TERMINAL_STATUSES: JobStatus[] = ["done", "error", "cancelled"];

export function isTerminal(status: JobStatus): boolean {
  return TERMINAL_STATUSES.includes(status);
}
