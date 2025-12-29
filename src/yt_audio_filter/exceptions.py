"""Custom exceptions for YT Audio Filter."""


class YTAudioFilterError(Exception):
    """Base exception for all YT Audio Filter errors."""

    def __init__(self, message: str, details: str = ""):
        super().__init__(message)
        self.message = message
        self.details = details

    def __str__(self) -> str:
        if self.details:
            return f"{self.message}\nDetails: {self.details}"
        return self.message


class ValidationError(YTAudioFilterError):
    """Input validation failures."""

    pass


class FFmpegError(YTAudioFilterError):
    """FFmpeg processing errors."""

    def __init__(self, message: str, returncode: int = -1, stderr: str = ""):
        super().__init__(message, stderr)
        self.returncode = returncode
        self.stderr = stderr


class DemucsError(YTAudioFilterError):
    """Demucs AI model errors."""

    pass


class PrerequisiteError(YTAudioFilterError):
    """Missing dependencies (FFmpeg, CUDA, etc.)."""

    pass


class YouTubeDownloadError(YTAudioFilterError):
    """YouTube download failures."""

    pass
