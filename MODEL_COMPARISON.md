# Model Comparison Guide

## Available Batch Files

### `run_filter.bat` (Default - htdemucs)
- **Model**: htdemucs (Hybrid Transformer Demucs)
- **Quality**: Best
- **Speed on short videos (<15 min)**: 40-50 seconds/s @ 110W
- **Speed on long videos (>1 hour)**: 5 seconds/s @ 60W
- **VRAM**: ~3-4 GB
- **Best for**: Maximum quality when processing time isn't critical

### `run_filter_mdx.bat` (Alternative - mdx_extra)
- **Model**: mdx_extra
- **Quality**: Good (comparable to htdemucs)
- **Speed**: More consistent across video lengths
- **VRAM**: ~2-3 GB
- **Best for**: Long videos where consistent speed matters

## Usage

### Quick Test (15-minute video)
```bash
# Test htdemucs (sequential)
.\run_filter.bat "https://www.youtube.com/watch?v=euADpFHHVng"

# Test mdx_extra
.\run_filter_mdx.bat "https://www.youtube.com/watch?v=euADpFHHVng"
```

### Long Video (1 hour)
```bash
# Standard chunked processing (sequential, 8-10x speedup)
.\run_filter.bat "https://www.youtube.com/watch?v=Lex0N_sQcAA"

# Parallel chunked processing (2x faster, requires 8GB+ VRAM)
.\run_filter_parallel.bat "https://www.youtube.com/watch?v=Lex0N_sQcAA"

# Test mdx_extra on long video
.\run_filter_mdx.bat "https://www.youtube.com/watch?v=Lex0N_sQcAA"
```

### Compare All Models
```bash
# This will test all three models on the same video
.\test_models.bat "https://www.youtube.com/watch?v=euADpFHHVng"
```

## Expected Results

| Model | Short Video Speed | Long Video Speed | Quality | VRAM | GPU Power |
|-------|-------------------|------------------|---------|------|-----------|
| htdemucs | 40-50 s/s | 5 s/s | ★★★★★ | 3-4 GB | 110W → 60W |
| mdx_extra | 30-40 s/s | 30-40 s/s | ★★★★☆ | 2-3 GB | 90-110W |
| mdx_extra_q | 50-60 s/s | 50-60 s/s | ★★★☆☆ | 1-2 GB | 80-100W |

## Parallel Processing (Experimental)

For videos longer than 30 minutes, **parallel chunked processing** can double your speed:

### Performance Comparison (1 hour video):

| Method | Speed | Total Time | GPU VRAM | GPU Power |
|--------|-------|------------|----------|-----------|
| **Old (no chunks)** | 5 s/s | ~12 min | 3-4 GB | 60W |
| **Chunked (sequential)** | 43 s/s | ~1.4 min | 3-4 GB | 110W |
| **Chunked (parallel x2)** | 80-90 s/s | **~40 sec** | 6-8 GB | 220W |

### How to Use:
```bash
# Use the parallel batch file
.\run_filter_parallel.bat "https://youtube.com/watch?v=VIDEO_ID"

# Or manually specify parallel chunks
yt-audio-filter VIDEO_URL --parallel-chunks 2 --upload --privacy public
```

### Requirements:
- **GPU VRAM**: 8GB+ (each chunk uses 3-4GB)
- **GPU Power**: 200W+ total (processes 2 chunks @ 110W each)
- **Cooling**: Ensure adequate GPU cooling

### When to Use:
- ✅ Long videos (>30 minutes) where speed matters
- ✅ High-end GPU with 8GB+ VRAM
- ❌ Low VRAM GPUs (4GB or less)
- ❌ Short videos (<15 minutes) - sequential is fast enough

## Recommendation

- **For long videos (>30 min) + 8GB GPU**: Use `run_filter_parallel.bat` (htdemucs, parallel)
- **For long videos (>30 min) + 4GB GPU**: Use `run_filter.bat` (htdemucs, sequential chunks)
- **For short videos (<15 min)**: Use `run_filter.bat` (htdemucs)
- **For fastest processing (quality tradeoff)**: Manually specify `--model mdx_extra_q`

## Manual Model Selection

You can also specify any model manually:

```bash
call .venv\Scripts\activate.bat
yt-audio-filter VIDEO_URL --model MODEL_NAME --upload --privacy public
```

Available models:
- `htdemucs` (default)
- `htdemucs_ft` (fine-tuned)
- `mdx_extra`
- `mdx_extra_q` (quantized)
- `mdx_q` (older, smaller)
