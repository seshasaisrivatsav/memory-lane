"""Download model weights and offline map assets, never user media."""
import hashlib
import json
from pathlib import Path
import urllib.request

BASE = Path(__file__).resolve().parent
REVISION = 'd15189d7028b43f1d3e65039190477f6af591c2a'

def download(url, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return
    print(f'Downloading {path.name}…', flush=True)
    temp = path.with_suffix(path.suffix + '.download')
    urllib.request.urlretrieve(url, temp)
    temp.replace(path)

if __name__ == '__main__':
    for name in ['vision_model_quantized.onnx', 'text_model_quantized.onnx', 'tokenizer.json']:
        remote = ('onnx/' if name.endswith('.onnx') else '') + name
        download(f'https://huggingface.co/Xenova/clip-vit-base-patch32/resolve/{REVISION}/{remote}', BASE / 'models/clip' / name)
    for name in ['leaflet.js', 'leaflet.css']:
        download('https://unpkg.com/leaflet@1.9.4/dist/' + name, BASE/'static/vendor'/name)
    download('https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_110m_admin_0_countries.geojson', BASE/'static/vendor/world.geojson')
    print('Offline search and map assets are ready.')
