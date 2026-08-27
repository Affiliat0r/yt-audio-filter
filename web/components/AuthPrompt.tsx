"use client";

import { usePendingAuth } from "@/lib/client";

/**
 * "⚠ <hostname> needs authorising with YouTube."
 *
 * The whole point of the remote flow: the worker is a PC in another room (or
 * another building), its refresh token has died, and the person who can fix it
 * is holding a phone. Pressing Authorise here opens Google's consent screen on
 * *this* device, and the worker collects the result on its next poll.
 */
export default function AuthPrompt() {
  const pending = usePendingAuth();
  if (!pending.length) return null;

  return (
    <div className="space-y-2">
      {pending.map((req) => (
        <div
          key={req.workerId}
          className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-sm text-amber-200"
        >
          <p>
            <span aria-hidden>⚠ </span>
            <strong>{req.hostname}</strong> needs authorising with YouTube. Its
            saved sign-in expired, so uploads from that machine will fail until
            you approve it.
          </p>
          <a
            className="btn-primary shrink-0"
            href={`/api/auth/youtube/start?workerId=${encodeURIComponent(
              req.workerId
            )}`}
            // A new tab keeps whatever is composed in the Studio intact — the
            // consent screen is a detour, not a destination.
            target="_blank"
            rel="noreferrer"
          >
            Authorise
          </a>
        </div>
      ))}
    </div>
  );
}
