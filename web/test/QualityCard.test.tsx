import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import QualityCard from "@/components/QualityCard";
import type { CatalogVideo, SourceQuality } from "@/lib/types";

/**
 * This card is where the whole output-quality feature's judgement lives, and
 * until now none of it was tested — two of the bugs that reached the user came
 * from exactly here.
 */

const video = (over: Partial<CatalogVideo> = {}): CatalogVideo => ({
  videoId: "abc12345xyz",
  url: "https://youtu.be/abc12345xyz",
  title: "A cartoon",
  duration: 120,
  viewCount: 10,
  uploadDate: "20250101",
  thumbnailUrl: "",
  channelSlug: "toyfactorycartoon",
  ...over,
});

const measured = (height: number): SourceQuality => ({
  kind: "measured",
  width: Math.round((height * 16) / 9),
  height,
  fps: 24,
  codec: "h264",
  probedAt: 0,
});

function renderCard(props: Partial<React.ComponentProps<typeof QualityCard>> = {}) {
  return render(
    <QualityCard
      visual={video()}
      quality={measured(360)}
      presetSlug="youtube_landscape"
      onPresetChange={() => {}}
      upscale={false}
      onRecheck={() => {}}
      disabled={false}
      {...props}
    />
  );
}

describe("QualityCard — overlay modes", () => {
  it("says a 360p source rendered at 1080p is being stretched", () => {
    renderCard();
    expect(screen.getByText(/only stretch|stretched pixels/i)).toBeInTheDocument();
  });

  it("calls a source that covers the target good", () => {
    renderCard({ quality: measured(1080) });
    expect(screen.getByText(/every pixel carries real detail/i)).toBeInTheDocument();
  });

  it("never recommends a landscape preset to someone rendering vertical", () => {
    // The bug: presets were ranked by height alone, so a vertical render was
    // told to "Pick YouTube (1080p landscape)" — switching platform to answer
    // a question nobody asked.
    renderCard({ quality: measured(360), upscale: true, presetSlug: "whatsapp_vertical" });
    // Scoped to the verdict: the preset dropdown legitimately lists every
    // preset by name, so a bare name match would hit those <option>s too.
    expect(screen.queryByText(/Pick YouTube/)).not.toBeInTheDocument();
  });

  it("does recommend a smaller preset of the same shape", () => {
    renderCard({ quality: measured(360), upscale: true, presetSlug: "youtube_landscape" });
    expect(
      screen.getByText(/Pick YouTube \(720p landscape\)/)
    ).toBeInTheDocument();
  });

  it("explains rather than silently skipping a too-long sharpen", () => {
    renderCard({ tooLongToUpscale: true, quality: measured(360) });
    expect(screen.getByText(/too long to sharpen/i)).toBeInTheDocument();
    // And is explicit that the render still happens.
    expect(screen.getByText(/render still works/i)).toBeInTheDocument();
  });

  it("says no sharpening is needed when the source already covers the target", () => {
    renderCard({ quality: measured(1080), upscale: false });
    expect(screen.getByText(/No sharpening needed/i)).toBeInTheDocument();
  });

  it("offers a re-check when the source could not be measured", async () => {
    const onRecheck = vi.fn();
    renderCard({ quality: "failed", onRecheck });

    await userEvent.click(screen.getByRole("button", { name: /check again/i }));

    expect(onRecheck).toHaveBeenCalled();
  });
});

describe("QualityCard — music removal", () => {
  const musicProps = {
    disabled: true,
    quality: measured(360),
    sourceHeightForScale: 360,
  };

  it("says the picture is kept as-is", () => {
    renderCard(musicProps);
    expect(screen.getByText(/keeps your picture exactly as it is/i)).toBeInTheDocument();
  });

  it("hides the render preset, which cannot apply to a copied stream", () => {
    renderCard(musicProps);
    expect(screen.queryByText(/Output preset/i)).not.toBeInTheDocument();
  });

  it("offers the 720p enlargement for a small source", async () => {
    const onScaleHeightChange = vi.fn();
    renderCard({ ...musicProps, scaleHeight: null, onScaleHeightChange });

    await userEvent.click(screen.getByRole("checkbox", { name: /enlarge/i }));

    expect(onScaleHeightChange).toHaveBeenCalledWith(720);
  });

  it("does not offer it when the source is already 720p or better", () => {
    renderCard({
      ...musicProps,
      quality: measured(1080),
      sourceHeightForScale: 1080,
      onScaleHeightChange: vi.fn(),
    });
    expect(screen.queryByRole("checkbox", { name: /enlarge/i })).not.toBeInTheDocument();
  });

  it("turns the enlargement back off", async () => {
    const onScaleHeightChange = vi.fn();
    renderCard({ ...musicProps, scaleHeight: 720, onScaleHeightChange });

    await userEvent.click(screen.getByRole("checkbox", { name: /enlarge/i }));

    expect(onScaleHeightChange).toHaveBeenCalledWith(null);
  });
});
