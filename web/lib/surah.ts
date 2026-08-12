export interface Surah {
  number: number;
  name: string;
  ayahCount: number;
}

export interface Reciter {
  slug: string;
  displayName: string;
  arabicName: string;
  sampleUrl: string;
  supportsAyah: boolean;
}

/**
 * Port of `streamlit_app._expand_surah_selection`.
 *
 * Each selected surah is repeated in place, then the whole resulting block is
 * repeated `setLoops` times. So [1,112,114] with repeats [2,1,1] and 2 loops
 * gives [1,1,112,114, 1,1,112,114].
 */
export function expandSurahSelection(
  numbers: number[],
  repeats: Record<number, number>,
  setLoops: number
): number[] {
  const block: number[] = [];
  for (const n of numbers) {
    const times = Math.max(1, repeats[n] ?? 1);
    for (let i = 0; i < times; i++) block.push(n);
  }
  const loops = Math.max(1, setLoops);
  const out: number[] = [];
  for (let i = 0; i < loops; i++) out.push(...block);
  return out;
}

/** Rough runtime estimate so the user knows what a big set-loop costs. */
export function estimateSegments(count: number): string {
  if (count === 0) return "nothing selected";
  return `${count} segment${count === 1 ? "" : "s"}`;
}
