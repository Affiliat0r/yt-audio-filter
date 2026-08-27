import { escapeHtml, renderNotice } from "@/lib/oauthPage";
import { resolveState, storeAuthCode } from "@/lib/youtubeAuth";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

/**
 * Where Google sends the browser after consent.
 *
 * **Deliberately not session-gated.** The caller is Google's redirect, which
 * carries no cookie; requiring one would break the flow in any browser that
 * drops cross-site cookies. What authorises it instead is the `state` — 32
 * random bytes that only ever existed inside a request the worker started and
 * a URL the signed-in user was handed. An unrecognised state is treated as
 * hostile: nothing is stored, and the page says so.
 *
 * The code is parked for the worker rather than exchanged here, because the
 * exchange needs the PKCE verifier and the client secret, and neither is —
 * or should ever be — available to this function.
 */
export async function GET(req: Request) {
  const params = new URL(req.url).searchParams;
  const state = params.get("state") ?? "";
  const code = params.get("code") ?? "";
  const error = params.get("error");

  if (error) {
    return renderNotice({
      ok: false,
      status: 400,
      title: "Authorisation was declined",
      body: `Google reported <code>${escapeHtml(
        error
      )}</code>. Nothing was changed. You can close this tab and try again from the Studio.`,
    });
  }

  if (!state || !code) {
    return renderNotice({
      ok: false,
      status: 400,
      title: "Incomplete response from Google",
      body: "The redirect arrived without a state or a code, so there is nothing to relay. Start again from the Studio.",
    });
  }

  const pending = await resolveState(state);
  if (!pending) {
    // Unknown or expired. Storing anything here would let an unauthenticated
    // caller plant a code against a flow of their choosing.
    return renderNotice({
      ok: false,
      status: 400,
      title: "This authorisation link has expired",
      body: "Requests are only valid for 15 minutes, and nothing was stored for this one. Return to the Studio and press <strong>Authorise</strong> again.",
    });
  }

  await storeAuthCode(state, code);

  return renderNotice({
    ok: true,
    title: "Authorised",
    body: `<strong>${escapeHtml(
      pending.hostname
    )}</strong> will pick this up within a few seconds and finish the upload. You can close this tab and return to the Studio.`,
  });
}
