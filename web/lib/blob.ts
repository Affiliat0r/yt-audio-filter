import { issueSignedToken, presignUrl } from "@vercel/blob";
import { KEYS, redis } from "./redis";

/**
 * The render store is **private**: uploads carry
 * `x-vercel-blob-access: private` and an anonymous GET on a blob URL is 403.
 * The browser therefore never receives a raw blob URL — it hits
 * `/api/jobs/:id/preview`, which is gated by the Studio session cookie and
 * redirects to a short-lived presigned URL minted here.
 */

/** How long a minted preview URL stays valid. */
const PREVIEW_TTL_MS = 30 * 60 * 1000;

/**
 * How long we reuse a minted URL. Deliberately shorter than its validity so a
 * cached URL never expires in someone's hands mid-playback.
 *
 * This cache exists to protect the Blob operations quota, not for speed.
 * `issueSignedToken` is a control-plane API call, and the preview route was
 * minting a fresh one on every request — including every re-render and every
 * player reload. On the free tier's 10,000 monthly operations that adds up
 * quickly, and it is pure waste: the URL is valid for half an hour.
 */
const PRESIGN_CACHE_MS = 20 * 60 * 1000;

export function blobConfigured(): boolean {
  return !!process.env.BLOB_READ_WRITE_TOKEN;
}

/**
 * Mint a short-lived signed GET URL for one blob pathname.
 *
 * The delegation is scoped to that single pathname and to `get` only, so a
 * leaked URL exposes exactly one render for at most {@link PREVIEW_TTL_MS}.
 */
export async function presignPreviewUrl(pathname: string): Promise<string> {
  const token = process.env.BLOB_READ_WRITE_TOKEN;
  if (!token) throw new Error("BLOB_READ_WRITE_TOKEN is not configured");

  const r = redis();
  const cacheKey = KEYS.blobPresign(pathname);

  const cached = await r.get<string>(cacheKey);
  if (cached) return cached;

  const validUntil = Date.now() + PREVIEW_TTL_MS;

  const signed = await issueSignedToken({
    token,
    pathname,
    operations: ["get"],
    validUntil,
  });

  const { presignedUrl } = await presignUrl(signed, {
    operation: "get",
    access: "private",
    pathname,
    validUntil,
  });

  // Redis is already in the request path, so caching here costs nothing extra
  // and removes a Blob API call per view.
  await r.set(cacheKey, presignedUrl, { px: PRESIGN_CACHE_MS });

  return presignedUrl;
}

/**
 * Drop a blob's cached signature. Called when the blob is deleted, so a stale
 * URL is not served for an object that no longer exists.
 */
export async function forgetPresignedUrl(pathname: string): Promise<void> {
  await redis().del(KEYS.blobPresign(pathname));
}
