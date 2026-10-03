# Memorylane

A local photo and video library inspired by the provided Google Photos layout. No cloud account or upload. Originals are read-only.

## Start on Windows

Double-click **start.bat**, or run `powershell -ExecutionPolicy Bypass -File G:\projects\memorylane\start.ps1`.
Open http://127.0.0.1:4317. The server binds only to loopback.

For a fresh installation, install Python 3.11+ and run `setup.ps1` first. FFmpeg/ffprobe on PATH enables video thumbnails, duration, and capture dates. Dependencies and the two face models need internet only during setup.

## Use

1. Open **Folders & settings**. Paste folder paths, one per line or comma-separated, then **Save & scan folders**. Quote paths containing commas when using CSV. All subfolders are included. Overlapping folders are deduplicated by resolved path. Discovery shows a live file count before indexing shows determinate progress. macOS `._` metadata sidecars are skipped.
2. Browse the timeline with incremental, cursor-based infinite loading and month separators. The dotted date rail jumps straight to any indexed month without loading intervening photos. Open a photo or video; use arrow keys to navigate and Escape to close. The heart adds favorites.
3. **Memories** builds stories for every previous year with files within seven days of today's anniversary. Photos advance every five seconds; videos advance when ended. Pause and replay are available.
4. **People → Find & group faces** runs local YuNet detection and SFace embeddings. While scanning, **Group faces after scan** queues recognition next. Name groups or merge duplicate groups through **Edit person**. Photo faces only; no cloud recognition or identity lookup. Similarity is conservative (cosine >= 0.45); groups need human review, and incorrect combined groups currently cannot be split through the UI. Processing uses two OpenCV CPU threads. Completed files are saved individually, so a later run skips finished photos.
5. **Places** is deliberately a placeholder. No GPS extraction, map provider, or geocoding is implemented.
6. Use **Refresh library** after adding, removing, or changing files. No continuous watcher runs. Unavailable root folders retain their index and report scan errors.
7. **Folders & settings → Cache & disk usage** shows the full data path, cache/temporary total, and separate thumbnail, preview, face-image, database, model, and cached-installer sizes. Click **Refresh usage** to measure again; original-media size is excluded. Browser-managed cache is separate.

## Supported media and limitations

- JPEG, PNG, WebP, GIF (still preview), BMP, TIFF, HEIC/HEIF, and AVIF where supported by Pillow; previews are converted to JPEG. RAW formats are not supported.
- MP4, MOV, M4V, WebM, MKV, AVI are indexed. Actual playback requires a browser-supported codec/container. No transcoding is performed.
- Photo EXIF DateTimeOriginal is preferred, then EXIF modification date, then filesystem modification time. Video creation metadata is used when available. File-date fallbacks are labeled in the viewer.
- Face grouping uses photo pixels locally and may miss faces or group them incorrectly. Names and embeddings stay in `data/`. Merge is persistent; automatic recognition is not an identity guarantee.
- Infinite scrolling fetches 60 items at a time. Previously loaded tiles remain mounted; very long browsing sessions can consume browser memory.
- The project starts with an honest empty library. No user folders are scanned until configured.

## Data and privacy

`data/library.sqlite` stores paths, dates, favorites, folder settings, names, and face vectors. `data/thumbs`, `data/previews`, and `data/faces` store derived images. Originals are never deleted or modified. Removing folders disconnects their index entries; cached derivatives may remain on disk. To erase the local index and all derived/face data, stop the app and delete the project's `data` folder; your originals stay untouched.

The app accepts only localhost:4317 or 127.0.0.1:4317 hosts. Mutating API requests require a per-process token, and no cross-origin API access is enabled. This is a personal desktop tool, not a multi-user or public server.

## Face model sources

- [OpenCV YuNet](https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet) (MIT)
- [OpenCV SFace](https://github.com/opencv/opencv_zoo/tree/main/models/face_recognition_sface) (Apache 2.0)
- [OpenCV implementation documentation](https://docs.opencv.org/4.x/d0/dd4/tutorial_dnn_face.html)

Models live in `models/`; run `download_models.py` to fetch missing models.
Downloaded models are verified against SHA-256 checksums. `requirements-lock.txt` records the tested Windows/Python 3.11 environment. Generated data, models, dependency environments, downloads, and logs are excluded from Git.

## Development checks

Run `.venv\Scripts\python.exe -m unittest test_app.py`. Tests use a temporary library and synthetic media, never your configured folders.
