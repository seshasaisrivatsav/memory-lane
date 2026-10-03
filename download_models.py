"""Download public OpenCV Zoo models once; runtime never needs the internet."""
from pathlib import Path
import urllib.request
import hashlib

root = Path(__file__).resolve().parent / 'models'
root.mkdir(exist_ok=True)
models = {
    'yunet.onnx': 'face_detection_yunet/face_detection_yunet_2023mar.onnx',
    'sface.onnx': 'face_recognition_sface/face_recognition_sface_2021dec.onnx',
}
hashes = {
    'yunet.onnx': '8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4',
    'sface.onnx': '0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79',
}
for name, source in models.items():
    path = root / name
    if path.exists() and hashlib.sha256(path.read_bytes()).hexdigest() == hashes[name]:
        continue
    print(f'Downloading {name}…', flush=True)
    temp = path.with_suffix('.download')
    urllib.request.urlretrieve('https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/' + source, temp)
    if hashlib.sha256(temp.read_bytes()).hexdigest() != hashes[name]:
        raise RuntimeError(f'Invalid model download: {name}')
    temp.replace(path)
print('Local face models are ready.')
