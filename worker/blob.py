"""Direct REST upload to Vercel Blob.

The official ``@vercel/blob`` SDK is Node-only, so the worker speaks the same
HTTP API by hand::

    PUT https://blob.vercel-storage.com/{url-encoded pathname}
    authorization: Bearer {BLOB_READ_WRITE_TOKEN}
    x-api-version: 7
    x-content-type: video/mp4
    x-add-random-suffix: 1
    <raw bytes>

The file object is handed to ``requests`` unread so a 500 MB render streams
off disk instead of landing in RAM. On any non-2xx the full response body is
logged — if Vercel bumps the API version, that body is the only clue.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote, unquote, urlparse

import requests

from yt_audio_filter.logger import get_logger

logger = get_logger()

BLOB_API_BASE = "https://blob.vercel-storage.com"
#: Verified against the live API on 2026-07-31. Version 11 is rejected with
#: `{"code":"bad_request","message":"Invalid pathname"}`; 7 is the newest that
#: accepts the plain `PUT /{pathname}` form AND returns `downloadUrl` (6 and 4
#: work but omit it). If uploads start 400ing, re-probe with curl before
#: assuming the pathname is at fault — the message is misleading.
BLOB_API_VERSION = "7"

#: The store is created with `--access private`, so uploads must declare it and
#: reads require a presigned URL (the Studio mints those in
#: `web/app/api/jobs/[id]/preview`). An anonymous GET on a blob URL is 403.
BLOB_ACCESS = "private"

#: (connect, read). Uploading a multi-hundred-MB render over a home
#: connection is slow; the read timeout only bounds server silence.
DEFAULT_TIMEOUT = (10, 900)


class BlobUploadError(RuntimeError):
    """The Blob API rejected the upload or was unreachable."""

    def __init__(self, message: str, status_code: int = 0, body: str = ""):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.body = body


@dataclass(frozen=True)
class BlobUpload:
    """A successful upload: the public URL plus what we sent."""

    url: str
    download_url: str
    pathname: str
    size_bytes: int


def upload_file(
    file_path: Path,
    token: str,
    *,
    pathname: Optional[str] = None,
    content_type: str = "video/mp4",
    timeout: Any = DEFAULT_TIMEOUT,
    session: Optional[requests.Session] = None,
) -> BlobUpload:
    """Stream ``file_path`` to Vercel Blob and return its public URL.

    Args:
        file_path: Local file to upload.
        token: ``BLOB_READ_WRITE_TOKEN`` from the Vercel project.
        pathname: Blob path; defaults to the file name. A random suffix is
            always appended server-side, so repeated renders never collide.
        content_type: MIME type stored with the blob.
        timeout: ``requests`` timeout tuple.
        session: Optional session to reuse.

    Raises:
        BlobUploadError: On a missing file, transport failure, or non-2xx.
    """
    file_path = Path(file_path)
    if not file_path.exists():
        raise BlobUploadError(f"File not found: {file_path}")
    if not token:
        raise BlobUploadError("BLOB_READ_WRITE_TOKEN is empty")

    name = pathname or file_path.name
    size = file_path.stat().st_size
    url = f"{BLOB_API_BASE}/{quote(name, safe='/')}"
    headers = {
        "authorization": f"Bearer {token}",
        "x-api-version": BLOB_API_VERSION,
        # The render store is private. Without this the API rejects the upload
        # with "Cannot use public access on a private store."
        "x-vercel-blob-access": BLOB_ACCESS,
        "x-content-type": content_type,
        "x-add-random-suffix": "1",
    }

    logger.info("Uploading %s (%.1f MB) to Vercel Blob...", name, size / 1_048_576)
    poster = session.put if session is not None else requests.put
    try:
        with open(file_path, "rb") as handle:
            response = poster(url, data=handle, headers=headers, timeout=timeout)
    except requests.RequestException as exc:
        raise BlobUploadError(f"Blob upload transport error: {exc}") from exc

    status = getattr(response, "status_code", 0)
    if not 200 <= status < 300:
        body = ""
        try:
            body = response.text
        except Exception:  # noqa: BLE001 - diagnostics only
            pass
        logger.error("Blob upload failed (HTTP %s). Response body: %s", status, body)
        raise BlobUploadError(
            f"Blob upload failed with HTTP {status}", status, body[:2000]
        )

    try:
        data = response.json()
    except ValueError as exc:
        raise BlobUploadError("Blob upload returned a non-JSON body", status) from exc

    blob_url = str(data.get("url") or "")
    if not blob_url:
        raise BlobUploadError(f"Blob response had no 'url' field: {data!r}", status)

    return BlobUpload(
        url=blob_url,
        download_url=str(data.get("downloadUrl") or blob_url),
        pathname=_pathname_from_url(blob_url) or str(data.get("pathname") or name),
        size_bytes=size,
    )


def _pathname_from_url(blob_url: str) -> str:
    """Extract the blob's real pathname from its URL.

    Do NOT use the API's ``pathname`` field: with ``x-add-random-suffix: 1``
    it echoes back the *requested* name (``render.mp4``) while the object is
    actually stored at ``render-<random>.mp4``. Presigning the echoed value
    yields a URL that 404s. The URL path is the only authoritative source.
    """
    path = urlparse(blob_url).path.lstrip("/")
    return unquote(path)


def selftest(token: str, *, session: Optional[requests.Session] = None) -> str:
    """Upload a tiny text file and return its URL.

    Backs ``python -m worker.worker --selftest-blob``: one command to prove
    the token works before a 40-minute render finds out it doesn't.
    """
    with tempfile.TemporaryDirectory() as tmp:
        probe = Path(tmp) / "worker-blob-selftest.txt"
        probe.write_text("quran-studio worker blob self-test\n", encoding="utf-8")
        upload = upload_file(
            probe,
            token,
            pathname="worker-blob-selftest.txt",
            content_type="text/plain",
            session=session,
        )
    return upload.url
