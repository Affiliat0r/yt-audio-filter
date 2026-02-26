#!/bin/bash
# Install Python dependencies for ARM64 (aarch64)
# Usage: ./install-dependencies.sh
set -euo pipefail

echo "=== Installing Python Dependencies ==="
echo ""

WORKSPACE="${HOME}/yt-filter-workspace"
cd "$WORKSPACE"

# Detect Python version
PYTHON=""
for py in python3.12 python3.11 python3.10 python3; do
    if command -v "$py" &>/dev/null; then
        PYTHON="$py"
        break
    fi
done

if [ -z "$PYTHON" ]; then
    echo "Error: Python 3.10+ not found"
    exit 1
fi

echo "Using Python: $PYTHON ($($PYTHON --version))"

# Create virtual environment
echo ""
echo "--- Creating virtual environment ---"
$PYTHON -m venv venv
source venv/bin/activate

# Upgrade pip
pip install --upgrade pip setuptools wheel

# Install PyTorch for CPU (ARM64 compatible)
echo ""
echo "--- Installing PyTorch (CPU-only for ARM64) ---"
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cpu

# Install the project with all optional dependencies
echo ""
echo "--- Installing yt-audio-filter ---"
pip install -e ".[upload,discovery]"

# Verify installations
echo ""
echo "--- Verifying installations ---"
echo -n "PyTorch: "
python -c "import torch; print(f'{torch.__version__} (device: cpu)')"

echo -n "Demucs: "
python -c "import demucs; print('OK')"

echo -n "PyYAML: "
python -c "import yaml; print('OK')"

echo -n "google-api-python-client: "
python -c "import googleapiclient; print('OK')" 2>/dev/null || echo "NOT INSTALLED (install upload extras)"

echo -n "FFmpeg: "
ffmpeg -version 2>/dev/null | head -1 || echo "NOT FOUND"

echo -n "yt-dlp: "
python -c "import yt_dlp; print(yt_dlp.version.__version__)"

echo ""
echo "=== Dependencies Installed Successfully ==="
echo ""
echo "Architecture: $(uname -m)"
echo "Note: ARM64 uses CPU-only processing (no CUDA)."
echo "Expected performance: ~2-4x realtime for htdemucs model."
