"use client";

import { useEffect, useState } from "react";
import type { CatalogVideo } from "@/lib/types";

export default function MusicPanel({
  visual,
  onPrivacyChange,
  sourceHeight,
  scaleHeight,
  onScaleHeightChange,
}: {
  visual: CatalogVideo | null;
  /** Reports the chosen privacy upward; the button is elsewhere. */
  onPrivacyChange: (privacy: "private" | "unlisted" | "public") => void;
  /** Measured source height, when known. */
  sourceHeight: number | null;
  /** null = copy the video untouched (default). */
  scaleHeight: number | null;
  onScaleHeightChange: (h: number | null) => void;
}) {
  const [privacy, setPrivacy] = useState<"private" | "unlisted" | "public">(
    "private"
  );

  // The process button lives after the output-quality step.
  useEffect(() => onPrivacyChange(privacy), [privacy, onPrivacyChange]);

  // Only worth offering when the source is genuinely small. Above 720p there
  // is nothing to gain, and re-encoding would only lose quality.
  const canUpscale = sourceHeight !== null && sourceHeight < 720;

  return (
    <div className="space-y-5">
      {canUpscale && (
        <div className="rounded-lg border border-ink-700 bg-ink-850 p-4 text-sm">
          <label className="flex items-start gap-3">
            <input
              type="checkbox"
              className="mt-0.5 h-4 w-4 shrink-0"
              checked={scaleHeight !== null}
              onChange={(e) => onScaleHeightChange(e.target.checked ? 720 : null)}
            />
            <span className="min-w-0">
              <span className="block font-medium text-ink-100">
                Upscale the picture to 720p
              </span>
              <span className="mt-0.5 block text-xs leading-relaxed text-ink-400">
                This video is {sourceHeight}p. Normally the picture is copied
                untouched, which is fastest and loses nothing. Enlarging it to
                720p adds no new detail — but YouTube gives 720p uploads a better
                bitrate than 360p ones, so it often looks sharper on playback.
                Adds roughly 10–20 minutes for a long video.
              </span>
            </span>
          </label>
        </div>
      )}

      <div className="rounded-lg border border-ink-700 bg-ink-850 p-4 text-sm text-ink-300">
        <p className="font-medium text-ink-100">Strip background music</p>
        <p className="mt-1 text-ink-400">
          Demucs (<code>htdemucs</code>) isolates the vocal track and the video
          stream is remuxed losslessly. Roughly 30–60&nbsp;s of processing per
          minute of video on the GPU.
        </p>
        {visual && (
          <p className="mt-2 truncate text-ink-300">
            Source: <span className="text-ink-100">{visual.title}</span>
          </p>
        )}
      </div>

      <div>
        <label className="label">Privacy if you publish this later</label>
        <select
          className="field"
          value={privacy}
          onChange={(e) =>
            setPrivacy(e.target.value as "private" | "unlisted" | "public")
          }
        >
          <option value="private">private</option>
          <option value="unlisted">unlisted</option>
          <option value="public">public</option>
        </select>
        <p className="mt-1 text-xs text-ink-400">
          Processing never uploads on its own — you get a preview first, then an
          explicit upload button.
        </p>
      </div>

    </div>
  );
}
