import { cookies } from "next/headers";

export const SESSION_COOKIE = "qs_session";
const SESSION_MAX_AGE = 60 * 60 * 24 * 30; // 30 days

function secret(): string {
  const s = process.env.APP_PASSWORD;
  if (!s) {
    throw new Error(
      "APP_PASSWORD is not set. The Studio must not be exposed without a password."
    );
  }
  return s;
}

const enc = new TextEncoder();

async function hmac(payload: string): Promise<string> {
  const key = await crypto.subtle.importKey(
    "raw",
    enc.encode(secret()),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"]
  );
  const sig = await crypto.subtle.sign("HMAC", key, enc.encode(payload));
  return Buffer.from(new Uint8Array(sig)).toString("base64url");
}

/** Constant-time-ish string compare. */
function safeEqual(a: string, b: string): boolean {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

export async function mintSessionToken(): Promise<string> {
  const issued = Date.now().toString(36);
  return `${issued}.${await hmac(issued)}`;
}

export async function verifySessionToken(token: string | undefined) {
  if (!token) return false;
  const [issued, sig] = token.split(".");
  if (!issued || !sig) return false;
  const expected = await hmac(issued);
  if (!safeEqual(sig, expected)) return false;
  const issuedMs = parseInt(issued, 36);
  if (!Number.isFinite(issuedMs)) return false;
  return Date.now() - issuedMs < SESSION_MAX_AGE * 1000;
}

export async function checkPassword(candidate: string): Promise<boolean> {
  return safeEqual(candidate, secret());
}

export async function isAuthenticated(): Promise<boolean> {
  const store = await cookies();
  return verifySessionToken(store.get(SESSION_COOKIE)?.value);
}

export const sessionCookieOptions = {
  httpOnly: true,
  secure: process.env.NODE_ENV === "production",
  sameSite: "lax" as const,
  path: "/",
  maxAge: SESSION_MAX_AGE,
};

// ------------------------------------------------------- worker auth

/**
 * The worker authenticates with a static shared secret in the
 * `x-worker-token` header. It never sees the user password.
 */
export function checkWorkerToken(req: Request): boolean {
  const expected = process.env.WORKER_TOKEN;
  if (!expected) return false;
  const got = req.headers.get("x-worker-token");
  if (!got) return false;
  return safeEqual(got, expected);
}
