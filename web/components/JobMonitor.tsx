"use client";

import { useEffect, useRef, useState } from "react";
import { cancelJob, requestUpload } from "@/lib/client";
import type { Job } from "@/lib/types";

/** Bytes as MB/GB — the render size is the only number worth showing. */
function formatSize(bytes: number): string {
  const mb = bytes / 1_048_576;
  return mb >= 1024 ? `${(mb / 1024).toFixed(2)} GB` : `${mb.toFixed(1)} MB`;
}

function StatusPill({ job }: { job: Job }) {
  const map: Record<string, string> = {
    queued: "border-ink-600 text-ink-300",
    claimed: "border-accent/50 text-accent",
    running: "border-accent/50 text-accent",
    done: "border-emerald-500/50 text-emerald-300",
    error: "border-red-500/50 text-red-300",
    cancelled: "border-ink-600 text-ink-400",
  };
  return (
    <span className={`pill ${map[job.status] ?? ""}`}>{job.status}</span>
  );
}

export default function JobMonitor({
  job,
  onJobChange,
  onDismiss,
}: {
  job: Job;
  onJobChange: (j: Job) => void;
  onDismiss: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const logRef = useRef<HTMLPreElement>(null);

  useEffect(() => {
    // Keep the newest worker log line in view.
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [job.progress.log.length]);

  const active =
    job.status === "queued" || job.status === "claimed" || job.status === "running";
  const pct = job.progress.percent;

  const doUpload = async () => {
    setBusy(true);
    setActionError(null);
    try {
      onJobChange(await requestUpload(job.id));
    } catch (e) {
      setActionError(e instanceof Error ? e.message : "Upload request failed");
    } finally {
      setBusy(false);
    }
  };

  const doCancel = async () => {
    setBusy(true);
    try {
      onJobChange(await cancelJob(job.id));
    } catch (e) {
      setActionError(e instanceof Error ? e.message : "Cancel failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="card space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-ink-300">
            Job
          </h2>
          <StatusPill job={job} />
          <span className="text-xs text-ink-400">{job.progress.stage}</span>
        </div>
        <div className="flex gap-2">
          {active && (
            <button onClick={doCancel} className="btn-ghost" disabled={busy}>
              Cancel
            </button>
          )}
          {!active && (
            <button onClick={onDismiss} className="btn-ghost">
              Dismiss
            </button>
          )}
        </div>
      </div>

      {active && (
        <div>
          <div className="h-1.5 w-full overflow-hidden rounded-full bg-ink-800">
            <div
              className={`h-full rounded-full bg-accent transition-all ${
                pct === null ? "animate-pulse w-1/3" : ""
              }`}
              style={pct !== null ? { width: `${Math.min(100, Math.max(0, pct))}%` } : undefined}
            />
          </div>
          {pct !== null && (
            <p className="mt-1 text-right text-xs text-ink-400">{Math.round(pct)}%</p>
          )}
        </div>
      )}

      {job.status === "error" && (
        <div className="rounded-lg bg-red-500/10 px-3 py-2 text-sm text-red-300">
          <p className="font-medium">{job.error}</p>
          {job.errorDetails && (
            <p className="mt-1 text-xs opacity-80">{job.errorDetails}</p>
          )}
        </div>
      )}

      {actionError && (
        <p className="rounded-lg bg-red-500/10 px-3 py-2 text-sm text-red-300">
          {actionError}
        </p>
      )}

      {job.progress.log.length > 0 && (
        <pre ref={logRef} className="log-tail">
          {job.progress.log.join("\n")}
        </pre>
      )}

      {job.status === "done" && job.result && (
        <div className="space-y-3">
          {job.result.localPath && (
            <div className="rounded-lg border border-ink-700 bg-ink-850 px-3 py-2 text-sm text-ink-300">
              <p>
                <span className="text-ink-100">{job.result.fileName}</span>
                {job.result.sizeBytes
                  ? ` — ${formatSize(job.result.sizeBytes)}`
                  : ""}
              </p>
              <p className="mt-1 text-xs">
                Saved on{" "}
                <span className="text-ink-100">
                  {job.claimedBy ?? "the worker"}
                </span>
                . It stays on that machine — upload it to YouTube to watch it
                from anywhere.
              </p>
              {/* Plain text, never a link: this is a path on the worker's own
                  filesystem, and no browser can fetch it. */}
              <p className="mt-2 break-all font-mono text-xs text-ink-400">
                {job.result.localPath}
              </p>
            </div>
          )}

          {job.result.localPath && !job.result.youtubeVideoId && (
            <div className="flex flex-wrap gap-2">
              <button onClick={doUpload} className="btn-primary" disabled={busy}>
                {busy ? "Queueing…" : "Upload to YouTube"}
              </button>
            </div>
          )}

          {job.result.youtubeVideoId && (
            <a
              href={`https://www.youtube.com/watch?v=${job.result.youtubeVideoId}`}
              target="_blank"
              rel="noreferrer"
              className="inline-block rounded-lg bg-emerald-500/10 px-3 py-2 text-sm text-emerald-300 hover:bg-emerald-500/20"
            >
              Published → youtube.com/watch?v={job.result.youtubeVideoId} ↗
            </a>
          )}
        </div>
      )}
    </section>
  );
}
