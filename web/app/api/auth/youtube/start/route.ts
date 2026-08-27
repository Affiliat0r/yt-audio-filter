import { NextResponse } from "next/server";
import { isAuthenticated } from "@/lib/auth";
import { renderNotice, escapeHtml } from "@/lib/oauthPage";
import { GOOGLE_AUTH_ENDPOINT, YOUTUBE_OAUTH_SCOPES } from "@/lib/types";
import { getAuthRequest } from "@/lib/youtubeAuth";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

/** The redirect URI must match what the OAuth client has registered, exactly. */
function redirectUri(req: Request): string {
  return (
    process.env.GOOGLE_OAUTH_REDIRECT_URI ??
    new URL("/api/auth/youtube/callback", req.url).toString()
  );
}

/**
 * Send the signed-in user to Google's consent screen on the worker's behalf.
 *
 * Session-gated: this is the step that decides *whose* YouTube channel the
 * worker gets to publish to, so it must be a person who can already sign in to
 * the Studio. The unguessable `state` then carries that decision through
 * Google's redirect back, which cannot present a cookie.
 *
 * Note what is *not* here: no client secret. The Studio holds only the client
 * id and the challenge; the code that comes back is worthless without the
 * verifier and secret sitting on the worker.
 */
export async function GET(req: Request) {
  if (!(await isAuthenticated())) {
    // A browser landed here, so bounce to the sign-in page rather than
    // answering a human with a JSON 401.
    return NextResponse.redirect(new URL("/login", req.url));
  }

  const workerId = new URL(req.url).searchParams.get("workerId") ?? "";
  if (!workerId) {
    return renderNotice({
      ok: false,
      status: 400,
      title: "No machine specified",
      body: "This link is missing its <code>workerId</code>. Open the Studio and press <strong>Authorise</strong> on the machine that asked.",
    });
  }

  const clientId = process.env.GOOGLE_OAUTH_CLIENT_ID;
  if (!clientId) {
    return renderNotice({
      ok: false,
      status: 500,
      title: "Google sign-in is not configured",
      body: "Set <code>GOOGLE_OAUTH_CLIENT_ID</code> (and optionally <code>GOOGLE_OAUTH_REDIRECT_URI</code>) in the Vercel project's environment variables, then redeploy. The client <em>secret</em> belongs on the worker only — never here.",
    });
  }

  const pending = await getAuthRequest(workerId);
  if (!pending) {
    return renderNotice({
      ok: false,
      status: 404,
      title: "Nothing is waiting to be authorised",
      body: `No live authorisation request for <code>${escapeHtml(
        workerId
      )}</code>. Requests expire after 15 minutes — queue the upload again and press <strong>Authorise</strong> when the prompt reappears.`,
    });
  }

  const url = new URL(GOOGLE_AUTH_ENDPOINT);
  url.searchParams.set("client_id", clientId);
  url.searchParams.set("redirect_uri", redirectUri(req));
  url.searchParams.set("response_type", "code");
  url.searchParams.set("scope", YOUTUBE_OAUTH_SCOPES.join(" "));
  // Without offline access Google returns an access token that dies in an hour
  // and no refresh token — which is exactly the state we are recovering from.
  url.searchParams.set("access_type", "offline");
  // And without a forced consent screen, a re-authorisation of an account that
  // has already granted these scopes comes back *without* a refresh token.
  url.searchParams.set("prompt", "consent");
  url.searchParams.set("include_granted_scopes", "true");
  url.searchParams.set("state", pending.state);
  url.searchParams.set("code_challenge", pending.codeChallenge);
  url.searchParams.set("code_challenge_method", "S256");

  return NextResponse.redirect(url.toString(), {
    headers: { "cache-control": "no-store" },
  });
}
