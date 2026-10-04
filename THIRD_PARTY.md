# Third-party assets

- Leaflet 1.9.4: https://leafletjs.com, BSD 2-Clause. Full notice: static/vendor/LEAFLET-LICENSE.txt.
- Offline country geometry: Natural Earth 1:110m admin-0 countries, https://github.com/nvkelso/natural-earth-vector. Public domain: https://www.naturalearthdata.com/about/terms-of-use/.
- Local description search: OpenAI CLIP ViT-B/32, converted to ONNX by Xenova: https://huggingface.co/Xenova/clip-vit-base-patch32, revision d15189d7028b43f1d3e65039190477f6af591c2a. Weights are downloaded during setup, not distributed in this repository. Original implementation and license: https://github.com/openai/CLIP.
- Face detection and recognition: OpenCV YuNet and SFace. Source/license links are in README.md and download_models.py. Weights are downloaded during setup and not committed.
- Python packages: see requirements-lock.txt for versions; each package retains its upstream license.
