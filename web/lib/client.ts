"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  DISCOVERY_PORT,
  isTerminal,
  type CatalogVideo,
  type Job,
  type JobInput,
  type SourceQuality,
  type WorkerSummary,
} from "./types";

export class ApiError extends Error {}

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: { "content-type": "application/json", ...(init?.headers ?? {}) },
  });
  if (res.status === 401) {
    window.location.href = "/login";
    throw new ApiError("Session expired");
  }
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new ApiError(body.error ?? `Request failed (${res.status})`);
  return body as T;
}

export const createJob = (input: JobInput, targetWorkerId?: string | null) =>
  api<{ job: Job }>("/api/jobs", {
    method: "POST",
    body: JSON.stringify(
      targetWorkerId ? { ...input, targetWorkerId } : input
    ),
  }).then((r) => r.job);

export const fetchJob = (id: string) =>
  api<{ job: Job }>(`/api/jobs/${id}`).then((r) => r.job);

export const cancelJob = (id: string) =>
  api<{ job: Job }>(`/api/jobs/${id}`, { method: "DELETE" }).then((r) => r.job);

export const requestUpload = (id: string) =>
  api<{ job: Job }>(`/api/jobs/${id}/upload`, { method: "POST" }).then(
    (r) => r.job
  );

export const fetchCatalog = (workerId?: string | null) =>
  api<{ videos: CatalogVideo[]; updatedAt: number | null }>(
    workerId ? `/api/catalog?workerId=${encodeURIComponent(workerId)}` : "/api/catalog"
  );

/**
 * Ask the worker how good a visual's source really is.
 *
 * ffprobe and yt-dlp only exist on the worker, so this is a job — a fast,
 * queue-jumping one, polled here rather than through `useJobPolling` so it
 * never surfaces in the job monitor.
 *
 * `targetWorkerId` matters: whether the file is already downloaded (measured)
 * or has to be guessed from YouTube's format list (listed) is a property of
 * one machine's disk, so an untargeted probe could be answered by the wrong PC.
 *
 * Never throws for "we could not find out": a worker that is busy with a
 * 40-minute render will not answer inside the deadline, and an older worker
 * rejects the unknown job kind outright. Both come back as `null`, which the
 * UI shows as "quality unknown" instead of blocking the render.
 */
export async function probeSourceQuality(
  visual: CatalogVideo,
  targetWorkerId: string | null,
  deadlineMs = 45_000
): Promise<SourceQuality | null> {
  try {
    const job = await createJob({ kind: "probe", visual }, targetWorkerId);
    const deadline = Date.now() + deadlineMs;
    for (;;) {
      await new Promise((r) => setTimeout(r, 1200));
      const cur = await fetchJob(job.id);
      if (cur.status === "done") return cur.result?.sourceQuality ?? null;
      if (cur.status === "error" || cur.status === "cancelled") return null;
      if (Date.now() > deadline) return null;
    }
  } catch {
    return null;
  }
}

/**
 * Server-side job history. This is what makes a render survive a reload or
 * follow you to another device — the job lives in Redis, not in the tab that
 * started it.
 */
export const fetchJobs = () =>
  api<{ jobs: Job[] }>("/api/jobs").then((r) => r.jobs);

export const fetchWorkerStatus = () =>
  api<{ online: boolean; queueDepth: number; heartbeat: unknown }>(
    "/api/worker/status"
  );

export const fetchWorkers = () =>
  api<{ workers: WorkerSummary[] }>("/api/workers").then((r) => r.workers);

/**
 * Ask the worker on THIS machine who it is.
 *
 * The browser cannot see local processes, so the worker runs a tiny loopback
 * endpoint and we probe it. A hit means that worker shares a machine with this
 * browser, which is what lets us pin jobs to the laptop you are sitting at.
 *
 * Expected to fail often and harmlessly — no local worker, a phone, a
 * different port — so failure just means "no local worker", never an error.
 */
export async function probeLocalWorker(
  timeoutMs = 1500
): Promise<{ workerId: string; hostname: string; gpu: string | null } | null> {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const res = await fetch(`http://127.0.0.1:${DISCOVERY_PORT}/whoami`, {
      signal: ctrl.signal,
      // No cookies to a local origin, and never a cached answer: the worker
      // may have restarted with a different identity.
      credentials: "omit",
      cache: "no-store",
    });
    if (!res.ok) return null;
    const body = await res.json();
    return typeof body?.workerId === "string" ? body : null;
  } catch {
    return null;
  } finally {
    clearTimeout(timer);
  }
}

/**
 * Poll a job until it reaches a terminal state. Polling (rather than SSE) is
 * deliberate: Vercel functions are short-lived, and a render can run for tens
 * of minutes across many cold starts.
 */
export function useJobPolling(jobId: string | null, intervalMs = 2000) {
  const [job, setJob] = useState<Job | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Bumped to restart polling after a job leaves a terminal state — which
  // happens when the user requests a YouTube upload of a finished render.
  const [generation, setGeneration] = useState(0);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (!jobId) {
      setJob(null);
      return;
    }
    let cancelled = false;

    const tick = async () => {
      try {
        const next = await fetchJob(jobId);
        if (cancelled) return;
        setJob(next);
        setError(null);
        if (next.status === "done" || next.status === "error" || next.status === "cancelled") {
          return; // stop polling
        }
      } catch (e) {
        if (cancelled) return;
        // Transient failures are expected on cold starts; keep polling.
        setError(e instanceof Error ? e.message : "Poll failed");
      }
      if (!cancelled) timer.current = setTimeout(tick, intervalMs);
    };

    void tick();
    return () => {
      cancelled = true;
      if (timer.current) clearTimeout(timer.current);
    };
  }, [jobId, intervalMs, generation]);

  /**
   * Adopt a job returned by a mutation. If it went back to a non-terminal
   * state, resume polling — the effect above has already stopped.
   */
  const adoptJob = useCallback((next: Job | null) => {
    setJob(next);
    if (next && !isTerminal(next.status)) setGeneration((g) => g + 1);
  }, []);

  return { job, error, setJob: adoptJob };
}

export function useWorkerStatus(intervalMs = 20000) {
  const [status, setStatus] = useState<{
    online: boolean;
    queueDepth: number;
  } | null>(null);

  const refresh = useCallback(async () => {
    try {
      const s = await fetchWorkerStatus();
      setStatus({ online: s.online, queueDepth: s.queueDepth });
    } catch {
      setStatus(null);
    }
  }, []);

  useEffect(() => {
    void refresh();
    const h = setInterval(refresh, intervalMs);
    return () => clearInterval(h);
  }, [refresh, intervalMs]);

  return status;
}

export function formatDuration(seconds: number): string {
  if (!seconds || seconds < 0) return "—";
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.floor(seconds % 60);
  return h > 0
    ? `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`
    : `${m}:${String(s).padStart(2, "0")}`;
}

export function formatViews(n: number): string {
  if (!n) return "—";
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(0)}K`;
  return String(n);
}
