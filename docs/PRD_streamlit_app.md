# Product Requirements Document: YT Audio Filter Web App

## Overview

A Streamlit-based web application that provides a user-friendly GUI for the YT Audio Filter pipeline, enabling users to remove background music from YouTube videos and automatically upload the processed versions.

---

## Problem Statement

The current CLI-based workflow requires technical knowledge to:
1. Run batch scripts from command line
2. Manage file paths and configurations
3. Monitor processing progress across multiple videos
4. Track upload status and results

**Target Users**: Content creators, educators, accessibility advocates, and parents who want musicless versions of videos but lack command-line experience.

---

## Goals & Success Metrics

### Goals
1. Democratize access to the audio filtering pipeline through a visual interface
2. Enable batch processing of entire YouTube channels with one click
3. Provide real-time progress monitoring and status tracking
4. Simplify YouTube API authentication setup

### Success Metrics
| Metric | Target |
|--------|--------|
| Time to first successful upload | < 5 minutes |
| User task completion rate | > 90% |
| Processing queue abandonment | < 10% |
| Daily active users | 100+ (post-launch) |

---

## Features & Requirements

### P0 - Must Have (MVP)

#### 1. Input Mode Selection (Tab Interface)

Two primary input modes presented as tabs:

**Tab A: Single Video URL**
- Text input field with YouTube URL validation
- Instant video preview (thumbnail + title + duration) on valid URL
- "Process & Upload" button

**Tab B: Channel Scraper**
- **Channel Input**: Text field for channel handle (e.g., `@niloyatv`, `@PeppaTV`)
  - Dynamic switching: User types new channel → hits Enter or "Fetch" → list updates
  - Recent channels dropdown for quick access
- **Scrape Options**:
  - Max videos slider (10-500, default: 50)
  - Include/exclude Shorts toggle
  - Sort: Recent / Popular / Oldest
- **Video Selection Grid** (the core UX):
  ```
  ┌─────────────────────────────────────────────────────────────┐
  │ Channel: @niloyatv                    [Change Channel ▼]   │
  │ Found: 127 videos                     [Fetch Videos]       │
  ├─────────────────────────────────────────────────────────────┤
  │ [☑ Select All] [☐ Deselect All]      Selected: 12 videos  │
  ├─────────────────────────────────────────────────────────────┤
  │                                                             │
  │  ☑ ┌──────────┐  Niloya Episode 125              12:34    │
  │    │ 🖼️ THUMB │  Views: 1.2M • 2 days ago                  │
  │    │ (frame)  │  https://youtube.com/watch?v=abc123        │
  │    └──────────┘                                             │
  │                                                             │
  │  ☑ ┌──────────┐  Niloya Episode 124              11:22    │
  │    │ 🖼️ THUMB │  Views: 980K • 1 week ago                  │
  │    │ (frame)  │  https://youtube.com/watch?v=def456        │
  │    └──────────┘                                             │
  │                                                             │
  │  ☐ ┌──────────┐  Niloya Episode 123              10:45    │
  │    │ 🖼️ THUMB │  Views: 1.5M • 2 weeks ago                 │
  │    │ (frame)  │  https://youtube.com/watch?v=ghi789        │
  │    └──────────┘                                             │
  │                                                             │
  │  [Load More...]                                             │
  └─────────────────────────────────────────────────────────────┘
  ```
- **Video Card Display** (each video shows):
  - Thumbnail image (fetched from YouTube)
  - Title
  - Duration (formatted: MM:SS)
  - View count
  - Upload date (relative: "2 days ago")
  - Direct URL (copyable)
  - Checkbox for selection (clickable anywhere on card)
- **Selection Behavior**:
  - Click anywhere on video card to toggle selection
  - Shift+Click for range selection
  - Visual highlight on selected items (border/background change)

#### 2. Processing Pipeline

