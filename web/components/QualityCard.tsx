"use client";

import {
  UPSCALE_FACTOR,
  type CatalogVideo,
  type SourceQuality,
} from "@/lib/types";
import PRESET_DATA from "@/data/presets.json";

/**
 * `scaleMode`, `aspectRatio` and `description` only exist once
 * scripts/sync_web_data.py has regenerated presets.json, so they are optional:
 * the card must compile — and read sensibly — against either version of the file.
 */
interface Preset {
  slug: string;
  label: string;
  width: number;
  height: number;
  scaleMode?: string;
  aspectRatio?: string;
  description?: string;
}

const PRESETS: Preset[] = PRESET_DATA;

type Tone = "neutral" | "info" | "good" | "warn";

const TONE_STYLE: Record<Tone, string> = {
  neutral: "border-ink-700 bg-ink-850 text-ink-300",
  info: "border-ink-700 bg-ink-850 text-ink-300",
  good: "border-emerald-500/40 bg-emerald-500/10 text-emerald-200",
  warn: "border-amber-500/40 bg-amber-500/10 text-amber-200",
};

/** Biggest preset whose height still fits inside `ceiling` pixels, if any. */
function largestPresetWithin(ceiling: number): Preset | null {
  return (
    [...PRESETS]
      .sort((a, b) => b.height - a.height)
      .find((p) => p.height <= ceiling) ?? null
  );
}

/**
 * The whole point of the card: what the chosen preset actually gets you from
 * the source you picked.
 *
 * `ceiling` is how many lines of real detail exist — the source height, doubled
 * when Real-ESRGAN is on. Rendering above it is legal and looks fine, it just
 * spends bitrate on interpolated pixels, so those cases warn rather than block.
 */
function deriveVerdict(
  sourceHeight: number | null,
  upscale: boolean,
  preset: Preset
): { tone: Tone; text: string } {
  if (sourceHeight === null) {
    return {
      tone: "info",
      text: "Source quality unknown — it is measured the first time this video is downloaded.",
    };
  }

  const source = sourceHeight;
  const target = preset.height;
  const ceiling = source * (upscale ? UPSCALE_FACTOR : 1);
  // A "fill" preset crops the source before scaling, so a landscape source
  // never maps height-to-height onto a vertical frame. Absolute claims about
  // every pixel would be a lie there.
  const cropped = preset.scaleMode === "fill";

  if (target <= ceiling) {
    const claim = cropped
      ? "detail is close to the source's best"
      : "every pixel carries real detail";
    return {
      tone: "good",
      text: `Output ${target}p — ${claim} from the ${source}p source${
        upscale ? ` after ${UPSCALE_FACTOR}× upscale` : ""
      }.`,
    };
  }

  const upscaled = source * UPSCALE_FACTOR;

  if (!upscale && upscaled >= target) {
    return {
      tone: "warn",
      text: `The source is ${source}p; rendering at ${target}p only stretches pixels. Turn on Real-ESRGAN upscale to get ${upscaled}p of real detail.`,
    };
  }

  if (!upscale) {
    return {
      tone: "warn",
      text: `The source is ${source}p; even with ${UPSCALE_FACTOR}× upscale real detail tops out at ${upscaled}p. The ${target}p render is mostly stretched pixels.`,
    };
  }

  const better = largestPresetWithin(ceiling);
  return {
    tone: "warn",
    text:
      `Real detail tops out at ${ceiling}p even after upscale; the ${target}p render stretches beyond that.` +
      (better
        ? ` Pick ${better.label} to keep the detail without wasted pixels.`
        : ""),
  };
}

function measuredLine(quality: SourceQuality): string {
  const dimensions =
    quality.width && quality.height
      ? `${quality.width}×${quality.height} (${quality.height}p)`
      : `${quality.height}p`;
  const parts = [
    dimensions,
    quality.codec,
    quality.fps ? `${quality.fps.toFixed(0)} fps` : null,
  ].filter(Boolean);
  return `Source: ${parts.join(", ")} — measured from the downloaded file.`;
}

