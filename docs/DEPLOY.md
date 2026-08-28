# Deploying Quran Studio (Vercel + local worker)

> **Live deployment:** https://quran-studio-mocha.vercel.app
> Project `quran-studio` under `hasans-projects-76845795`. Redis (Upstash
> `upstash-kv-claret-pillar`) is provisioned and linked. The steps below are the
> from-scratch recipe — for the existing deployment you only need step 5, "Run
> the worker".

The Studio is split in two:

| Piece | Runs on | Does |
|---|---|---|
| `web/` — Next.js | Vercel, always up | The whole UI and the job queue API |
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
```

> **Renders never leave the worker.** The Studio shows the finished file's
> name, size, and path on that machine; there is no in-browser player and no
> download. Uploading to YouTube is how a render becomes watchable elsewhere.

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

## Running a worker on a second machine

Every machine you want to render on runs its own worker, and jobs follow you:
open the Studio on a laptop and the work runs *there*.

How the Studio knows which machine you are on: the worker serves
`http://127.0.0.1:7717/whoami`, the page probes it on load, and a hit means
that worker shares a machine with the browser. Jobs are then tagged with its
`workerId` and only that worker may claim them. No local worker (a phone, say)
falls back to "any available machine". The sidebar's **Run the work on**
selector overrides it either way.

On the new machine, one command in PowerShell:

```powershell
irm https://raw.githubusercontent.com/Affiliat0r/yt-audio-filter/main/scripts/install_worker.ps1 | iex
```

It checks prerequisites, clones the repo, builds a venv, installs the worker,
prompts for the Studio URL and `WORKER_TOKEN`, registers a hidden auto-start
task, and confirms the worker answered on `127.0.0.1:7717`.

**It installs the light worker by default — about 300 MB.** That covers
downloads, overlay renders, search, and YouTube upload: everything except
music removal. Demucs and PyTorch add roughly 5 GB and are only worth
installing on a machine with an NVIDIA GPU:

```powershell
.\scripts\install_worker.ps1 -WithMusicRemoval
```

Ask a light worker for a music-removal job and it fails with a message saying
so, rather than crashing — target the GPU machine for those instead.

**Without a GPU it still works, with caveats:**

| | |
|---|---|
| Music removal (Demucs) | Falls back to CPU — roughly 10-20x slower. A 5-minute video can exceed an hour. |
| Overlay renders | libx264 instead of NVENC; 2-3x slower, same output. |
| `--upscale` (Real-ESRGAN) | Needs Vulkan. Fails rather than degrades on machines without it. |
| YouTube upload | Needs its own OAuth consent on that machine — credentials are deliberately machine-local. |

The discovery endpoint binds `127.0.0.1` only and is unreachable from the
network. It serves one path and returns nothing but id, hostname, GPU, and
version. Disable it with `--no-discovery` (the target selector then falls back
to a manual choice); move it with `WORKER_DISCOVERY_PORT`.

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
