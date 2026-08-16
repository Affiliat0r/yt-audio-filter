"use client";

import { useEffect, useRef, useState } from "react";

/**
 * A number input you can actually clear.
 *
 * The obvious controlled form is a trap:
 *
 *     value={n}
 *     onChange={(e) => setN(Number(e.target.value) || 1)}
 *
 * Emptying the box makes `Number("")` zero, `|| 1` turns that into 1, and the
 * field instantly refills — so to get from 1 to 10 you must select-all first,
 * which on a phone is most of a fight. Clamping mid-keystroke is just as bad:
 * typing "25" into a max-99 field briefly reads "2", and a min of 1 rejects the
 * "0" in "10".
 *
 * So the raw text is held locally and allowed to be empty or half-typed while
 * focused. The parsed value propagates whenever it is genuinely valid, and
 * clamping happens on blur, when the user has finished saying what they mean.
 */
export default function NumberField({
  value,
  min,
  max,
  step = 1,
  onChange,
  className = "",
  ariaLabel,
}: {
  value: number;
  min: number;
  max: number;
  step?: number;
  onChange: (n: number) => void;
  className?: string;
  ariaLabel?: string;
}) {
  const [raw, setRaw] = useState(String(value));
  const focused = useRef(false);

  // Adopt outside changes (a row being removed, a preset applied) — but never
  // while typing, or the field would rewrite itself under the cursor.
  useEffect(() => {
    if (!focused.current && Number(raw) !== value) setRaw(String(value));
  }, [value, raw]);

  const commit = () => {
    const parsed = Number(raw);
    const clamped =
      raw.trim() === "" || !Number.isFinite(parsed)
        ? value
        : Math.min(max, Math.max(min, parsed));
    setRaw(String(clamped));
    if (clamped !== value) onChange(clamped);
  };

  return (
    <input
      type="number"
      inputMode="decimal"
      aria-label={ariaLabel}
      min={min}
      max={max}
      step={step}
      value={raw}
      className={className}
      onFocus={(e) => {
        focused.current = true;
        // Selecting on focus means the first keystroke replaces rather than
        // appends, which is what you want when changing 1 to 10.
        e.target.select();
      }}
      onChange={(e) => {
        const next = e.target.value;
        setRaw(next);
        const parsed = Number(next);
        // Propagate only a value that is already in range; anything else waits
        // for blur so half-typed input is never clamped out from under them.
        if (next.trim() !== "" && Number.isFinite(parsed) && parsed >= min && parsed <= max) {
          onChange(parsed);
        }
      }}
      onBlur={() => {
        focused.current = false;
        commit();
      }}
    />
  );
}
