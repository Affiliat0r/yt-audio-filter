---
description: Produce and publish a request end to end (search or paste links, render, upload, playlist)
argument-hint: "niloya, Quran (An-Nas, Ghamdi), riko x2, <youtube-url>"
---

Run the `yt-studio` pipeline for this request: `$ARGUMENTS`

## How to run it

Always set the encoding — Turkish titles crash a cp1252 console *after* the
render but *before* the upload, which wastes the whole GPU run:

```bash
PYTHONIOENCODING=utf-8 python -m yt_audio_filter.workflow_cli "<request>"
```

There is no terminal attached, so it resolves the picks, saves a plan to
`state/last_plan.json`, prints the URLs and exits **10** — "awaiting approval".
That is success, not failure.

## The approval gate is not optional

The user asked for this explicitly. Never pass `--yes`, and never run
`--approve` in the same turn you resolved the plan.

Present each pick as a clickable YouTube URL with its title, duration, target
playlist, and whether that playlist already exists. Then stop and wait. Only
after the user says yes:

```bash
PYTHONIOENCODING=utf-8 python -m yt_audio_filter.workflow_cli --approve "<the same request>"
```

A render takes roughly 45 minutes per episode (Real-ESRGAN is on by default),
so run the approve step in the background and report the result when it lands.

## Things that go wrong

**Content ID.** Peppa Pig, Gumball, Clarence, TAYO, Bumba and Leo are blocked
worldwide on this channel — 19 of 83 uploads are. Music removal does not
prevent it, because Content ID fingerprints the picture. If the request names
one of these, say so *before* spending the GPU hours. Niloya, Riko, Hop Hop
Baykuş, Sevimli Dostlar, ABC songs and the Quran renders all pass.

**A pasted link has no playlist name.** The runner names it after the source's
channel, which is usually wrong. Label it: `Sevimli Dostlar: <url>`. Kurabiye
adam belongs under Sevimli Dostlar.

**A vague item is a search string.** "sevimli dostlar with a story like the
kurabiye adam one" gets searched verbatim and finds nothing useful. Translate
it to what the channel actually titles things (here: `sevimli dostlar masal`),
say which translation you chose, and let the approval gate catch a bad guess.

**Sources arrive at 360p.** The SABR wall leaves only format 18, so output is
reconstructed 720p, never real 1080p. Do not promise otherwise.

## Reporting

Report what was published with its URL and playlist. Verify rather than trust
the log: check the output file's resolution and that video and audio durations
agree. If sharpening was skipped, an `upscale-skipped` event says so — pass
that on rather than implying the render was sharpened.
