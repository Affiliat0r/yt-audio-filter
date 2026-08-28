import { beforeEach, describe, expect, it, vi } from "vitest";

/**
 * The upload gate.
 *
 * "Upload to YouTube" re-queues an already-rendered job instead of rendering
 * again, so `requestUpload` is the only thing standing between the button and
 * a job that has no file to publish. It used to gate on a Vercel Blob URL;
 * Blob is gone, and the replacement gate is `result.localPath` — the path the
 * worker records once a render has landed on its disk.
 *
 * Both directions matter and both have to stay pinned: a real render must get
 * through, and a `search` job (which finishes `done` with no file at all) must
 * not.
 */

const store = new Map<string, unknown>();
const queue: string[] = [];
const zsets = new Map<string, Map<string, number>>();

// Upstash round-trips through JSON, so a caller never gets a live reference to
// what it stored. Cloning here keeps the fake honest: `jobs.ts` mutates the
// object it reads back, and a shared reference would hide a missing write.
const clone = <T>(value: T): T => structuredClone(value);

const fakeRedis = {
  get: vi.fn(async (key: string) => clone(store.get(key) ?? null)),
  set: vi.fn(async (key: string, value: unknown) => {
    store.set(key, clone(value));
    return "OK";
  }),
  del: vi.fn(async (key: string) => (store.delete(key) ? 1 : 0)),
  rpush: vi.fn(async (_key: string, id: string) => queue.push(id)),
  lpush: vi.fn(async (_key: string, id: string) => queue.unshift(id)),
  rpop: vi.fn(async () => queue.pop() ?? null),
  zadd: vi.fn(async (key: string, entry: { score: number; member: string }) => {
    const set = zsets.get(key) ?? new Map<string, number>();
    set.set(entry.member, entry.score);
    zsets.set(key, set);
    return 1;
  }),
  zrange: vi.fn(async () => [] as string[]),
};

vi.mock("@/lib/redis", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/redis")>()),
  redis: () => fakeRedis,
}));

const { completeJob, createJob, getJob, requestUpload } = await import(
  "@/lib/jobs"
);

import type { CatalogVideo, JobResult } from "@/lib/types";

const visual: CatalogVideo = {
  videoId: "abc12345xyz",
  url: "https://youtu.be/abc12345xyz",
  title: "A cartoon",
  duration: 120,
  viewCount: 10,
  uploadDate: "20250101",
  thumbnailUrl: "",
  channelSlug: "toyfactorycartoon",
};

async function finishedSurahJob(result: JobResult) {
  const job = await createJob({
    kind: "surah",
    surahNumbers: [1],
    reciterSlug: "alafasy",
    visual,
    settings: {
      presetSlug: "",
      burnSubtitles: false,
      upscale: false,
      metadataPath: "",
      playlistId: null,
    },
  });
  await completeJob(job.id, result);
  return job.id;
}

async function finishedSearchJob() {
  const job = await createJob({ kind: "search", query: "cartoon", maxResults: 5 });
  await completeJob(job.id, { searchResults: [visual] });
  return job.id;
}

beforeEach(() => {
  store.clear();
  queue.length = 0;
  zsets.clear();
  vi.clearAllMocks();
});

describe("requestUpload", () => {
  it("queues a finished render that has a file on the worker", async () => {
    const id = await finishedSurahJob({
      localPath: "D:/cache/AlFatiha.mp4",
      fileName: "AlFatiha.mp4",
      sizeBytes: 12_345,
    });
    queue.length = 0;

    const job = await requestUpload(id);

    expect(job?.uploadRequested).toBe(true);
    expect(job?.status).toBe("queued");
    expect(job?.progress.stage).toBe("Queued for upload");
    // Actually re-queued, not just flagged — otherwise no worker ever picks it.
    expect(queue).toEqual([id]);
    // And persisted, so a page reload does not lose the request.
    expect((await getJob(id))?.uploadRequested).toBe(true);
  });

  it("refuses a search job, which finishes done with nothing rendered", async () => {
    const id = await finishedSearchJob();
    queue.length = 0;

    const job = await requestUpload(id);

    expect(job?.uploadRequested).toBe(false);
    expect(job?.status).toBe("done");
    expect(queue).toEqual([]);
  });

  it("refuses a done job whose result names no file", async () => {
    // A render that reported metadata but no path: there is nothing on disk to
    // publish, and the worker would fail the upload after claiming it.
    const id = await finishedSurahJob({ fileName: "AlFatiha.mp4", sizeBytes: 0 });
    queue.length = 0;

    const job = await requestUpload(id);

    expect(job?.uploadRequested).toBe(false);
    expect(queue).toEqual([]);
  });

  it("refuses a job that has not finished yet", async () => {
    const id = await finishedSurahJob({ localPath: "D:/cache/AlFatiha.mp4" });
    const running = await getJob(id);
    await fakeRedis.set(`job:${id}`, { ...running, status: "running" });
    queue.length = 0;

    const job = await requestUpload(id);

    expect(job?.uploadRequested).toBe(false);
    expect(queue).toEqual([]);
  });

  it("returns null for a job that does not exist", async () => {
    expect(await requestUpload("nope")).toBeNull();
  });
});
