import { issueSignedToken, presignUrl } from "@vercel/blob";

/**
 * The render store is **private**: uploads carry
 * `x-vercel-blob-access: private` and an anonymous GET on a blob URL is 403.
 * The browser therefore never receives a raw blob URL — it hits
 * `/api/jobs/:id/preview`, which is gated by the Studio session cookie and
 * redirects to a short-lived presigned URL minted here.
 */

/** How long a minted preview URL stays valid. */
const PREVIEW_TTL_MS = 30 * 60 * 1000;

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

  return presignedUrl;
}
