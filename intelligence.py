"""Local CLIP inference, EXIF GPS parsing, and bounded video sampling."""
import io
import math
from pathlib import Path
import shutil
import subprocess
import threading
import numpy as np
from PIL import Image, ImageOps

# Keep two AI workloads from saturating all cores at the same instant.
AI_BUDGET = threading.Semaphore(1)

def exif_coordinates(gps):
    try:
        def coordinate(value, ref, positive, negative, limit):
            ref = ref.decode() if isinstance(ref, bytes) else str(ref)
            if ref not in (positive, negative) or len(value) != 3:
                return None
            degrees, minutes, seconds = map(float, value)
            if not (0 <= minutes < 60 and 0 <= seconds < 60):
                return None
            result = degrees + minutes / 60 + seconds / 3600
            if ref == negative:
                result *= -1
            return result if math.isfinite(result) and abs(result) <= limit else None
        lat = coordinate(gps[2], gps[1], 'N', 'S', 90)
        lon = coordinate(gps[4], gps[3], 'E', 'W', 180)
        return (lat, lon) if lat is not None and lon is not None else (None, None)
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return None, None

def photo_coordinates(path):
    with Image.open(path) as image:
        return exif_coordinates(image.getexif().get_ifd(34853))

def sample_times(duration, limit=24, interval=10):
    duration = max(0, float(duration or 0))
    if not math.isfinite(duration) or duration <= 0.1:
        return [0.0]
    count = min(limit, max(1, math.ceil(duration / interval)))
    if count == 1:
        return [min(duration / 2, 1.0)]
    return np.linspace(min(1.0, duration / 10), max(0, duration - 0.25), count).tolist()

def video_frames(path, duration, limit=24, times=None):
    executable = shutil.which('ffmpeg')
    if not executable:
        raise RuntimeError('FFmpeg is required for video face recognition.')
    for seconds in (sample_times(duration, limit) if times is None else times):
        result = subprocess.run([executable, '-v', 'error', '-threads', '1', '-ss', str(seconds),
            '-i', str(path), '-frames:v', '1', '-vf', 'scale=960:960:force_original_aspect_ratio=decrease',
            '-f', 'image2pipe', '-vcodec', 'mjpeg', '-threads', '1', 'pipe:1'], capture_output=True,
            timeout=30, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if result.returncode or not result.stdout:
            raise RuntimeError(f'Cannot decode video frame at {seconds:.1f}s')
        with Image.open(io.BytesIO(result.stdout)) as image:
            yield seconds, image.convert('RGB')

class LocalCLIP:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.lock = threading.Lock()
        self.text = self.vision = self.tokenizer = None

    def ready(self):
        return all((self.directory / name).exists() for name in
                   ('vision_model_quantized.onnx', 'text_model_quantized.onnx', 'tokenizer.json'))

    def load(self):
        with self.lock:
            if self.vision is not None:
                return
            if not self.ready():
                raise RuntimeError('Local search models are missing. Run setup.ps1 to install them.')
            import onnxruntime as ort
            from tokenizers import Tokenizer
            options = ort.SessionOptions()
            options.intra_op_num_threads = 2
            options.inter_op_num_threads = 1
            self.text = ort.InferenceSession(str(self.directory / 'text_model_quantized.onnx'), options, providers=['CPUExecutionProvider'])
            self.vision = ort.InferenceSession(str(self.directory / 'vision_model_quantized.onnx'), options, providers=['CPUExecutionProvider'])
            self.tokenizer = Tokenizer.from_file(str(self.directory / 'tokenizer.json'))
            self.tokenizer.enable_truncation(max_length=77)
            self.tokenizer.enable_padding(length=77, pad_id=49407, pad_token='<|endoftext|>')

    @staticmethod
    def normalized(value):
        value = np.asarray(value, dtype=np.float32).reshape(-1)
        return value / max(float(np.linalg.norm(value)), 1e-8)

    def image_vector(self, image):
        self.load()
        image = ImageOps.fit(image.convert('RGB'), (224, 224), method=Image.Resampling.BICUBIC)
        pixels = np.asarray(image, dtype=np.float32) / 255.0
        pixels = (pixels - [0.48145466, 0.4578275, 0.40821073]) / [0.26862954, 0.26130258, 0.27577711]
        pixels = pixels.astype(np.float32).transpose(2, 0, 1)[None]
        with AI_BUDGET:
            return self.normalized(self.vision.run(['image_embeds'], {'pixel_values': pixels})[0])

    def text_vector(self, text):
        self.load()
        with self.lock:
            encoded = self.tokenizer.encode(text)
        feeds = {'input_ids': np.asarray([encoded.ids], dtype=np.int64),
                 'attention_mask': np.asarray([encoded.attention_mask], dtype=np.int64)}
        feeds = {i.name: feeds[i.name] for i in self.text.get_inputs()}
        return self.normalized(self.text.run(['text_embeds'], feeds)[0])
