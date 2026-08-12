"use client";

import { useEffect, useState } from "react";
import Gallery, { type Channel } from "./Gallery";
import SurahPanel from "./SurahPanel";
import AyahPanel from "./AyahPanel";
import MusicPanel from "./MusicPanel";
import JobMonitor from "./JobMonitor";
import RecentJobs from "./RecentJobs";
import { createJob, useJobPolling, useWorkerStatus } from "@/lib/client";
import type { Reciter, Surah } from "@/lib/surah";
import type { AyahRangeSpec, CatalogVideo, RenderSettings } from "@/lib/types";
// Generated from `render_presets.list_presets()` so slugs cannot drift away
// from what the worker accepts. Regenerate with scripts/sync_web_data.py.
import PRESETS from "@/data/presets.json";

type Mode = "surah" | "ayah" | "music_removal";

const MODES: { value: Mode; label: string }[] = [
  { value: "surah", label: "Overlay full surahs" },
  { value: "ayah", label: "Overlay an ayah range (memorisation)" },
  { value: "music_removal", label: "Strip background music (Demucs)" },
];


const DEFAULT_METADATA_PATH = "examples/metadata-surah-arrahman.json";

export default function Studio({
  surahs,
  reciters,
  channels,
}: {
  surahs: Surah[];
  reciters: Reciter[];
  channels: Channel[];
}) {
  const [mode, setMode] = useState<Mode>("surah");
  const [visual, setVisual] = useState<CatalogVideo | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  // Sidebar settings
  const [presetSlug, setPresetSlug] = useState(PRESETS[0].slug);
  const [burnSubtitles, setBurnSubtitles] = useState(false);
  const [upscale, setUpscale] = useState(false);
  const [metadataPath, setMetadataPath] = useState(DEFAULT_METADATA_PATH);
  const [playlistId, setPlaylistId] = useState("");

  const [surahReciter, setSurahReciter] = useState(reciters[0]?.slug ?? "");
  const [ayahReciter, setAyahReciter] = useState(
    reciters.find((r) => r.supportsAyah)?.slug ?? ""
  );

  const worker = useWorkerStatus();
  const { job, setJob } = useJobPolling(jobId);

  // Reset the monitor when the user starts composing a different kind of render.
  useEffect(() => setSubmitError(null), [mode]);

  const settings = (): RenderSettings => ({
    presetSlug,
    burnSubtitles,
    upscale,
    metadataPath: metadataPath.trim() || DEFAULT_METADATA_PATH,
    playlistId: playlistId.trim() || null,
  });

  const submit = async (build: () => Parameters<typeof createJob>[0]) => {
    setSubmitting(true);
    setSubmitError(null);
    try {
      const created = await createJob(build());
      setJobId(created.id);
      setJob(created);
    } catch (e) {
      setSubmitError(e instanceof Error ? e.message : "Could not queue the job");
    } finally {
      setSubmitting(false);
    }
  };

  const renderSurah = (surahNumbers: number[]) =>
    submit(() => ({
      kind: "surah",
      surahNumbers,
      reciterSlug: surahReciter,
      visual: visual!,
      settings: settings(),
    }));

  const renderAyah = (ranges: AyahRangeSpec[]) =>
    submit(() => ({
      kind: "ayah",
      ranges,
      reciterSlug: ayahReciter,
      visual: visual!,
      settings: settings(),
    }));

  const runMusicRemoval = (privacy: "private" | "unlisted" | "public") =>
    submit(() => ({
      kind: "music_removal",
      visual: visual!,
      privacy,
      playlistId: playlistId.trim() || null,
    }));

  const busy = submitting || !visual;

  return (
    <main className="mx-auto max-w-7xl space-y-5 p-5">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">Quran Studio</h1>
          <p className="text-sm text-ink-400">
            Renders run on your PC. This page just drives them.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <span
            className={`pill ${
              worker?.online
                ? "border-emerald-500/50 text-emerald-300"
                : "border-amber-500/50 text-amber-300"
            }`}
          >
            <span
              className={`h-1.5 w-1.5 rounded-full ${
                worker?.online ? "bg-emerald-400" : "bg-amber-400"
              }`}
            />
            {worker?.online ? "Worker online" : "Worker offline"}
            {worker && worker.queueDepth > 0 && ` · ${worker.queueDepth} queued`}
          </span>
          <form action="/api/auth/logout" method="post">
            <button
              className="btn-ghost"
              onClick={async (e) => {
                e.preventDefault();
                await fetch("/api/auth/logout", { method: "POST" });
                window.location.href = "/login";
              }}
            >
              Sign out
            </button>
          </form>
        </div>
      </header>

      {!worker?.online && (
        <p className="rounded-lg border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-sm text-amber-200">
          The worker on your PC is not polling. Jobs you queue now will run as
          soon as it comes back online — start it with{" "}
          <code>worker/run_worker.bat</code>.
        </p>
      )}

      <div className="grid gap-5 lg:grid-cols-[280px_1fr]">
        <aside className="card h-fit space-y-4">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-ink-300">
            Settings
          </h2>

          <div>
            <label className="label">Output preset</label>
            <select
              className="field"
              value={presetSlug}
              onChange={(e) => setPresetSlug(e.target.value)}
              disabled={mode === "music_removal"}
            >
              {PRESETS.map((p) => (
                <option key={p.slug} value={p.slug}>
                  {p.label}
                </option>
              ))}
            </select>
            {upscale && presetSlug === "youtube_landscape" && (
              <p className="mt-1 text-xs text-amber-300">
                Real-ESRGAN upscales to 720p; rendering at 1080p scales that
                back up. Pick the 720p preset to keep the detail.
              </p>
            )}
          </div>

          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={upscale}
              onChange={(e) => setUpscale(e.target.checked)}
              disabled={mode === "music_removal"}
            />
            Upscale visual (Real-ESRGAN)
          </label>

          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={burnSubtitles}
              onChange={(e) => setBurnSubtitles(e.target.checked)}
              disabled={mode !== "ayah"}
            />
            Burn subtitles
            {mode !== "ayah" && (
              <span className="text-xs text-ink-400">(ayah mode only)</span>
            )}
          </label>

          <div>
            <label className="label">Metadata JSON path (on the worker)</label>
            <input
              className="field"
              value={metadataPath}
              onChange={(e) => setMetadataPath(e.target.value)}
              disabled={mode === "music_removal"}
            />
          </div>

          <div>
            <label className="label">YouTube playlist id (optional)</label>
            <input
              className="field"
              value={playlistId}
              onChange={(e) => setPlaylistId(e.target.value)}
              placeholder="PL…"
            />
          </div>
        </aside>

        <div className="space-y-5">
          <Gallery
            channels={channels}
            selected={visual}
            onSelect={setVisual}
          />

          <section className="card space-y-4">
            <h2 className="text-sm font-semibold uppercase tracking-wide text-ink-300">
              2 · What do you want to do with it?
            </h2>
            <select
              className="field"
              value={mode}
              onChange={(e) => setMode(e.target.value as Mode)}
            >
              {MODES.map((m) => (
                <option key={m.value} value={m.value}>
                  {m.label}
                </option>
              ))}
            </select>

            {!visual && (
              <p className="rounded-lg border border-dashed border-ink-700 px-3 py-3 text-sm text-ink-400">
                Pick a video above to continue.
              </p>
            )}

            {submitError && (
              <p className="rounded-lg bg-red-500/10 px-3 py-2 text-sm text-red-300">
                {submitError}
              </p>
            )}

            {mode === "surah" && (
              <SurahPanel
                surahs={surahs}
                reciters={reciters}
                reciterSlug={surahReciter}
                onReciterChange={setSurahReciter}
                disabled={busy}
                onRender={renderSurah}
              />
            )}
            {mode === "ayah" && (
              <AyahPanel
                surahs={surahs}
                reciters={reciters}
                reciterSlug={ayahReciter}
                onReciterChange={setAyahReciter}
                disabled={busy}
                onRender={renderAyah}
              />
            )}
            {mode === "music_removal" && (
              <MusicPanel
                visual={visual}
                disabled={busy}
                onProcess={runMusicRemoval}
              />
            )}
          </section>

          {job && job.kind !== "search" && (
            <JobMonitor
              job={job}
              onJobChange={setJob}
              onDismiss={() => {
                setJobId(null);
                setJob(null);
              }}
            />
          )}

          {/* Jobs live in Redis, not in this tab. Without this list a render
              started elsewhere — or before a reload — would be invisible. */}
          <RecentJobs
            activeJobId={jobId}
            onOpen={(picked) => {
              setJobId(picked.id);
              setJob(picked);
            }}
          />
        </div>
      </div>
    </main>
  );
}