export default function QualityCard({
  visual,
  quality,
  presetSlug,
  onPresetChange,
  upscale,
  onUpscaleChange,
  onRecheck,
  disabled,
}: {
  visual: CatalogVideo | null;
  /** `"loading"` while a probe is in flight, `"failed"`/`null` when we do not know. */
  quality: SourceQuality | "loading" | "failed" | null;
  presetSlug: string;
  onPresetChange: (slug: string) => void;
  upscale: boolean;
  onUpscaleChange: (on: boolean) => void;
  /** Discard a cached answer and probe again. */
  onRecheck: () => void;
  /** Music removal remuxes the original streams, so none of this applies. */
  disabled: boolean;
}) {
  const preset = PRESETS.find((p) => p.slug === presetSlug) ?? PRESETS[0];
  const probed = typeof quality === "object" && quality !== null ? quality : null;
  const probing = quality === "loading";
  // A "listed" result with no height means the probe ran and learned nothing,
  // which is the same thing to the user as no probe at all.
  const sourceHeight = probed?.height ?? null;
  const listed = probed?.kind === "listed" && sourceHeight !== null;

  const verdict = probing
    ? { tone: "neutral" as Tone, text: "Checking source quality…" }
    : deriveVerdict(sourceHeight, upscale, preset);

  const ceiling =
    sourceHeight === null
      ? null
      : sourceHeight * (upscale ? UPSCALE_FACTOR : 1);

  return (
    <section className="card space-y-4">
      <h2 className="text-sm font-semibold uppercase tracking-wide text-ink-300">
        3 · Output quality
      </h2>

      {!visual ? (
        <p className="rounded-lg border border-dashed border-ink-700 px-3 py-3 text-sm text-ink-400">
          Pick a video above to see how good its source is.
        </p>
      ) : (
        <div className="space-y-2">
          <div className="flex flex-wrap gap-2">
            <span className="pill">
              Source{" "}
              {probing
                ? "checking…"
                : sourceHeight === null
                  ? "unknown"
                  : `${sourceHeight}p`}
            </span>
            {ceiling !== null && (
              <span className="pill">
                Real detail ceiling {ceiling}p
                {upscale ? ` (${UPSCALE_FACTOR}× upscale)` : ""}
              </span>
            )}
            <span className="pill">Render {preset.height}p</span>
          </div>

          {probed && sourceHeight !== null && (
            <p className="text-sm leading-relaxed text-ink-300">
              {listed
                ? `YouTube lists up to ${sourceHeight}p, but protected videos often deliver only 360p (SABR) — the true quality is known after the first download.`
                : measuredLine(probed)}
            </p>
          )}
        </div>
      )}

      {disabled ? (
        <p className="rounded-lg border border-ink-700 bg-ink-850 px-3 py-3 text-sm text-ink-300">
          Music removal remuxes the original video and audio streams untouched,
          so there is nothing to re-scale — the preset and upscale choices below
          do not apply to it.
        </p>
      ) : (
        <p
          className={`rounded-lg border px-3 py-3 text-sm leading-relaxed ${TONE_STYLE[verdict.tone]}`}
        >
          {verdict.text}
          {listed && !probing && " (estimate)"}
        </p>
      )}

      <div>
        <label className="label" htmlFor="output-preset">
          Output preset
        </label>
        <select
          id="output-preset"
          className="field"
          value={presetSlug}
          onChange={(e) => onPresetChange(e.target.value)}
          disabled={disabled}
        >
          {PRESETS.map((p) => (
            <option key={p.slug} value={p.slug}>
              {p.label}
              {p.aspectRatio ? ` · ${p.aspectRatio}` : ""}
            </option>
          ))}
        </select>
        {preset.description && (
          <p className="mt-1.5 text-xs leading-relaxed text-ink-400">
            {preset.description}
          </p>
        )}
      </div>

      <label className="flex items-start gap-3 rounded-lg border border-ink-700 bg-ink-850 p-3 text-sm">
        <input
          type="checkbox"
          className="mt-0.5 h-4 w-4 shrink-0"
          checked={upscale}
          onChange={(e) => onUpscaleChange(e.target.checked)}
          disabled={disabled}
        />
        <span className="min-w-0">
          <span className="block font-medium text-ink-100">
            Upscale visual ({UPSCALE_FACTOR}×, Real-ESRGAN)
          </span>
          <span className="mt-0.5 block text-xs leading-relaxed text-ink-400">
            {visual?.cacheState === "upscaled"
              ? "Already upscaled on this machine — enabling this is free (cached)."
              : `Reconstructs detail instead of stretching it, up to ${UPSCALE_FACTOR}× the source height. The first run on a video is slow; the result is cached for later renders.`}
          </span>
        </span>
      </label>

      {/* The worker runs one job at a time, so a probe queued behind a render
          waits it out and gives up at 45s. Saying so beats a silent "unknown". */}
      {!probing && visual && sourceHeight === null && (
        <div className="space-y-2">
          <p className="text-xs leading-relaxed text-ink-400">
            The check runs on your PC and waits its turn — if a render is
            already going, or the worker is offline, it gives up after 45s.
            Rendering still works either way.
          </p>
          <button className="btn-ghost w-full" onClick={onRecheck}>
            Check again
          </button>
        </div>
      )}
    </section>
  );
}
