"use client";

import { useMemo, useState } from "react";
import type { Reciter, Surah } from "@/lib/surah";
import type { AyahRangeSpec } from "@/lib/types";

interface Row extends AyahRangeSpec {
  /** Stable id — index-keyed rows misbind their values when one is deleted. */
  id: string;
}

let rowSeq = 0;
const newRow = (): Row => ({
  id: `r${rowSeq++}`,
  surah: 1,
  start: 1,
  end: 7,
  repeats: 3,
  gapSeconds: 0,
});

export default function AyahPanel({
  surahs,
  reciters,
  reciterSlug,
  onReciterChange,
  disabled,
  onRender,
}: {
  surahs: Surah[];
  reciters: Reciter[];
  reciterSlug: string;
  onReciterChange: (slug: string) => void;
  disabled: boolean;
  onRender: (ranges: AyahRangeSpec[]) => void;
}) {
  const [rows, setRows] = useState<Row[]>(() => [newRow()]);

  const ayahCounts = useMemo(
    () => new Map(surahs.map((s) => [s.number, s.ayahCount || 286])),
    [surahs]
  );

  // Only reciters with an everyayah mirror can do per-ayah audio.
  const eligible = reciters.filter((r) => r.supportsAyah);

  const patch = (id: string, updates: Partial<Row>) =>
    setRows((prev) =>
      prev.map((r) => {
        if (r.id !== id) return r;
        const next = { ...r, ...updates };
        const max = ayahCounts.get(next.surah) ?? 286;
        next.start = Math.min(Math.max(1, next.start), max);
        next.end = Math.min(Math.max(next.start, next.end), max);
        return next;
      })
    );

  const totalAyat = rows.reduce(
    (sum, r) => sum + (r.end - r.start + 1) * r.repeats,
    0
  );

  return (
    <div className="space-y-5">
      <div className="space-y-3">
        {rows.map((r, i) => {
          const max = ayahCounts.get(r.surah) ?? 286;
          return (
            <div
              key={r.id}
              className="rounded-lg border border-ink-700 bg-ink-850 p-3"
            >
              <div className="mb-2 flex items-center justify-between">
                <span className="text-xs text-ink-400">Range {i + 1}</span>
                <button
                  onClick={() => setRows((prev) => prev.filter((x) => x.id !== r.id))}
                  disabled={rows.length === 1}
                  className="text-ink-400 hover:text-red-300 disabled:opacity-30"
                  title="Remove this range"
                >
                  ✕
                </button>
              </div>
              <div className="grid gap-2 sm:grid-cols-5">
                <div className="sm:col-span-2">
                  <label className="label">Surah</label>
                  <select
                    className="field"
                    value={r.surah}
                    onChange={(e) =>
                      patch(r.id, { surah: Number(e.target.value) })
                    }
                  >
                    {surahs.map((s) => (
                      <option key={s.number} value={s.number}>
                        {s.number}. {s.name}
                      </option>
                    ))}
                  </select>
                </div>
                <div>
                  <label className="label">From ayah</label>
                  <input
                    type="number"
                    min={1}
                    max={max}
                    className="field"
                    value={r.start}
                    onChange={(e) =>
                      patch(r.id, { start: Number(e.target.value) || 1 })
                    }
                  />
                </div>
                <div>
                  <label className="label">To ayah</label>
                  <input
                    type="number"
                    min={r.start}
                    max={max}
                    className="field"
                    value={r.end}
                    onChange={(e) =>
                      patch(r.id, { end: Number(e.target.value) || r.start })
                    }
                  />
                </div>
                <div>
                  <label className="label">Repeats</label>
                  <input
                    type="number"
                    min={1}
                    max={99}
                    className="field"
                    value={r.repeats}
                    onChange={(e) =>
                      patch(r.id, {
                        repeats: Math.max(
                          1,
                          Math.min(99, Number(e.target.value) || 1)
                        ),
                      })
                    }
                  />
                </div>
              </div>
              <div className="mt-2 grid gap-2 sm:grid-cols-5">
                <div>
                  <label className="label">Gap (s)</label>
                  <input
                    type="number"
                    min={0}
                    max={5}
                    step={0.5}
                    className="field"
                    value={r.gapSeconds}
                    onChange={(e) =>
                      patch(r.id, {
                        gapSeconds: Math.max(
                          0,
                          Math.min(5, Number(e.target.value) || 0)
                        ),
                      })
                    }
                  />
                </div>
                <p className="self-end pb-2 text-xs text-ink-400 sm:col-span-4">
                  Surah has {max} ayat · this range plays{" "}
                  {(r.end - r.start + 1) * r.repeats} recitations
                </p>
              </div>
            </div>
          );
        })}
      </div>

      <button
        onClick={() => setRows((prev) => [...prev, newRow()])}
        className="btn-ghost w-full"
      >
        + Add another range
      </button>

      <div>
        <label className="label">Reciter (per-ayah audio required)</label>
        <select
          className="field"
          value={reciterSlug}
          onChange={(e) => onReciterChange(e.target.value)}
        >
          {eligible.map((r) => (
            <option key={r.slug} value={r.slug}>
              {r.displayName}
            </option>
          ))}
        </select>
        <p className="mt-1 text-xs text-ink-400">
          {eligible.length} of {reciters.length} reciters have a per-ayah mirror.
        </p>
      </div>

      <button
        className="btn-primary w-full"
        disabled={disabled || rows.length === 0}
        onClick={() =>
          onRender(
            rows.map(({ id: _id, ...spec }) => spec)
          )
        }
      >
        Render ayah-range video ({totalAyat} recitations)
      </button>
    </div>
  );
}