- **Real-time Progress** for each video:
  ```
  ┌────────────────────────────────────────────────────────────┐
  │  Processing: "Niloya Episode 125"                          │
  ├────────────────────────────────────────────────────────────┤
  │  ✅ Download          [████████████████████] 100%          │
  │  ✅ Extract Audio     [████████████████████] 100%          │
  │  🔄 Isolate Vocals    [████████████░░░░░░░░]  62%          │
  │  ⏳ Remux Video       [░░░░░░░░░░░░░░░░░░░░]   0%          │
  │  ⏳ Upload            [░░░░░░░░░░░░░░░░░░░░]   0%          │
  └────────────────────────────────────────────────────────────┘
  ```
- **Output**:
  - Uploaded video link (clickable)
  - Download button for local copy
  - Processing logs (collapsible)

#### 3. Batch Processing Queue

- **Queue Table**:
  | # | Thumbnail | Title | Status | Progress | Actions |
  |---|-----------|-------|--------|----------|---------|
  | 1 | [img] | Video Title | Processing | 45% | Cancel |
  | 2 | [img] | Video Title | Queued | - | Remove |
  | 3 | [img] | Video Title | Completed | 100% | View |
- **Controls**:
  - Start/Pause queue
  - Clear completed
  - Cancel all
- **Persistence**: Queue survives page refresh (session state + file backup)

#### 4. Upload Configuration

- **Privacy Setting**: Radio buttons (Public / Unlisted / Private) - default: Public
- **Playlist Management**:
  - Dropdown showing existing playlists (fetched via YouTube API)
  - "Create New Playlist" option with inline name input
  - Auto-create playlist if specified name doesn't exist
  - CLI integration: Uses `create_playlist()` from uploader.py
