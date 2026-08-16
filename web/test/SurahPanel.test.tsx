import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import SurahPanel from "@/components/SurahPanel";
import { expandSurahSelection } from "@/lib/surah";
import surahData from "@/data/surahs.json";
import reciterData from "@/data/reciters.json";

const surahs = surahData as { number: number; name: string; ayahCount: number }[];
const reciters = reciterData as {
  slug: string;
  displayName: string;
  arabicName: string;
  sampleUrl: string;
  supportsAyah: boolean;
}[];

/**
 * The render button lives in step 4, not in this panel — the panel reports the
 * repeat-expanded play order upward. So the assertions read the last payload
 * rather than clicking a button that is not here.
 */
function renderPanel() {
  const onPayloadChange = vi.fn();
  render(
    <SurahPanel
      surahs={surahs}
      reciters={reciters}
      reciterSlug={reciters[0].slug}
      onReciterChange={() => {}}
      onPayloadChange={onPayloadChange}
    />
  );
  const lastPayload = () =>
    onPayloadChange.mock.calls.at(-1)?.[0] as number[] | undefined;
  return { onPayloadChange, lastPayload };
}

describe("expandSurahSelection", () => {
  it("repeats each surah in place, then loops the whole block", () => {
    expect(expandSurahSelection([1, 112, 114], { 1: 2 }, 2)).toEqual([
      1, 1, 112, 114, 1, 1, 112, 114,
    ]);
  });

  it("treats a missing or zero repeat as one", () => {
    expect(expandSurahSelection([1, 2], {}, 1)).toEqual([1, 2]);
    expect(expandSurahSelection([1], { 1: 0 }, 1)).toEqual([1]);
  });

  it("treats a zero loop count as one", () => {
    expect(expandSurahSelection([1, 2], {}, 0)).toEqual([1, 2]);
  });

  it("returns nothing for an empty selection", () => {
    expect(expandSurahSelection([], {}, 5)).toEqual([]);
  });
});

describe("SurahPanel range picker", () => {
  it("adds a whole run from one click", async () => {
    // Ten clicks for one decision was the complaint; 105-114 is the common case.
    const user = userEvent.setup();
    const { lastPayload } = renderPanel();

    const [from, to] = screen.getAllByRole("combobox");
    await user.selectOptions(from, "105");
    await user.selectOptions(to, "114");
    await user.click(screen.getByRole("button", { name: /add 10 surahs/i }));

    expect(lastPayload()).toEqual([
      105, 106, 107, 108, 109, 110, 111, 112, 113, 114,
    ]);
  });

  it("reads a reversed range as descending rather than refusing it", async () => {
    // An-Nas back to Al-Falaq is a legitimate order to recite in.
    const user = userEvent.setup();
    const { lastPayload } = renderPanel();

    const [from, to] = screen.getAllByRole("combobox");
    await user.selectOptions(from, "114");
    await user.selectOptions(to, "112");
    await user.click(screen.getByRole("button", { name: /add 3 surahs/i }));

    expect(lastPayload()).toEqual([114, 113, 112]);
  });

  it("counts a single-surah range as one", async () => {
    const user = userEvent.setup();
    renderPanel();

    const [from, to] = screen.getAllByRole("combobox");
    await user.selectOptions(from, "36");
    await user.selectOptions(to, "36");

    expect(screen.getByRole("button", { name: /add 1 surahs?/i })).toBeInTheDocument();
  });

  it("reports an empty play order before anything is picked", () => {
    const { lastPayload } = renderPanel();
    expect(lastPayload()).toEqual([]);
  });
});
