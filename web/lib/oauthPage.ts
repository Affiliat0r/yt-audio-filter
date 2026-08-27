/**
 * Self-contained HTML for the two pages a browser lands on during remote
 * YouTube authorisation.
 *
 * These are plain `Response`s rather than Next pages on purpose: the OAuth
 * callback has to be a route handler (it reads query params and writes Redis
 * before rendering), and the tab it opens in is disposable — it exists to say
 * one sentence and be closed. Styles are inlined because nothing in `app/`
 * wraps a route handler's response.
 */

const ESCAPES: Record<string, string> = {
  "&": "&amp;",
  "<": "&lt;",
  ">": "&gt;",
  '"': "&quot;",
  "'": "&#39;",
};

/** Hostnames and worker ids come from the worker, so never trust them raw. */
export function escapeHtml(value: string): string {
  return value.replace(/[&<>"']/g, (c) => ESCAPES[c]);
}

export interface Notice {
  title: string;
  body: string;
  /** Green tick or amber warning. */
  ok: boolean;
  status?: number;
}

export function renderNotice({ title, body, ok, status = 200 }: Notice): Response {
  const accent = ok ? "#34d399" : "#fbbf24";
  const html = `<!doctype html>
<html lang="en" style="color-scheme: dark light">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>${escapeHtml(title)} — Quran Studio</title>
<style>
  :root { --bg:#0b0d10; --card:#14171c; --line:#262b33; --text:#e6e9ee; --mute:#98a2b3; }
  @media (prefers-color-scheme: light) {
    :root { --bg:#f6f7f9; --card:#ffffff; --line:#e3e6ea; --text:#12151a; --mute:#5b6472; }
  }
  * { box-sizing: border-box; }
  body { margin:0; min-height:100vh; display:grid; place-items:center; padding:24px;
         background:var(--bg); color:var(--text);
         font:16px/1.55 ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif; }
  main { max-width:34rem; width:100%; background:var(--card); border:1px solid var(--line);
         border-left:4px solid ${accent}; border-radius:14px; padding:26px 28px; }
  h1 { margin:0 0 10px; font-size:1.2rem; letter-spacing:-0.01em; }
  p  { margin:0; color:var(--mute); }
  code { background:rgba(127,127,127,.16); padding:.1em .35em; border-radius:5px;
         font:0.9em ui-monospace,SFMono-Regular,Menlo,monospace; }
</style>
</head>
<body>
  <main>
    <h1>${ok ? "✅" : "⚠️"} ${escapeHtml(title)}</h1>
    <p>${body}</p>
  </main>
</body>
</html>`;

  return new Response(html, {
    status,
    headers: {
      "content-type": "text/html; charset=utf-8",
      // The authorisation code rides in this page's URL. Neither a cache nor
      // an outbound Referer header should ever carry it anywhere.
      "cache-control": "no-store",
      "referrer-policy": "no-referrer",
    },
  });
}
