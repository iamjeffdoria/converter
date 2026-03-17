# MKV → MP4 Converter (Django)

A fast, self-hosted MKV to MP4 converter. No ads, no limits, no uploads to strangers' servers.

## Requirements

- Python 3.11+
- FFmpeg installed on your system
- Django 4.2+

## Setup

```bash
# 1. Install FFmpeg (if not already installed)
# Ubuntu/Debian:
sudo apt install ffmpeg
# macOS:
brew install ffmpeg
# Windows: https://ffmpeg.org/download.html (add to PATH)

# 2. Install Python dependencies
pip install -r requirements.txt

# 3. Run the dev server
python manage.py runserver

# 4. Open http://127.0.0.1:8000
```

## How it works

1. **Upload** — File is uploaded via XHR with real-time progress tracking.
2. **Stream copy (Strategy 1)** — FFmpeg tries `-c copy` first. This remuxes the MKV into MP4 **without re-encoding**, so conversion is nearly instant (a few seconds even for huge files) with zero quality loss.
3. **Ultrafast fallback (Strategy 2)** — If stream copy fails (e.g. codec incompatibility), FFmpeg re-encodes with `-preset ultrafast -crf 18`. Still fast, visually lossless.
4. **Download** — Click download, file is served directly. Files are cleaned up after download or on "Convert another".

## Speed tips

- Stream copy is **instant** for most MKVs that already contain H.264/AAC — which is the vast majority.
- For production, use **Celery + Redis** instead of threading (included threading is fine for personal/small use).
- For big files, increase `DATA_UPLOAD_MAX_MEMORY_SIZE` in `settings.py` or use chunked upload.

## Project structure

```
mkv2mp4/
├── manage.py
├── requirements.txt
├── mkv2mp4/
│   ├── settings.py
│   ├── urls.py
│   └── wsgi.py
├── converter/
│   ├── views.py       ← all the logic
│   └── urls.py
└── templates/
    └── converter/
        └── index.html
```
