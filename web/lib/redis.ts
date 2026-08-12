import { Redis } from "@upstash/redis";

let client: Redis | null = null;

/**
 * Upstash Redis over REST — the only store that works from Vercel's
 * serverless runtime without a connection pool.
 *
 * Accepts either the Upstash-native env names or the ones Vercel's
 * Marketplace integration injects (`KV_REST_API_*`), so the same code works
 * whichever way the store was provisioned.
 */
export function redis(): Redis {
  if (client) return client;

  const url =
    process.env.UPSTASH_REDIS_REST_URL ?? process.env.KV_REST_API_URL;
  const token =
    process.env.UPSTASH_REDIS_REST_TOKEN ?? process.env.KV_REST_API_TOKEN;

  if (!url || !token) {
    throw new Error(
      "Redis is not configured. Set UPSTASH_REDIS_REST_URL and " +
        "UPSTASH_REDIS_REST_TOKEN (or the KV_REST_API_* equivalents)."
    );
  }

  client = new Redis({ url, token });
  return client;
}

export const KEYS = {
  job: (id: string) => `job:${id}`,
  queue: "queue:pending",
  jobIndex: "jobs:index",
  catalog: "catalog:videos",
  catalogUpdatedAt: "catalog:updatedAt",
  /** Legacy single-worker heartbeat; kept so an un-upgraded worker still shows. */
  heartbeat: "worker:heartbeat",
  /** Per-worker heartbeat, now that several machines can run one each. */
  workerHeartbeat: (id: string) => `worker:hb:${id}`,
  /** Set of known worker ids, so they can be listed without a KEYS scan. */
  workerIndex: "worker:index",
} as const;

/** Jobs older than this are pruned from the index on write. */
export const JOB_TTL_SECONDS = 60 * 60 * 24 * 7;
