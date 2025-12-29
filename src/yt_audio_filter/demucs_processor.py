"""Demucs AI model integration for vocal isolation."""

from pathlib import Path
from typing import Callable, Optional

import torch
import torchaudio
import soundfile as sf
from tqdm import tqdm as original_tqdm

from .exceptions import DemucsError, PrerequisiteError
from .logger import get_logger

logger = get_logger()

# Global model cache to avoid reloading
_model_cache = {}

# Global progress callback for tqdm interception
_progress_callback = None


class ProgressCaptureTqdm(original_tqdm):
    """Custom tqdm that captures progress and reports to a callback."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._last_reported = -1

    def update(self, n=1):
        super().update(n)
        try:
            if _progress_callback and self.total and self.total > 0:
                # Calculate percentage
                pct = int(100 * self.n / self.total)
                # Only report on significant changes (every 1%)
                if pct != self._last_reported:
                    self._last_reported = pct
                    # Build progress info similar to tqdm display
                    # Use getattr for safer access to format_dict
                    fmt_dict = getattr(self, 'format_dict', {}) or {}
                    elapsed = fmt_dict.get('elapsed', 0) or 0
                    rate = fmt_dict.get('rate', 0) or 0
                    remaining = (self.total - self.n) / rate if rate and rate > 0 else 0

                    progress_info = {
                        'percent': pct,
                        'current': self.n,
                        'total': self.total,
                        'elapsed_seconds': elapsed,
                        'remaining_seconds': remaining,
                        'rate': rate,
                        'unit': getattr(self, 'unit', 's'),
                    }
                    _progress_callback(progress_info)
        except Exception:
            # Don't let progress reporting break the actual processing
            pass


def get_device(device: str = "auto") -> torch.device:
    """
    Get the appropriate torch device.

    Args:
        device: Device specification ("auto", "cpu", "cuda", "cuda:0", etc.)

    Returns:
        torch.device instance
    """
    if device == "auto":
        if torch.cuda.is_available():
            logger.debug("CUDA is available, using GPU")
            return torch.device("cuda")
        else:
            logger.debug("CUDA not available, using CPU")
            return torch.device("cpu")

    if device.startswith("cuda") and not torch.cuda.is_available():
        logger.warning("CUDA requested but not available, falling back to CPU")
        return torch.device("cpu")

    return torch.device(device)


def _load_model(model_name: str = "htdemucs", device: torch.device = None):
    """
    Load the Demucs model with caching.

    Args:
        model_name: Name of the Demucs model to load
        device: Device to load the model on

    Returns:
        Loaded Demucs model
    """
    if device is None:
        device = get_device("auto")

    cache_key = (model_name, str(device))

    if cache_key in _model_cache:
        logger.debug(f"Using cached model: {model_name}")
        return _model_cache[cache_key]

    logger.debug(f"Loading Demucs model: {model_name}")

    try:
        from demucs.pretrained import get_model

        model = get_model(model_name)
        model.to(device)
        model.eval()

        _model_cache[cache_key] = model
        logger.debug(f"Model loaded successfully (sample rate: {model.samplerate})")
        return model

    except ImportError:
        raise PrerequisiteError(
            "Demucs not installed",
            "Please install demucs: pip install demucs"
        )
    except Exception as e:
        raise DemucsError(f"Failed to load Demucs model '{model_name}'", str(e))


def isolate_vocals(
    audio_path: Path,
    output_path: Path,
    device: str = "auto",
    model_name: str = "htdemucs",
    progress_callback: Optional[Callable[[dict], None]] = None
) -> Path:
    """
    Isolate vocals from an audio file using Demucs.

    Args:
        audio_path: Path to input audio file (WAV)
        output_path: Path for output vocals file (WAV)
        device: Device to use ("auto", "cpu", "cuda")
        model_name: Demucs model variant
        progress_callback: Optional callback receiving progress dict with:
            - percent: int (0-100)
            - current: float (seconds processed)
            - total: float (total seconds)
            - elapsed_seconds: float
            - remaining_seconds: float
            - rate: float (seconds per second)

    Returns:
        Path to the isolated vocals file

    Raises:
        DemucsError: If vocal isolation fails
    """
    global _progress_callback

    try:
        from demucs.apply import apply_model
        import demucs.apply as demucs_apply
    except ImportError:
        raise PrerequisiteError(
            "Demucs not installed",
            "Please install demucs: pip install demucs"
        )

    if not audio_path.exists():
        raise DemucsError(f"Audio file not found: {audio_path}")

    logger.debug(f"Isolating vocals from {audio_path}")

    # Get device and load model
    torch_device = get_device(device)
    model = _load_model(model_name, torch_device)

    try:
        # Load audio using soundfile (more compatible than torchaudio default backend)
        logger.debug("Loading audio file...")
        audio_data, sample_rate = sf.read(str(audio_path), dtype='float32')

        # Convert to tensor: soundfile returns (samples, channels), we need (channels, samples)
        if len(audio_data.shape) == 1:
            # Mono audio
            waveform = torch.from_numpy(audio_data).unsqueeze(0)
        else:
            # Stereo/multi-channel audio - transpose to (channels, samples)
            waveform = torch.from_numpy(audio_data.T.copy())

        # Resample if necessary
        if sample_rate != model.samplerate:
            logger.debug(f"Resampling from {sample_rate} to {model.samplerate}")
            resampler = torchaudio.transforms.Resample(sample_rate, model.samplerate)
            waveform = resampler(waveform)

        # Ensure stereo
        if waveform.shape[0] == 1:
            waveform = waveform.repeat(2, 1)
        elif waveform.shape[0] > 2:
            waveform = waveform[:2, :]

        # Add batch dimension: (channels, samples) -> (batch, channels, samples)
        waveform = waveform.unsqueeze(0).to(torch_device)

        # Apply model with progress capture
        logger.debug("Running vocal separation (this may take a while)...")

        # Set up progress callback
        global _progress_callback
        _progress_callback = progress_callback

        try:
            if progress_callback:
                # demucs.apply does: `import tqdm` then uses `tqdm.tqdm(...)`
                # So demucs_apply.tqdm is the tqdm MODULE, and we need to patch
                # the 'tqdm' attribute ON that module (which is the tqdm class)
                from unittest.mock import patch

                # demucs_apply.tqdm is the tqdm module
                # We need to patch demucs_apply.tqdm.tqdm (the class)
                tqdm_module = demucs_apply.tqdm
                with patch.object(tqdm_module, 'tqdm', ProgressCaptureTqdm):
                    with torch.no_grad():
                        sources = apply_model(model, waveform, device=torch_device, progress=True)
            else:
                with torch.no_grad():
                    sources = apply_model(model, waveform, device=torch_device, progress=True)
        finally:
            _progress_callback = None

        # sources shape: (batch, num_sources, channels, samples)
        # htdemucs sources: drums, bass, other, vocals (index 3)
        sources = sources.squeeze(0)  # Remove batch dimension

        # Find vocals index
        source_names = model.sources
        logger.debug(f"Available sources: {source_names}")

        if "vocals" in source_names:
            vocals_idx = source_names.index("vocals")
        else:
            # Fallback: assume vocals is last
            vocals_idx = len(source_names) - 1
            logger.warning(f"'vocals' not found, using source index {vocals_idx}")

        vocals = sources[vocals_idx]  # Shape: (channels, samples)
        logger.debug(f"Vocals tensor shape: {vocals.shape}")

        # Move to CPU for saving
        vocals = vocals.cpu().numpy()

        # Save vocals to file using soundfile
        # Transpose from (channels, samples) to (samples, channels) for soundfile
        sf.write(
            str(output_path),
            vocals.T,
            model.samplerate,
            subtype='PCM_16'
        )

        logger.debug(f"Vocals saved to {output_path}")

        # Clear CUDA cache if using GPU
        if torch_device.type == "cuda":
            torch.cuda.empty_cache()

        return output_path

    except Exception as e:
        if isinstance(e, (DemucsError, PrerequisiteError)):
            raise
        raise DemucsError(f"Vocal isolation failed: {e}")


def check_demucs_available() -> bool:
    """
    Check if Demucs is available and importable.

    Returns:
        True if Demucs is available, False otherwise
    """
    try:
        from demucs.pretrained import get_model
        from demucs.apply import apply_model
        return True
    except ImportError:
        return False


def ensure_demucs_available() -> None:
    """
    Ensure Demucs is available, raising an error if not.

    Raises:
        PrerequisiteError: If Demucs is not installed
    """
    if not check_demucs_available():
        raise PrerequisiteError(
            "Demucs not installed",
            "Please install demucs: pip install demucs\n"
            "Note: This also requires PyTorch. Visit https://pytorch.org for installation."
        )
