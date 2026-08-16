"use client";

import { useEffect, useRef, useState } from "react";
import Gallery, { type Channel } from "./Gallery";
import SurahPanel from "./SurahPanel";
import AyahPanel from "./AyahPanel";
import MusicPanel from "./MusicPanel";
import JobMonitor from "./JobMonitor";
import QualityCard from "./QualityCard";
import RecentJobs from "./RecentJobs";
import WorkerTarget, { type TargetState } from "./WorkerTarget";
import {
  createJob,
  probeSourceQuality,
  useJobPolling,
  useWorkerStatus,
} from "@/lib/client";
import type { Reciter, Surah } from "@/lib/surah";
import type {
  AyahRangeSpec,
  CatalogVideo,
  RenderSettings,
  SourceQuality,
} from "@/lib/types";
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

  // Render settings. `presetSlug`/`upscale` are edited in the quality card
  // rather than the aside, but they still travel in `RenderSettings` unchanged.
  const [presetSlug, setPresetSlug] = useState(PRESETS[0].slug);
  const [burnSubtitles, setBurnSubtitles] = useState(false);
  const [metadataPath, setMetadataPath] = useState(DEFAULT_METADATA_PATH);
  const [playlistId, setPlaylistId] = useState("");
  // null = copy the video untouched. Music removal only; the overlay
  // pipeline already renders at the preset resolution.
  const [scaleHeight, setScaleHeight] = useState<number | null>(null);

  // What each panel has composed. The panels report upward instead of holding
  // their own submit button, so the action can sit after the quality step —
  // choosing an output resolution below a button that already rendered is no
  // choice at all.
  const [surahNumbers, setSurahNumbers] = useState<number[]>([]);
  const [ayahRanges, setAyahRanges] = useState<AyahRangeSpec[]>([]);
  const [musicPrivacy, setMusicPrivacy] = useState<
    "private" | "unlisted" | "public"
  >("private");

  const [surahReciter, setSurahReciter] = useState(reciters[0]?.slug ?? "");
  const [ayahReciter, setAyahReciter] = useState(
    reciters.find((r) => r.supportsAyah)?.slug ?? ""
  );

  const worker = useWorkerStatus();
  const { job, setJob } = useJobPolling(jobId);
  const [target, setTarget] = useState<TargetState>({
    targetWorkerId: null,
    chosen: null,
    localWorkerId: null,
    workers: [],
    probing: true,
  });

  // A light worker has no Demucs. Blocking here beats letting the job queue,
  // download the source, and only then fail.
  const targetCannotRemoveMusic =
    target.chosen?.online === true && target.chosen.canRemoveMusic === false;

  // Source quality, keyed by machine AND video: whether the file is already on
  // disk — and so measurable instead of guessed from YouTube's format list — is
  // a property of one PC's cache, not of the video.
  const [quality, setQuality] = useState<Map<string, SourceQuality | "failed">>(
    () => new Map()
  );
  const [probing, setProbing] = useState<Set<string>>(() => new Set());
  // Read through refs so the effect below can depend on the key alone. Keyed on
  // the previous commit's values, which is exactly what "have we already asked
  // about this one?" needs.
  const qualityRef = useRef(quality);
  const probingRef = useRef(probing);
  useEffect(() => {
    qualityRef.current = quality;
    probingRef.current = probing;
  });

  // Bumped to re-ask about a video we already have an answer for. The effect
  // keys on the video, so without this a cleared cache entry would not re-fire.
  const [recheckNonce, setRecheckNonce] = useState(0);

  const probeWorkerId = target.targetWorkerId ?? target.localWorkerId;
  const qualityKey = visual
    ? `${probeWorkerId ?? "any"}:${visual.videoId}`
    : null;

  useEffect(() => {
    if (!visual || !qualityKey) return;
    // Music removal remuxes the original streams untouched, so the quality card
    // is disabled and nothing would ever display the answer. Probing anyway
    // spends a queue-jumping worker job per gallery click for nothing.
    if (mode === "music_removal") return;
    // Already measured during catalog sync — the worker ffprobed the file it
    // has on disk. Probing again would queue a job to re-learn what we were
    // just told, and would sit behind any running render.
    if (visual.sourceQuality) return;
    // Wait for worker discovery to settle. Probing before it does would ask
    // "any machine" about a cache that belongs to one specific disk, and the
    // answer — measured off a file only that machine has — would describe a PC
    // the render is not going to run on.
    if (target.probing) return;
    if (qualityRef.current.has(qualityKey) || probingRef.current.has(qualityKey))
      return;
    setProbing((prev) => new Set(prev).add(qualityKey));
    // A probe whose video the user has moved on from is left to finish rather
    // than cancelled: it already cost a worker job, and caching the answer
    // makes coming back to that video instant.
    void probeSourceQuality(visual, probeWorkerId).then((result) => {
      setQuality((prev) => new Map(prev).set(qualityKey, result ?? "failed"));
      setProbing((prev) => {
        const next = new Set(prev);
        next.delete(qualityKey);
        return next;
      });
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [qualityKey, recheckNonce, target.probing, mode]);

  // Catalog-supplied measurement wins: it came from ffprobe on the real file,
  // and it is available instantly even while the worker is mid-render.
  const cachedQuality = visual?.sourceQuality
    ? visual.sourceQuality
    : qualityKey
      ? quality.get(qualityKey)
      : undefined;

  // Upscale is derived, never asked. Real-ESRGAN only helps when the source is
  // smaller than the target — above that it costs a slow first pass for nothing
  // — and deciding it from the measured height removes a control whose
  // interaction with the preset nobody should have to reason about.
  const measuredHeight =
    typeof cachedQuality === "object" && cachedQuality !== null
      ? cachedQuality.height ?? null
      : null;
  const targetHeight =
    PRESETS.find((p) => p.slug === presetSlug)?.height ?? PRESETS[0].height;
  // Sharpening is worth it when the source is smaller than the target — but
  // only when it can actually finish. Real-ESRGAN runs frame by frame and
  // writes every frame to disk twice, so a 30-minute cartoon is ~44,000 frames
  // and tens of gigabytes. One auto-enabled render ground for twenty hours.
  // Mirrors MAX_UPSCALE_FRAMES in upscale.py; the worker enforces it too.
  const estimatedFrames = visual ? visual.duration * 24 : 0;
  const tooLongToUpscale = estimatedFrames > 10_000;
  const upscale =
    measuredHeight !== null &&
    measuredHeight < targetHeight &&
    (!tooLongToUpscale || visual?.cacheState === "upscaled");

  // A probe that timed out because a render was hogging the worker is not a
  // permanent answer, but it is cached like one. Without a way to ask again the
  // card stays "unknown" until the page is reloaded.
  const recheckQuality = () => {
    if (!qualityKey) return;
    setQuality((prev) => {
      const next = new Map(prev);
      next.delete(qualityKey);
      return next;
    });
    setRecheckNonce((n) => n + 1);
  };

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
      const created = await createJob(build(), target.targetWorkerId);
      setJobId(created.id);
      setJob(created);
    } catch (e) {
      setSubmitError(e instanceof Error ? e.message : "Could not queue the job");
    } finally {
      setSubmitting(false);
    }
  };

  const start = () => {
    if (mode === "surah")
      return submit(() => ({
        kind: "surah",
        surahNumbers,
        reciterSlug: surahReciter,
        visual: visual!,
        settings: settings(),
      }));
    if (mode === "ayah")
      return submit(() => ({
        kind: "ayah",
        ranges: ayahRanges,
        reciterSlug: ayahReciter,
        visual: visual!,
        settings: settings(),
      }));
    return submit(() => ({
      kind: "music_removal",
      visual: visual!,
      privacy: musicPrivacy,
      playlistId: playlistId.trim() || null,
    }));
  };

  const busy = submitting || !visual;

  // The action moved below the quality step, so its label and enablement have
  // to be derived here from what the panels reported rather than owned by them.
  const totalAyat = ayahRanges.reduce(
    (sum, r) => sum + (r.end - r.start + 1) * r.repeats,
    0
  );
  const action =
    mode === "surah"
      ? {
          label: `Render${surahNumbers.length > 0 ? ` (${surahNumbers.length} segments)` : ""}`,
          ready: surahNumbers.length > 0,
        }
      : mode === "ayah"
        ? {
            label: `Render ayah-range video (${totalAyat} recitations)`,
            ready: ayahRanges.length > 0,
          }
        : { label: "Process", ready: !targetCannotRemoveMusic };

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

          <WorkerTarget onChange={setTarget} />

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
            targetWorkerId={target.targetWorkerId ?? target.localWorkerId}
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

            {mode === "surah" && (
              <SurahPanel
                surahs={surahs}
                reciters={reciters}
                reciterSlug={surahReciter}
                onReciterChange={setSurahReciter}
                onPayloadChange={setSurahNumbers}
              />
            )}
            {mode === "ayah" && (
              <AyahPanel
                surahs={surahs}
                reciters={reciters}
                reciterSlug={ayahReciter}
                onReciterChange={setAyahReciter}
                onPayloadChange={setAyahRanges}
              />
            )}
            {mode === "music_removal" && (
              <>
                {targetCannotRemoveMusic && (
                  <p className="rounded-lg border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-sm text-amber-200">
                    <strong>{target.chosen?.hostname}</strong> cannot strip
                    background music — it has no Demucs installed. Pick a
                    machine that does under <em>Run the work on</em>, or install
                    it there with{" "}
                    <code>pip install -e &quot;.[music]&quot;</code> (~5 GB).
                  </p>
                )}
                <MusicPanel
                  visual={visual}
                  onPrivacyChange={setMusicPrivacy}
                  sourceHeight={measuredHeight}
                  scaleHeight={scaleHeight}
                  onScaleHeightChange={setScaleHeight}
                />
              </>
            )}
          </section>

          <QualityCard
            visual={visual}
            quality={
              cachedQuality ??
              // Discovery still running counts as in-flight: the probe is about
              // to fire, so "checking…" is truer than "unknown".
              (qualityKey !== null &&
              (probing.has(qualityKey) || target.probing)
                ? "loading"
                : null)
            }
            presetSlug={presetSlug}
            onPresetChange={setPresetSlug}
            upscale={upscale}
            tooLongToUpscale={tooLongToUpscale}
            onRecheck={recheckQuality}
            disabled={mode === "music_removal"}
          />

          <section className="card space-y-3">
            <h2 className="text-sm font-semibold uppercase tracking-wide text-ink-300">
              4 · Start it
            </h2>

            {submitError && (
              <p className="rounded-lg bg-red-500/10 px-3 py-2 text-sm text-red-300">
                {submitError}
              </p>
            )}

            <button
              className="btn-primary w-full"
              disabled={busy || !action.ready}
              onClick={start}
            >
              {action.label}
            </button>

            <p className="text-xs leading-relaxed text-ink-400">
              {!visual
                ? "Pick a video in step 1 first."
                : !action.ready && mode !== "music_removal"
                  ? "Choose what to recite in step 2 first."
                  : "Renders land as a preview on this page. Publishing to YouTube is a separate button afterwards."}
            </p>
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
