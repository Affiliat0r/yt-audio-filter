"use client";

import { useState } from "react";
import type { CatalogVideo } from "@/lib/types";

export default function MusicPanel({
  visual,
  disabled,
  onProcess,
}: {
  visual: CatalogVideo | null;
  disabled: boolean;
  onProcess: (privacy: "private" | "unlisted" | "public") => void;
}) {
  const [privacy, setPrivacy] = useState<"private" | "unlisted" | "public">(
    "private"
  );

  return (
    <div className="space-y-5">
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

      <button
        className="btn-primary w-full"
        disabled={disabled}
        onClick={() => onProcess(privacy)}
      >
        Process
      </button>
    </div>
  );
}
