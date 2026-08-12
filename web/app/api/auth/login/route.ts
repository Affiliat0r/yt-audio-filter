import { NextResponse } from "next/server";
import {
  SESSION_COOKIE,
  checkPassword,
  mintSessionToken,
  sessionCookieOptions,
} from "@/lib/auth";

export const runtime = "nodejs";

export async function POST(req: Request) {
  let password = "";
  try {
    const body = (await req.json()) as { password?: unknown };
    password = typeof body.password === "string" ? body.password : "";
  } catch {
    return NextResponse.json({ error: "Invalid body" }, { status: 400 });
  }

  if (!password || !(await checkPassword(password))) {
    // Blunt the brute-force edge without needing a rate limiter.
    await new Promise((r) => setTimeout(r, 600));
    return NextResponse.json({ error: "Wrong password" }, { status: 401 });
  }

  const res = NextResponse.json({ ok: true });
  res.cookies.set(SESSION_COOKIE, await mintSessionToken(), sessionCookieOptions);
  return res;
}
