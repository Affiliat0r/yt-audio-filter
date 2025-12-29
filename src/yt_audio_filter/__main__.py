"""Entry point for running as a module: python -m yt_audio_filter"""

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
