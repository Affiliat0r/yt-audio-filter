"use client";

import { useEffect, useMemo, useState } from "react";
import type { Reciter, Surah } from "@/lib/surah";
import { expandSurahSelection } from "@/lib/surah";

export default function SurahPanel({
  surahs,
  reciters,
  reciterSlug,
  onReciterChange,
  onPayloadChange,
}: {
  surahs: Surah[];
  reciters: Reciter[];
  reciterSlug: string;
  onReciterChange: (slug: string) => void;
  /** Reports the repeat-expanded play order upward; the button is elsewhere. */
  onPayloadChange: (surahNumbers: number[]) => void;
}) {
  const [selected, setSelected] = useState<number[]>([]);
  const [repeats, setRepeats] = useState<Record<number, number>>({});
  const [setLoops, setSetLoops] = useState(1);
  const [filter, setFilter] = useState("");
  const [rangeFrom, setRangeFrom] = useState(105);
  const [rangeTo, setRangeTo] = useState(114);

  const byNumber = useMemo(
    () => new Map(surahs.map((s) => [s.number, s])),
    [surahs]
  );

  const options = useMemo(() => {
    const needle = filter.trim().toLowerCase();
    return surahs.filter(
      (s) =>
        !selected.includes(s.number) &&
        (!needle ||
          s.name.toLowerCase().includes(needle) ||
          String(s.number) === needle)
    );
  }, [surahs, selected, filter]);

  // Memoised so the report-upward effect fires on a real change rather than on
  // every render.
  const expanded = useMemo(
    () => expandSurahSelection(selected, repeats, setLoops),
    [selected, repeats, setLoops]
  );

  // The render button lives after the output-quality step, so the payload has
  // to travel up rather than being submitted from in here.
  useEffect(() => onPayloadChange(expanded), [expanded, onPayloadChange]);
  const reciter = reciters.find((r) => r.slug === reciterSlug) ?? reciters[0];

  const add = (n: number) => {
    setSelected((prev) => [...prev, n]);
    setFilter("");
  };
  /**
   * Append every surah between the two ends, inclusive.
   *
   * Picking 105 through 114 one at a time is ten clicks for what is really one
   * decision — and it is the common case, since the short closing surahs are
   * usually recited as a run. Reversed ends are read as descending rather than
   * rejected: An-Nas → Al-Fil is a legitimate order to recite in.
   */
  const addRange = () => {
    const step = rangeFrom <= rangeTo ? 1 : -1;
    const run: number[] = [];
    for (let n = rangeFrom; step > 0 ? n <= rangeTo : n >= rangeTo; n += step) {
      run.push(n);
    }
    setSelected((prev) => [...prev, ...run]);
  };

  const remove = (idx: number) =>
    setSelected((prev) => prev.filter((_, i) => i !== idx));
  const move = (idx: number, delta: number) =>
    setSelected((prev) => {
      const next = [...prev];
      const target = idx + delta;
      if (target < 0 || target >= next.length) return prev;
      [next[idx], next[target]] = [next[target], next[idx]];
      return next;
    });

  return (
    <div className="space-y-5">
      <div>
        <label className="label">Surahs — selection order is play order</label>
        <input
          className="field"
          placeholder="Search a surah by name or number…"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
        />
        {filter.trim() && options.length > 0 && (
          <div className="mt-2 max-h-52 overflow-y-auto rounded-lg border border-ink-700 bg-ink-850">
            {options.slice(0, 40).map((s) => (
              <button
                key={s.number}
                onClick={() => add(s.number)}
                className="block w-full px-3 py-1.5 text-left text-sm hover:bg-ink-800"
              >
                <span className="text-ink-400">{s.number}.</span> {s.name}
              </button>
            ))}
          </div>
        )}

        <div className="mt-3 rounded-lg border border-ink-700 bg-ink-850 p-3">
          <p className="mb-2 text-xs text-ink-400">
            Or add a whole run at once — pick the first and the last.
          </p>
          <div className="grid gap-2 sm:grid-cols-[1fr_1fr_auto]">
            <select
              className="field"
              value={rangeFrom}
              onChange={(e) => setRangeFrom(Number(e.target.value))}
            >
              {surahs.map((s) => (
                <option key={s.number} value={s.number}>
                  {s.number}. {s.name}
                </option>
              ))}
            </select>
            <select
              className="field"
              value={rangeTo}
              onChange={(e) => setRangeTo(Number(e.target.value))}
            >
              {surahs.map((s) => (
                <option key={s.number} value={s.number}>
                  {s.number}. {s.name}
                </option>
              ))}
            </select>
            <button onClick={addRange} className="btn-ghost whitespace-nowrap">
              Add {Math.abs(rangeTo - rangeFrom) + 1} surahs
            </button>
          </div>
        </div>
      </div>

      {selected.length === 0 ? (
        <p className="rounded-lg border border-dashed border-ink-700 px-3 py-4 text-sm text-ink-400">
          No surahs selected yet. Search for one above, or add a range.
        </p>
      ) : (
        <div className="space-y-2">
          {selected.map((n, idx) => {
            const s = byNumber.get(n);
            return (
              <div
                key={`${n}-${idx}`}
                className="flex items-center gap-2 rounded-lg border border-ink-700 bg-ink-850 px-3 py-2"
              >
                <span className="flex-1 truncate text-sm">
                  <span className="text-ink-400">{n}.</span> {s?.name ?? "?"}
                </span>
                <label className="flex items-center gap-1.5 text-xs text-ink-400">
                  repeat ×
                  <input
                    type="number"
                    min={1}
                    max={99}
                    value={repeats[n] ?? 1}
                    onChange={(e) =>
                      setRepeats((prev) => ({
                        ...prev,
                        [n]: Math.max(1, Math.min(99, Number(e.target.value) || 1)),
                      }))
                    }
                    className="w-16 rounded border border-ink-700 bg-ink-900 px-2 py-1 text-ink-100"
                  />
                </label>
                <button
                  onClick={() => move(idx, -1)}
                  disabled={idx === 0}
                  className="px-1.5 text-ink-400 hover:text-ink-100 disabled:opacity-30"
                  title="Move up"
                >
                  ↑
                </button>
                <button
                  onClick={() => move(idx, 1)}
                  disabled={idx === selected.length - 1}
                  className="px-1.5 text-ink-400 hover:text-ink-100 disabled:opacity-30"
                  title="Move down"
                >
                  ↓
                </button>
                <button
                  onClick={() => remove(idx)}
                  className="px-1.5 text-ink-400 hover:text-red-300"
                  title="Remove"
                >
                  ✕
                </button>
              </div>
            );
          })}

          {selected.length >= 2 && (
            <label className="flex items-center gap-2 pt-1 text-sm text-ink-300">
              Loop the whole set ×
              <input
                type="number"
                min={1}
                max={99}
                value={setLoops}
                onChange={(e) =>
                  setSetLoops(Math.max(1, Math.min(99, Number(e.target.value) || 1)))
                }
                className="w-20 rounded border border-ink-700 bg-ink-900 px-2 py-1"
              />
              <span className="text-xs text-ink-400">
                → {expanded.length} segments total
              </span>
            </label>
          )}
        </div>
      )}

      <div>
        <label className="label">Reciter</label>
        <select
          className="field"
          value={reciterSlug}
          onChange={(e) => onReciterChange(e.target.value)}
        >
          {reciters.map((r) => (
            <option key={r.slug} value={r.slug}>
              {r.displayName}
            </option>
          ))}
        </select>
        {reciter && (
          <audio
            key={reciter.slug}
            controls
            src={reciter.sampleUrl}
            className="mt-2 h-9 w-full"
          />
        )}
        <p className="mt-1 text-xs text-ink-400">
          Sample: Al-Fatiha · slug <code>{reciter?.slug}</code>
        </p>
      </div>

    </div>
  );
}
