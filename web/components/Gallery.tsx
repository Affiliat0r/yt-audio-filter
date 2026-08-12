"use client";

import { useEffect, useMemo, useState } from "react";
import {
  createJob,
  fetchCatalog,
  fetchJob,
  formatDuration,
  formatViews,
} from "@/lib/client";
import type { CatalogVideo } from "@/lib/types";

const PAGE_SIZE = 24;

export interface Channel {
  slug: string;
  handle: string;
  url: string;
  display_name: string;
}

type SourceMode = "curated" | "search";

const CURATED_SORTS = [
  "Downloaded first",
  "Longest first",
  "Shortest first",
  "Most viewed",
  "Newest",
  "Title A-Z",
] as const;

const SEARCH_SORTS = [
  "Most viewed",
  "Longest first",
  "Shortest first",
  "Newest",
  "Title A-Z",
] as const;

type SortMode = (typeof CURATED_SORTS)[number];

function sortVideos(videos: CatalogVideo[], mode: string): CatalogVideo[] {
  const out = [...videos];
  switch (mode) {
    case "Longest first":
      return out.sort((a, b) => b.duration - a.duration);
    case "Shortest first":
      return out.sort((a, b) => a.duration - b.duration);
    case "Most viewed":
      return out.sort((a, b) => b.viewCount - a.viewCount);
    case "Newest":
      return out.sort((a, b) => (b.uploadDate || "").localeCompare(a.uploadDate || ""));
    case "Title A-Z":
      return out.sort((a, b) => a.title.localeCompare(b.title));
    case "Downloaded first":
    default: {
      const rank = { upscaled: 0, downloaded: 1, new: 2 } as const;
      return out.sort((a, b) => {
        const ra = rank[a.cacheState ?? "new"];
        const rb = rank[b.cacheState ?? "new"];
        return ra !== rb ? ra - rb : b.viewCount - a.viewCount;
      });
    }
  }
}

function CacheBadge({ state }: { state?: CatalogVideo["cacheState"] }) {
  if (state === "upscaled")
    return <span className="pill">🔵 Upscaled — ready</span>;
  if (state === "downloaded")
    return <span className="pill">🟢 Downloaded — ready</span>;
  return <span className="pill">⚪ Will download</span>;
}

function VideoTile({
  video,
  selected,
  onSelect,
}: {
  video: CatalogVideo;
  selected: boolean;
  onSelect: (v: CatalogVideo) => void;
}) {
  return (
    <div
      className={`overflow-hidden rounded-xl border transition ${
        selected
          ? "border-accent ring-1 ring-accent"
          : "border-ink-800 hover:border-ink-600"
      }`}
    >
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        src={video.thumbnailUrl || `https://i.ytimg.com/vi/${video.videoId}/hqdefault.jpg`}
        alt=""
        loading="lazy"
        className="aspect-video w-full bg-ink-850 object-cover"
      />
      <div className="space-y-2 p-3">
        <p className="line-clamp-2 min-h-[2.5rem] text-xs leading-snug text-ink-100">
          {video.title}
        </p>
        <div className="flex flex-wrap items-center gap-1.5 text-[11px] text-ink-400">
          <span>{formatDuration(video.duration)}</span>
          <span>·</span>
          <span>{formatViews(video.viewCount)} views</span>
        </div>
        <CacheBadge state={video.cacheState} />
        <button
          onClick={() => onSelect(video)}
          className={selected ? "btn-primary w-full" : "btn-ghost w-full"}
        >
          {selected ? "✓ Selected" : "Select"}
        </button>
      </div>
    </div>
  );
}

