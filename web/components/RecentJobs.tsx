"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { fetchJobs } from "@/lib/client";
import { isTerminal, jobLabel, type Job } from "@/lib/types";

const STATUS_STYLE: Record<string, string> = {
  queued: "text-ink-300",
  claimed: "text-accent",
  running: "text-accent",
  done: "text-emerald-300",
  error: "text-red-300",
  cancelled: "text-ink-400",
};

function relativeTime(ms: number): string {
  const delta = Date.now() - ms;
  const mins = Math.round(delta / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins} min ago`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.round(hours / 24)}d ago`;
}

export default function RecentJobs({
  activeJobId,
  onOpen,
}: {
  activeJobId: string | null;
  onOpen: (job: Job) => void;
}) {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  // Auto-open runs once per mount, so dismissing a job does not re-open it.
  const autoOpened = useRef(false);

  // `onOpen` and `activeJobId` come from Studio, which re-renders on every
  // job-poll tick. Reading them through refs keeps `refresh` stable, so the
  // interval below stays at 15s instead of being torn down and refetching
  // every couple of seconds.
  const onOpenRef = useRef(onOpen);
  const activeJobIdRef = useRef(activeJobId);
  useEffect(() => {
    onOpenRef.current = onOpen;
    activeJobIdRef.current = activeJobId;
  });

  const refresh = useCallback(async () => {
    try {
      // Search and quality-probe jobs are plumbing for the gallery and the
      // quality card, not something the user thinks of as a job. Hide them —
      // which also keeps the auto-open below from putting one in the monitor.
      const all = await fetchJobs();
      const visible = all.filter(
        (j) => j.kind !== "search" && j.kind !== "probe"
      );
      setJobs(visible);
      setError(null);

      if (!autoOpened.current && !activeJobIdRef.current) {
        autoOpened.current = true;
        // Surface a render that is still going, so loading the page mid-render
        // shows live progress rather than an empty panel.
        const inFlight = visible.find((j) => !isTerminal(j.status));
        if (inFlight) onOpenRef.current(inFlight);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not load jobs");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
    const h = setInterval(refresh, 15000);
    return () => clearInterval(h);
  }, [refresh]);

  if (loading) return null;
  if (!error && jobs.length === 0) return null;

  return (
    <section className="card space-y-3">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-ink-300">
          Recent jobs
        </h2>
        <button onClick={refresh} className="btn-ghost">
          Refresh
        </button>
      </div>

      {error ? (
        <p className="rounded-lg bg-red-500/10 px-3 py-2 text-sm text-red-300">
          {error}
        </p>
      ) : (
        <ul className="divide-y divide-ink-800">
          {jobs.map((job) => {
            const active = job.id === activeJobId;
            return (
              <li
                key={job.id}
                className="flex flex-wrap items-center gap-3 py-2"
              >
                <span
                  className={`w-20 shrink-0 text-xs font-medium ${
                    STATUS_STYLE[job.status] ?? ""
                  }`}
                >
                  {!isTerminal(job.status) && "● "}
                  {job.status}
                </span>
                <span className="min-w-0 flex-1 truncate text-sm text-ink-100">
                  {jobLabel(job)}
                </span>
                {job.result?.youtubeVideoId && (
                  <span className="pill border-emerald-500/40 text-emerald-300">
                    on YouTube
                  </span>
                )}
                <span className="shrink-0 text-xs text-ink-400">
                  {relativeTime(job.createdAt)}
                </span>
                <button
                  onClick={() => onOpen(job)}
                  className="btn-ghost shrink-0"
                  disabled={active}
                >
                  {active ? "Open" : "View"}
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