- **Description Handling** (IMPORTANT):
  - **Preserves original video description** (user's explicit requirement)
  - Only appends small attribution footer:
    ```
    ---
    🔇 Background music removed version
    📺 Original: [URL] | 👤 [Channel]
    🔍 #NoBackgroundMusic #Musicless #VocalsOnly
    ```
  - Toggle option: "Add attribution footer" (default: on)
- **Title Format**: `[Original Title] [No Background Music]`
- **Tags**: Original tags + musicless keywords merged

#### 5. Authentication Setup

- **Guided Setup Wizard**:
  1. Step-by-step Google Cloud Console instructions (with screenshots)
  2. File upload for `client_secrets.json`
  3. OAuth flow trigger button ("Authenticate with YouTube")
  4. Connection status indicator (green checkmark / red X)
- **Status Display**: "Connected as: username@gmail.com"
- **Token Refresh**: Auto-refresh expired tokens, prompt if re-auth needed

---

### P1 - Should Have

#### 6. Processing History
- **Table**: All processed videos with:
  - Original URL
  - Uploaded URL
  - Processing date
  - Status (success/failed)
- **Filters**: Date range, status, channel
- **Export**: CSV download

#### 7. Settings Panel
- **Processing Options**:
  - Device selection (Auto/CPU/CUDA)
  - Audio bitrate (128k/192k/256k/320k)
  - Demucs model selection (htdemucs/htdemucs_ft)
- **Output Options**:
  - Default output directory
  - Keep local copies (toggle)
  - Auto-delete after upload (toggle)

#### 8. Error Handling & Recovery
- **Failed Job Retry**: One-click retry for failed videos
- **Error Details**: Expandable error messages with troubleshooting tips
- **Partial Progress**: Resume interrupted batch jobs

---

### P2 - Nice to Have

#### 9. Scheduling
- Schedule batch jobs for off-peak hours
- Daily/weekly recurring scrape + process jobs
- Email notifications on completion

#### 10. Analytics Dashboard
- Videos processed (total, this week, today)
- Total upload size
- Processing time trends
- Success/failure rate chart

#### 11. Multi-Channel Management
- Save multiple channel presets
- Quick-access channel favorites
- Channel-specific settings (playlist mapping)

#### 12. Advanced SEO Customization
- Custom title templates
- Description template editor
- Tag management (add/remove defaults)
- Thumbnail extraction/preview

---

## Technical Architecture

### Stack
```
┌─────────────────────────────────────────┐
│           Streamlit Frontend            │
│  (UI Components, Session State, Forms)  │
├─────────────────────────────────────────┤
│           Application Layer             │
│  (Queue Manager, Progress Tracker)      │
├─────────────────────────────────────────┤
│         YT Audio Filter Core            │
│  (scraper.py, pipeline.py, uploader.py) │
├─────────────────────────────────────────┤
│           External Services             │
│  (YouTube API, yt-dlp, Demucs, FFmpeg)  │
└─────────────────────────────────────────┘
```

### File Structure
```
yt-audio-filter/
├── src/
│   └── yt_audio_filter/
│       ├── app/                    # NEW: Streamlit app
│       │   ├── __init__.py
│       │   ├── main.py             # App entry point
│       │   ├── pages/
│       │   │   ├── 1_Process.py    # Single video processing
│       │   │   ├── 2_Channel.py    # Channel scraping
│       │   │   ├── 3_Queue.py      # Batch queue management
│       │   │   ├── 4_History.py    # Processing history
│       │   │   └── 5_Settings.py   # Configuration
│       │   ├── components/
│       │   │   ├── video_card.py   # Video display component
│       │   │   ├── progress.py     # Progress indicators
│       │   │   ├── auth_wizard.py  # OAuth setup wizard
│       │   │   └── seo_preview.py  # SEO metadata preview
│       │   └── state/
│       │       ├── queue.py        # Queue state management
│       │       └── config.py       # App configuration
│       ├── cli.py                  # Existing CLI
│       ├── pipeline.py             # Existing processing
│       ├── scraper.py              # Existing scraper
│       └── uploader.py             # Existing uploader
├── data/
│   ├── queue.json                  # Persistent queue state
│   └── history.db                  # SQLite processing history
└── run_app.bat                     # Windows launcher
```

### Key Dependencies (additions to existing)
```toml
[project.optional-dependencies]
app = [
    "streamlit>=1.28.0",
    "streamlit-option-menu>=0.3.6",
    "pandas>=2.0.0",
    "pillow>=10.0.0",
]
```

---

## UI Wireframes

### Main Processing Page
```
┌────────────────────────────────────────────────────────────┐
│  🔇 YT Audio Filter                    [Settings] [Help]  │
├────────────────────────────────────────────────────────────┤
│                                                            │
│  ┌──────────────────────────────────────────────────────┐ │
│  │  🔗 Enter YouTube URL                                │ │
│  │  ┌────────────────────────────────────────────────┐  │ │
│  │  │ https://youtube.com/watch?v=...                │  │ │
│  │  └────────────────────────────────────────────────┘  │ │
│  │                                                      │ │
│  │  [🚀 Process Video]                                  │ │
│  └──────────────────────────────────────────────────────┘ │
│                                                            │
│  ─────────────────── OR ───────────────────                │
│                                                            │
│  ┌──────────────────────────────────────────────────────┐ │
│  │  📺 Scrape Entire Channel                            │ │
│  │  ┌────────────────────────────────────────────────┐  │ │
│  │  │ @niloyatv                                      │  │ │
│  │  └────────────────────────────────────────────────┘  │ │
│  │                                                      │ │
│  │  Max Videos: [====●=====] 50                         │ │
│  │  ☑ Include Shorts                                    │ │
│  │                                                      │ │
│  │  [🔍 Fetch Videos]                                   │ │
│  └──────────────────────────────────────────────────────┘ │
│                                                            │
└────────────────────────────────────────────────────────────┘
```

### Processing Progress
```
┌────────────────────────────────────────────────────────────┐
│  Processing: "Niloya Episode 1"                            │
├────────────────────────────────────────────────────────────┤
│                                                            │
│  ✅ Download          [████████████████████] 100%          │
│  ✅ Extract Audio     [████████████████████] 100%          │
│  🔄 Isolate Vocals    [████████████░░░░░░░░]  62%          │
│  ⏳ Remux Video       [░░░░░░░░░░░░░░░░░░░░]   0%          │
│  ⏳ Upload            [░░░░░░░░░░░░░░░░░░░░]   0%          │
│                                                            │
│  ┌──────────────────────────────────────────────────────┐ │
│  │ 📋 Logs                                         [▼] │ │
│  │ 14:05:32 | Downloading video...                     │ │
│  │ 14:05:45 | Download complete (125.3 MB)             │ │
│  │ 14:05:46 | Extracting audio to WAV...               │ │
│  │ 14:05:52 | Running Demucs vocal isolation...        │ │
│  └──────────────────────────────────────────────────────┘ │
│                                                            │
│  [Cancel]                                                  │
│                                                            │
└────────────────────────────────────────────────────────────┘
```

### Channel Video Selection
```
┌────────────────────────────────────────────────────────────┐
│  📺 @niloyatv - 127 videos found                           │
├────────────────────────────────────────────────────────────┤
│  [☑ Select All]  [☐ Deselect All]  [Add Selected to Queue] │
├────────────────────────────────────────────────────────────┤
│                                                            │
│  ☑ ┌──────┐  Niloya - Episode 125               12:34     │
│    │ 🖼️  │  Views: 1.2M • 2 days ago                      │
│    └──────┘                                                │
│                                                            │
│  ☑ ┌──────┐  Niloya - Episode 124               11:22     │
│    │ 🖼️  │  Views: 980K • 1 week ago                      │
│    └──────┘                                                │
│                                                            │
│  ☐ ┌──────┐  Niloya - Episode 123               10:45     │
│    │ 🖼️  │  Views: 1.5M • 2 weeks ago                     │
│    └──────┘                                                │
│                                                            │
│  [Load More...]                                            │
│                                                            │
└────────────────────────────────────────────────────────────┘
```

---

## Implementation Phases

### Phase 1: MVP (2 weeks)
- [ ] Basic Streamlit app structure
- [ ] Single video processing with progress
- [ ] Simple channel scraper integration
- [ ] Basic queue (in-memory)
- [ ] Upload with existing credentials

### Phase 2: Enhanced UX (1 week)
- [ ] Persistent queue (survives refresh)
- [ ] Processing history with SQLite
- [ ] Settings panel
- [ ] Improved error handling

### Phase 3: Polish (1 week)
- [ ] OAuth setup wizard
- [ ] SEO preview component
- [ ] Multi-video selection UI
- [ ] Export/reporting features

### Phase 4: Advanced (Future)
- [ ] Scheduling system
- [ ] Analytics dashboard
- [ ] Multi-channel presets

---

## Risks & Mitigations

| Risk | Impact | Likelihood | Mitigation |
|------|--------|------------|------------|
| YouTube API quota limits | High | Medium | Implement rate limiting, show quota warnings |
| Long processing times | Medium | High | Background processing, progress persistence |
| GPU memory issues | High | Medium | Auto-fallback to CPU, memory monitoring |
| OAuth token expiry | Medium | Low | Auto-refresh, clear re-auth flow |
| Browser tab closure | Medium | High | Persistent queue, recovery on restart |

---

## Open Questions

1. **Hosting**: Local-only or option for cloud deployment (Streamlit Cloud)?
2. **Concurrent Processing**: How many parallel jobs? (GPU memory constraint)
3. **Storage**: Where to store processed videos before upload? Auto-cleanup policy?
4. **Multi-user**: Single user or support multiple YouTube accounts?

---

## Appendix

### A. Existing CLI Commands Reference
```bash
# Single video processing
yt-audio-filter "https://youtube.com/watch?v=..." --upload --privacy public

# Channel scraping
yt-channel-scrape @niloyatv -n 50 -o videos.txt

# Batch processing
run_batch.bat videos.txt
```

### B. Related Documentation
- [CLAUDE.md](../CLAUDE.md) - Project architecture overview
- [YouTube Data API Quota](https://developers.google.com/youtube/v3/getting-started#quota)
- [Streamlit Documentation](https://docs.streamlit.io/)

---

*Document Version: 1.0*
*Last Updated: December 2024*
*Author: Product Team*