export default function Gallery({
  channels,
  selected,
  onSelect,
  targetWorkerId,
}: {
  channels: Channel[];
  selected: CatalogVideo | null;
  onSelect: (v: CatalogVideo | null) => void;
  /** Whose disk the cache badges should describe. */
  targetWorkerId: string | null;
}) {
  const [mode, setMode] = useState<SourceMode>("curated");

  // Curated state
  const [catalog, setCatalog] = useState<CatalogVideo[]>([]);
  const [catalogUpdatedAt, setCatalogUpdatedAt] = useState<number | null>(null);
  const [loadingCatalog, setLoadingCatalog] = useState(true);
  const [catalogError, setCatalogError] = useState<string | null>(null);
  const [titleFilter, setTitleFilter] = useState("");
  const [activeSlugs, setActiveSlugs] = useState<Set<string>>(
    () => new Set(channels.map((c) => c.slug))
  );
  const [sort, setSort] = useState<SortMode>("Downloaded first");
  const [page, setPage] = useState(0);

  // Search state
  const [query, setQuery] = useState("");
  const [maxResults, setMaxResults] = useState(25);
  const [searchResults, setSearchResults] = useState<CatalogVideo[]>([]);
  const [searching, setSearching] = useState(false);
  const [searchError, setSearchError] = useState<string | null>(null);
  const [searchSort, setSearchSort] = useState<string>("Most viewed");
  const [searchPage, setSearchPage] = useState(0);

  const loadCatalog = async () => {
    setLoadingCatalog(true);
    setCatalogError(null);
    try {
      const { videos, updatedAt } = await fetchCatalog(targetWorkerId);
      setCatalog(videos);
      setCatalogUpdatedAt(updatedAt);
    } catch (e) {
      setCatalogError(e instanceof Error ? e.message : "Failed to load catalog");
    } finally {
      setLoadingCatalog(false);
    }
  };

  useEffect(() => {
    void loadCatalog();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [targetWorkerId]);

  const curatedPool = useMemo(
    () => catalog.filter((v) => v.channelSlug !== "__search__"),
    [catalog]
  );

  const filtered = useMemo(() => {
    const needle = titleFilter.trim().toLowerCase();
    const base = curatedPool.filter(
      (v) =>
        activeSlugs.has(v.channelSlug) &&
        (!needle || v.title.toLowerCase().includes(needle))
    );
    return sortVideos(base, sort);
  }, [curatedPool, activeSlugs, titleFilter, sort]);

  // Any filter change puts you back on page 1, otherwise you land on an empty page.
  useEffect(() => setPage(0), [titleFilter, sort, activeSlugs]);

  const sortedSearch = useMemo(
    () => sortVideos(searchResults, searchSort),
    [searchResults, searchSort]
  );

  const runSearch = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!query.trim()) return;
    setSearching(true);
    setSearchError(null);
    setSearchPage(0);
    try {
      // Search runs on the worker (yt-dlp cannot run on Vercel), so it is a
      // job like any other — just a fast one.
      const job = await createJob({
        kind: "search",
        query: query.trim(),
        maxResults,
      });
      const deadline = Date.now() + 90_000;
      for (;;) {
        await new Promise((r) => setTimeout(r, 1200));
        const cur = await fetchJob(job.id);
        if (cur.status === "done") {
          setSearchResults(cur.result?.searchResults ?? []);
          break;
        }
        if (cur.status === "error" || cur.status === "cancelled") {
          setSearchError(cur.error ?? "Search failed");
          break;
        }
        if (Date.now() > deadline) {
          setSearchError(
            "Search timed out. Is the worker running on your PC?"
          );
          break;
        }
      }
    } catch (err) {
      setSearchError(err instanceof Error ? err.message : "Search failed");
    } finally {
      setSearching(false);
    }
  };

  const activePool = mode === "curated" ? filtered : sortedSearch;
  const activePage = mode === "curated" ? page : searchPage;
  const setActivePage = mode === "curated" ? setPage : setSearchPage;
  const totalPages = Math.max(1, Math.ceil(activePool.length / PAGE_SIZE));
  const pageItems = activePool.slice(
    activePage * PAGE_SIZE,
    activePage * PAGE_SIZE + PAGE_SIZE
  );

  const toggleSlug = (slug: string) => {
    setActiveSlugs((prev) => {
      const next = new Set(prev);
      if (next.has(slug)) next.delete(slug);
      else next.add(slug);
      return next;
    });
  };

  const readyCount = filtered.filter(
    (v) => v.cacheState === "downloaded" || v.cacheState === "upscaled"
  ).length;

  return (
    <section className="card space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-ink-300">
          1 · Pick a video
        </h2>
        <div className="flex rounded-lg border border-ink-700 p-0.5">
          {(["curated", "search"] as const).map((m) => (
            <button
              key={m}
              onClick={() => setMode(m)}
              className={`rounded-md px-3 py-1.5 text-xs font-medium transition ${
                mode === m
                  ? "bg-accent text-white"
                  : "text-ink-300 hover:text-ink-100"
              }`}
            >
              {m === "curated" ? "Curated channels" : "YouTube search"}
            </button>
          ))}
        </div>
      </div>

      {selected && (
        <div className="flex items-center justify-between gap-3 rounded-lg border border-accent/40 bg-accent/10 px-3 py-2">
          <p className="truncate text-sm">
            <span className="text-ink-400">Selected:</span>{" "}
            <span className="font-medium">{selected.title}</span>{" "}
            <span className="text-ink-400">
              · {formatDuration(selected.duration)}
            </span>
          </p>
          <button onClick={() => onSelect(null)} className="btn-ghost shrink-0">
            Clear
          </button>
        </div>
      )}

      {mode === "curated" ? (
        <div className="space-y-3">
          <div className="grid gap-3 sm:grid-cols-[1fr_auto_auto]">
            <input
              className="field"
              placeholder="Filter by title — e.g. train, bus, dinosaur"
              value={titleFilter}
              onChange={(e) => setTitleFilter(e.target.value)}
            />
            <select
              className="field sm:w-48"
              value={sort}
              onChange={(e) => setSort(e.target.value as SortMode)}
            >
              {CURATED_SORTS.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
            <button
              onClick={loadCatalog}
              className="btn-ghost"
              disabled={loadingCatalog}
            >
              {loadingCatalog ? "Loading…" : "Refresh"}
            </button>
          </div>

          <div className="flex flex-wrap gap-2">
            {channels.map((c) => (
              <button
                key={c.slug}
                onClick={() => toggleSlug(c.slug)}
                className={`rounded-full border px-3 py-1 text-xs transition ${
                  activeSlugs.has(c.slug)
                    ? "border-accent bg-accent/15 text-ink-100"
                    : "border-ink-700 text-ink-400 hover:border-ink-600"
                }`}
              >
                {c.display_name}
              </button>
            ))}
          </div>

          {catalogError ? (
            <p className="rounded-lg bg-red-500/10 px-3 py-2 text-sm text-red-300">
              {catalogError}
            </p>
          ) : (
            <p className="text-xs text-ink-400">
              Showing {pageItems.length} of {filtered.length} curated videos ·{" "}
              {readyCount} already cached on the target machine
              {catalogUpdatedAt
                ? ` · catalog synced ${new Date(catalogUpdatedAt).toLocaleString()}`
                : ""}
            </p>
          )}

          {!loadingCatalog && catalog.length === 0 && !catalogError && (
            <p className="rounded-lg border border-ink-700 bg-ink-850 px-3 py-4 text-sm text-ink-300">
              The catalog is empty. Start the worker on your PC — it pushes the
              cartoon catalog up on startup.
            </p>
          )}
        </div>
      ) : (
        <div className="space-y-3">
          <form onSubmit={runSearch} className="grid gap-3 sm:grid-cols-[1fr_auto_auto_auto]">
            <input
              className="field"
              placeholder="Keyword search — e.g. police car, dinosaur, fire truck"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
            <select
              className="field sm:w-28"
              value={maxResults}
              onChange={(e) => setMaxResults(Number(e.target.value))}
            >
              {[15, 25, 40].map((n) => (
                <option key={n} value={n}>
                  {n} results
                </option>
              ))}
            </select>
            <select
              className="field sm:w-44"
              value={searchSort}
              onChange={(e) => setSearchSort(e.target.value)}
            >
              {SEARCH_SORTS.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
            <button type="submit" className="btn-primary" disabled={searching}>
              {searching ? "Searching…" : "Search"}
            </button>
          </form>

          {searchError && (
            <p className="rounded-lg bg-red-500/10 px-3 py-2 text-sm text-red-300">
              {searchError}
            </p>
          )}
          {!searchError && searchResults.length > 0 && (
            <p className="text-xs text-ink-400">
              {searchResults.length} results. Picking one adds it to your
              catalog so the worker can render it.
            </p>
          )}
        </div>
      )}

      {pageItems.length > 0 && (
        <>
          <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-4">
            {pageItems.map((v) => (
              <VideoTile
                key={v.videoId}
                video={v}
                selected={selected?.videoId === v.videoId}
                onSelect={(picked) =>
                  onSelect(selected?.videoId === picked.videoId ? null : picked)
                }
              />
            ))}
          </div>

          {totalPages > 1 && (
            <div className="flex items-center justify-center gap-3">
              <button
                className="btn-ghost"
                disabled={activePage === 0}
                onClick={() => setActivePage(activePage - 1)}
              >
                ◀ Previous
              </button>
              <span className="text-xs text-ink-400">
                Page {activePage + 1} of {totalPages}
              </span>
              <button
                className="btn-ghost"
                disabled={activePage >= totalPages - 1}
                onClick={() => setActivePage(activePage + 1)}
              >
                Next ▶
              </button>
            </div>
          )}
        </>
      )}
    </section>
  );
}
