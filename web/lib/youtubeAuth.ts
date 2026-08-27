/**
 * Remote YouTube authorisation store.
 *
 * The worker runs unattended, so when its saved OAuth credentials die there is
 * nobody at that machine to click "Allow". This is the relay that lets the
 * consent happen from a phone: the worker registers a request here, the Studio
 * shows a button, Google redirects back here with an authorisation code, and
 * the worker collects it on its next poll.
 *
 * **What the Studio deliberately cannot do:** redeem any of it. The exchange
 * needs the PKCE `code_verifier` *and* the OAuth client secret, and both stay
 * on the worker. Everything stored here is therefore useless on its own — a
 * leaked Redis dump buys an attacker nothing.
 *
 * Every key is short-lived (see `AUTH_REQUEST_TTL_SECONDS` /
 * `AUTH_CODE_TTL_SECONDS`) so an abandoned flow disappears without anything
 * having to reap it.
 */

import { KEYS, redis } from "./redis";
import {
  AUTH_CODE_TTL_SECONDS,
  AUTH_REQUEST_TTL_SECONDS,
  type AuthCodeResponse,
  type PendingAuthRequest,
  type PendingAuthSummary,
} from "./types";

/** `secrets.token_urlsafe(32)` is 43 chars; anything shorter is not ours. */
const MIN_STATE_CHARS = 32;

/** base64url(sha256(...)) unpadded is always exactly 43 chars. */
const S256_CHALLENGE_CHARS = 43;

const BASE64URL = /^[A-Za-z0-9_-]+$/;

/**
 * Reject a malformed registration before it reaches Redis.
 *
 * Returns the problem, or `null` when the payload is usable. Being strict here
 * is cheap insurance: the state is the *only* thing authorising Google's
 * unauthenticated callback, so a short or non-random one would quietly weaken
 * the whole flow.
 */
export function validateAuthRequest(raw: {
  workerId?: unknown;
  hostname?: unknown;
  state?: unknown;
  codeChallenge?: unknown;
}): string | null {
  if (typeof raw.workerId !== "string" || !raw.workerId.trim())
    return "workerId is required";
  if (typeof raw.hostname !== "string" || !raw.hostname.trim())
    return "hostname is required";
  if (typeof raw.state !== "string" || raw.state.length < MIN_STATE_CHARS)
    return `state must be at least ${MIN_STATE_CHARS} characters`;
  if (!BASE64URL.test(raw.state)) return "state must be urlsafe base64";
  if (
    typeof raw.codeChallenge !== "string" ||
    raw.codeChallenge.length !== S256_CHALLENGE_CHARS ||
    !BASE64URL.test(raw.codeChallenge)
  )
    return "codeChallenge must be an unpadded base64url SHA-256 digest";
  return null;
}

/**
 * Register (or refresh) a worker's request for consent.
 *
 * Re-registering the same `state` is deliberately allowed: a worker that
 * restarts mid-flow keeps its verifier on disk and re-announces itself, which
 * refreshes the TTL rather than stranding a half-finished flow.
 */
export async function createAuthRequest(input: {
  workerId: string;
  hostname: string;
  state: string;
  codeChallenge: string;
}): Promise<PendingAuthRequest> {
  const r = redis();
  const now = Date.now();
  const record: PendingAuthRequest = {
    workerId: input.workerId,
    hostname: input.hostname,
    state: input.state,
    codeChallenge: input.codeChallenge,
    createdAt: now,
    expiresAt: now + AUTH_REQUEST_TTL_SECONDS * 1000,
  };

  await r.set(KEYS.authRequest(record.workerId), record, {
    ex: AUTH_REQUEST_TTL_SECONDS,
  });
  // The callback arrives from Google knowing only the state, so it needs its
  // own way back to the record.
  await r.set(KEYS.authState(record.state), record.workerId, {
    ex: AUTH_REQUEST_TTL_SECONDS,
  });
  await r.zadd(KEYS.authIndex, { score: now, member: record.workerId });
  return record;
}

export async function getAuthRequest(
  workerId: string
): Promise<PendingAuthRequest | null> {
  if (!workerId) return null;
  const record = await redis().get<PendingAuthRequest>(
    KEYS.authRequest(workerId)
  );
  return record ?? null;
}

/**
 * The record a `state` belongs to, or `null` when it is unknown or expired.
 *
 * An unknown state is the security-relevant case: it means the caller did not
 * come from a flow we started, and the callback must store nothing.
 */
export async function resolveState(
  state: string
): Promise<PendingAuthRequest | null> {
  if (!state) return null;
  const workerId = await redis().get<string>(KEYS.authState(state));
  if (!workerId) return null;
  const record = await getAuthRequest(workerId);
  // Belt and braces: the index could outlive the record if one key was
  // rewritten with a shorter TTL. A mismatched state is not this flow's.
  if (!record || record.state !== state) return null;
  return record;
}

/** Park Google's authorisation code for the worker to collect. */
export async function storeAuthCode(
  state: string,
  code: string
): Promise<void> {
  await redis().set(KEYS.authCode(state), code, { ex: AUTH_CODE_TTL_SECONDS });
}

/**
 * Hand the code to the worker — once.
 *
 * `getdel` is what makes it single-use: a replayed poll (or a second worker
 * sharing the token) finds nothing. Collecting the code also retires the whole
 * request, which is what makes the Studio's prompt disappear.
 */
export async function takeAuthCode(state: string): Promise<AuthCodeResponse> {
  const record = await resolveState(state);
  if (!record) return { state, status: "expired", code: null };

  const code = await redis().getdel<string>(KEYS.authCode(state));
  if (!code) return { state, status: "pending", code: null };

  await clearAuthRequest(record.workerId, state);
  return { state, status: "ready", code };
}

/** Forget a request: nothing left to authorise, nothing left to collect. */
export async function clearAuthRequest(
  workerId: string,
  state?: string
): Promise<void> {
  const r = redis();
  const known = state ?? (await getAuthRequest(workerId))?.state;
  await r.del(KEYS.authRequest(workerId));
  if (known) await r.del(KEYS.authState(known), KEYS.authCode(known));
  await r.zrem(KEYS.authIndex, workerId);
}

/**
 * Workers currently waiting on consent, newest first.
 *
 * Index entries whose record has expired are dropped as they are found — the
 * records carry the TTL, the index is only a hint about where to look.
 */
export async function listPendingAuth(): Promise<PendingAuthSummary[]> {
  const r = redis();
  const ids = await r.zrange<string[]>(KEYS.authIndex, 0, 49, { rev: true });
  if (!ids.length) return [];

  const records = await Promise.all(ids.map((id) => getAuthRequest(id)));
  const stale = ids.filter((_, i) => !records[i]);
  if (stale.length) await r.zrem(KEYS.authIndex, ...stale);

  const now = Date.now();
  return records
    .filter((rec): rec is PendingAuthRequest => !!rec && rec.expiresAt > now)
    .map((rec) => ({
      workerId: rec.workerId,
      hostname: rec.hostname,
      createdAt: rec.createdAt,
      expiresAt: rec.expiresAt,
    }));
}
