"""Local render worker for the Quran Studio web frontend.

Vercel cannot run FFmpeg, Demucs, or a GPU, so the browser only ever queues
*jobs*. This package is the other half: a long-running process on the render
box that polls the Vercel API, executes the job against the **existing**
``yt_audio_filter`` pipeline code, and reports progress / results back.

Nothing under ``src/yt_audio_filter/`` is modified by this package — the
worker is purely a new consumer of the public pipeline functions.
"""

WORKER_VERSION = "1.0.0"

__all__ = ["WORKER_VERSION"]
