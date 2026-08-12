# Deploying Quran Studio (Vercel + local worker)

> **Live deployment:** https://quran-studio-mocha.vercel.app
> Project `quran-studio` under `hasans-projects-76845795`. Redis (Upstash
> `upstash-kv-claret-pillar`) and Blob (`quran-studio-renders`, public) are
> provisioned and linked. The steps below are the from-scratch recipe — for the
> existing deployment you only need step 5, "Run the worker".

The Studio is split in two:

| Piece | Runs on | Does |
|---|---|---|
| `web/` — Next.js | Vercel, always up | The whole UI, job queue API, previews |
| `worker/` — Python | Your PC | Downloads, FFmpeg, Demucs, Real-ESRGAN, NVENC, YouTube upload |

The worker **polls** Vercel over plain outbound HTTPS. There is no inbound
connection, no port forwarding, and no tunnel — it works behind NAT and
keeps your residential IP, which is what stops YouTube's bot detection from
blocking downloads.

Vercel cannot do the heavy work itself: no GPU, a 250 MB function bundle
limit (PyTorch alone is ~800 MB), no persistent filesystem for the 4.9 GB
`cache/`, and a hard execution ceiling far below a real render.

---

## 1. Provision Redis

Jobs live in Redis. Vercel Marketplace → **Upstash** → create a free database,
and link it to the project. That injects `KV_REST_API_URL` and
`KV_REST_API_TOKEN`.

You can equally create a free database at [upstash.com](https://upstash.com)
and set `UPSTASH_REDIS_REST_URL` / `UPSTASH_REDIS_REST_TOKEN` by hand. The app
accepts either pair.

## 2. Generate the two secrets

```bash
python -c "import secrets; print('APP_PASSWORD =', secrets.token_urlsafe(24))"
python -c "import secrets; print('WORKER_TOKEN =', secrets.token_urlsafe(32))"
```

`APP_PASSWORD` is what you type to sign in — **and** the key that signs session
cookies. Anyone holding it can publish to your YouTube channel.

## 3. Deploy the frontend

```bash
npm i -g vercel
cd web
vercel login
vercel link          # create a new project when prompted
```

Set the environment variables (Production + Preview + Development):

```bash
vercel env add APP_PASSWORD
vercel env add WORKER_TOKEN
# only if you are NOT using the Vercel↔Upstash integration:
vercel env add UPSTASH_REDIS_REST_URL
vercel env add UPSTASH_REDIS_REST_TOKEN
```

Then ship it:

```bash
vercel --prod
```

Note the deployment URL, e.g. `https://quran-studio.vercel.app`.

> **Root directory:** if you connect the GitHub repo through the Vercel
> dashboard instead of the CLI, set *Root Directory* to `web` so Vercel builds
> the Next.js app rather than the Python package.

## 4. Configure the worker

```bash
cp worker/.env.example worker/.env
```

Fill in:

```ini
STUDIO_BASE_URL=https://quran-studio.vercel.app
WORKER_TOKEN=<the same value you set on Vercel>
BLOB_READ_WRITE_TOKEN=<optional — see below>
```

### Vercel Blob (optional, enables in-browser preview)

Without it everything still renders; you just do not get a preview player or a
download button in the browser, because the MP4 never leaves your PC.

Vercel dashboard → **Storage → Blob → Create**, then copy the read-write token
into `worker/.env`. Verify it in one command:

```bash
python -m worker.worker --selftest-blob
```

## 5. Run the worker

```bash
worker\run_worker.bat
```

It pushes the cartoon catalog on startup, then polls for jobs. The Studio
header shows a green **Worker online** pill within about a minute.

To keep it running across reboots, register it as a scheduled task:

```powershell
schtasks /create /tn "QuranStudioWorker" /tr "C:\Users\hasan\yt-audio-filter\worker\run_worker.bat" /sc onlogon /rl highest
```

---

## Local development

You do not need a real Redis to work on the UI. `web/scripts/dev-redis.mjs` is
an in-memory stand-in that speaks enough of Upstash's REST protocol for the job
store (state is lost on restart — it is a test fixture, not a store):

```bash
cd web
node scripts/dev-redis.mjs 8790      # terminal 1
npm run dev                          # terminal 2
```

with `web/.env.local`:

```ini
APP_PASSWORD=devpassword
WORKER_TOKEN=devworkertoken
UPSTASH_REDIS_REST_URL=http://localhost:8790
UPSTASH_REDIS_REST_TOKEN=devtoken
```

Point the worker at `STUDIO_BASE_URL=http://localhost:3000` to exercise the
full loop offline.

## Regenerating the baked-in data

`web/data/*.json` (surahs, reciters, presets, channels) is generated from the
Python source of truth. After changing reciters, presets, or `config/channels.json`:

```bash
python scripts/sync_web_data.py
git diff web/data
```

## Day-to-day

Open the Vercel URL from any device, sign in, pick a video, choose a mode,
render. The job queues instantly; if the PC is asleep it runs as soon as the
worker is back. Nothing uploads to YouTube until you press **Upload to
YouTube** on a finished render.
