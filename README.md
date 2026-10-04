# Memorylane

A local photo and video library inspired by the provided Google Photos layout. No cloud account or upload. Originals are read-only.

## Start on Windows

Double-click **start.bat**, or run `powershell -ExecutionPolicy Bypass -File G:\projects\memorylane\start.ps1`.
Open http://127.0.0.1:4317. The server binds only to loopback.

For a fresh installation, install Python 3.11+ and run `setup.ps1` first. FFmpeg/ffprobe on PATH enables video thumbnails, duration, and capture dates. Dependencies, face models, CLIP weights, and map assets need internet only during setup.

## Use

1. Open **Folders & settings**. Paste folder paths, one per line or comma-separated, then **Save & scan folders**. Quote paths containing commas when using CSV. All subfolders are included. Overlapping folders are deduplicated by resolved path. Discovery shows a live file count before indexing shows determinate progress. macOS `._` metadata sidecars are skipped.
2. Browse the timeline with incremental, cursor-based infinite loading and month separators. The dotted date rail jumps straight to any indexed month without loading intervening photos. Scroll upward after a date jump to fetch newer months; the viewport stays anchored. Memories appear at the latest end, not above an arbitrary year jump. Open a photo or video; use arrow keys to navigate and Escape to close. The heart adds favorites.
3. **Memories** builds stories for every previous year with files within seven days of today's anniversary. Photos advance every five seconds; videos advance when ended. Pause and replay are available.
4. **People → Find faces in photos & videos** runs local YuNet detection and SFace embeddings. Face recognition, library scanning, GPS extraction, and search indexing have independent progress and pause controls. AI inference is resource-limited so background tasks do not saturate all CPU cores. Video recognition samples at most 24 frames per clip; brief appearances can be missed. Opening a video from a person starts at the detected timestamp.
   At startup, face recognition consolidates very close duplicate groups (centroid cosine >= 0.80), excludes groups seen together in a still photo, and preserves distinct names. Previous membership is saved locally in face_merge_history for recovery. Less certain pairs are left for visual review. Face matching uses several examples per group and rejects ambiguous matches. Portraits use square crops without stretching; existing covers are repaired when recognition starts. **Review likely duplicates** shows similar groups side by side; click **Same person — merge** only for a match you recognize. Merging works while recognition runs. Groups can still split across ages and angles or contain mistakes; there is no identity lookup or automatic guarantee, and the UI cannot yet split a combined group.
5. **Places** plots embedded photo GPS coordinates on an offline, zoomable world map. Click a cluster to browse its photos. Coordinates and images stay local. Country outlines are included; street tiles, address lookup, and video GPS extraction are not included.
6. Use **Refresh library** after adding, removing, or changing files. No continuous watcher runs. Unavailable root folders retain their index and report scan errors.
7. **Folders & settings → Cache & disk usage** shows the full data path, cache/temporary total, and separate thumbnail, preview, face-image, database, model, and cached-installer sizes. Click **Refresh usage** to measure again; original-media size is excluded. Browser-managed cache is separate. The reusable visual-search index is stored in data/search-vectors.npz and included in the cache total.

8. **Description search** uses a local CLIP model. Try “a beach at sunset” or “a dog on a sofa”. Build the descriptive index in Settings; search covers indexed items immediately and grows as processing continues. Videos are represented by their poster frame, not their audio or every frame. Switch to **Filename / date** for literal matching.

## Supported media and limitations

- JPEG, PNG, WebP, GIF (still preview), BMP, TIFF, HEIC/HEIF, and AVIF where supported by Pillow; previews are converted to JPEG. RAW formats are not supported.
- MP4, MOV, M4V, WebM, MKV, AVI are indexed. Actual playback requires a browser-supported codec/container. No transcoding is performed.
- Photo EXIF DateTimeOriginal is preferred, then EXIF modification date, then filesystem modification time. Video creation metadata is used when available. File-date fallbacks are labeled in the viewer.
- Face grouping uses photo pixels and sampled video frames locally and may miss faces or group them incorrectly. Names and embeddings stay in `data/`. Merge is persistent; automatic recognition is not an identity guarantee.
- Infinite scrolling fetches 60 items at a time. Previously loaded tiles remain mounted; very long browsing sessions can consume browser memory.
- The project starts with an honest empty library. No user folders are scanned until configured.

## Data and privacy

`data/library.sqlite` stores paths, dates, favorites, folder settings, names, GPS coordinates, face vectors, and visual-search vectors. `data/thumbs`, `data/previews`, and `data/faces` store derived images. Originals are never deleted or modified. Removing folders disconnects their index entries; cached derivatives may remain on disk. To erase the local index and all derived/face data, stop the app and delete the project's `data` folder; your originals stay untouched.

The app accepts only localhost:4317 or 127.0.0.1:4317 hosts. Mutating API requests require a per-process token, and no cross-origin API access is enabled. This is a personal desktop tool, not a multi-user or public server.

## Face model sources

- [OpenCV YuNet](https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet) (MIT)
- [OpenCV SFace](https://github.com/opencv/opencv_zoo/tree/main/models/face_recognition_sface) (Apache 2.0)
- [OpenCV implementation documentation](https://docs.opencv.org/4.x/d0/dd4/tutorial_dnn_face.html)

Models live in `models/`; run `download_models.py` for face models and `download_extras.py` for CLIP and offline map assets. CLIP is pinned to a specific model revision. See THIRD_PARTY.md for asset attribution.
The two face model downloads are verified against SHA-256 checksums. `requirements-lock.txt` records the tested Windows/Python 3.11 environment. Generated data, models, dependency environments, downloads, and logs are excluded from Git.

## Development checks

Run `.venv\Scripts\python.exe -m unittest test_app.py`. Tests use a temporary library and synthetic media, never your configured folders.
