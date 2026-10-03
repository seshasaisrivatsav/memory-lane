"""Memorylane: a loopback-only, read-only-originals personal photo library."""
from __future__ import annotations
import datetime as dt
import csv
import hashlib
import json
import mimetypes
import os
from pathlib import Path
import secrets
import shutil
import sqlite3
import subprocess
import threading
import time
from contextlib import contextmanager
from flask import Flask, request, jsonify, send_file, abort
from PIL import Image, ImageOps
from pillow_heif import register_heif_opener

register_heif_opener()
BASE = Path(__file__).resolve().parent
DATA = Path(os.environ.get('MEMORYLANE_DATA', str(BASE / 'data')))
DATA.mkdir(parents=True, exist_ok=True)
for folder in ('thumbs', 'faces', 'previews'):
    (DATA / folder).mkdir(exist_ok=True)
app = Flask(__name__, static_folder=str(BASE / 'static'))
app.config['MAX_CONTENT_LENGTH'] = 1024 * 1024
TOKEN = secrets.token_urlsafe(32)
LOCK = threading.Lock()
JOB = dict(running=False, kind='', phase='', discovered=0, processed=0, total=0, errors=[], error_count=0, queued_faces=False, message='Ready')
PHOTOS = {'.jpg', '.jpeg', '.png', '.webp', '.gif', '.bmp', '.tif', '.tiff', '.heic', '.heif', '.avif'}
VIDEOS = {'.mp4', '.mov', '.m4v', '.webm', '.mkv', '.avi'}

@contextmanager
def db():
    con = sqlite3.connect(DATA / 'library.sqlite', timeout=30)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA foreign_keys=ON')
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()

with db() as c:
    c.executescript('''
    PRAGMA journal_mode=WAL;
    CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS media (id TEXT PRIMARY KEY, path TEXT UNIQUE NOT NULL,
      name TEXT, kind TEXT, captured TEXT, date_source TEXT, width INTEGER, height INTEGER,
      duration REAL DEFAULT 0, mtime REAL, size INTEGER, favorite INTEGER DEFAULT 0,
      face_done INTEGER DEFAULT 0);
    CREATE INDEX IF NOT EXISTS media_date ON media(captured DESC, id DESC);
    CREATE TABLE IF NOT EXISTS people (id INTEGER PRIMARY KEY, name TEXT DEFAULT '', feature BLOB);
    CREATE TABLE IF NOT EXISTS faces (id INTEGER PRIMARY KEY, media_id TEXT REFERENCES media(id) ON DELETE CASCADE,
      person_id INTEGER REFERENCES people(id), crop TEXT, feature BLOB);
    CREATE INDEX IF NOT EXISTS face_person ON faces(person_id,media_id);
    ''')

def folders():
    with db() as c:
        row = c.execute("SELECT value FROM settings WHERE key='folders'").fetchone()
    return json.loads(row[0]) if row else []

def allowed(path):
    p = Path(path).resolve()
    return any(p.is_relative_to(Path(root).resolve()) for root in folders())

def job_error(message):
    JOB['error_count'] += 1
    if len(JOB['errors']) < 100:
        JOB['errors'].append(message)

def ffprobe(path):
    exe = shutil.which('ffprobe')
    if not exe:
        return {}
    try:
        r = subprocess.run([exe, '-v', 'quiet', '-show_format', '-show_streams', '-of', 'json', str(path)],
                           capture_output=True, timeout=30, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        return json.loads(r.stdout)
    except Exception:
        return {}

def photo_data(path):
    with Image.open(path) as original:
        exif = original.getexif()
        try:
            sub = exif.get_ifd(34665)
        except Exception:
            sub = {}
        raw = sub.get(36867) or exif.get(36867) or exif.get(306)
        captured = None
        if raw:
            try:
                captured = dt.datetime.strptime(str(raw)[:19], '%Y:%m:%d %H:%M:%S').isoformat()
            except ValueError:
                pass
        im = ImageOps.exif_transpose(original).convert('RGB')
        return im, captured

def scan_worker():
    roots = folders()
    resolved_roots = [Path(root).resolve() for root in roots]
    resolved_data = DATA.resolve()
    JOB.update(phase='discovering', message='Discovering media in your folders…')
    files = {}
    inaccessible = []
    visited = set()
    for root in roots:
        p = Path(root)
        if not p.is_dir():
            inaccessible.append(root)
            job_error(f'Folder unavailable: {root}')
            continue
        pending = [p.resolve()]
        while pending:
            parent = pending.pop()
            if parent in visited or parent.is_relative_to(resolved_data):
                continue
            visited.add(parent)
            JOB['message'] = f'Discovering: {parent.name}'
            try:
                with os.scandir(parent) as entries:
                    for entry in entries:
                        if entry.name.startswith('._') or entry.is_symlink():
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            if entry.name.startswith('.'):
                                continue
                            directory = Path(entry.path).resolve()
                            if any(directory.is_relative_to(r) for r in resolved_roots):
                                pending.append(directory)
                        elif Path(entry.name).suffix.lower() in PHOTOS | VIDEOS and entry.is_file(follow_symlinks=False):
                            # Parent is already canonical; ordinary files don't
                            # need another expensive Windows path resolution.
                            path = Path(entry.path)
                            files[str(path)] = path
                            JOB['discovered'] = len(files)
            except OSError as error:
                inaccessible.append(root)
                job_error(str(error))
    JOB.update(total=len(files), phase='indexing')
    with db() as c:
        existing = {row['id']: row for row in c.execute('SELECT id,mtime,size FROM media')}
    for n, path in enumerate(files.values()):
        JOB.update(processed=n, message=path.name)
        try:
            stat = path.stat()
            ident = hashlib.sha256(str(path).encode()).hexdigest()[:24]
            old = existing.get(ident)
            if old and old['mtime'] == stat.st_mtime and old['size'] == stat.st_size:
                continue
            kind = 'video' if path.suffix.lower() in VIDEOS else 'photo'
            captured = None
            duration = 0
            width = height = 0
            thumb = DATA / 'thumbs' / f'{ident}.jpg'
            if kind == 'photo':
                im, captured = photo_data(path)
                width, height = im.size
                im.thumbnail((720, 720))
                im.save(thumb, quality=83)
            else:
                info = ffprobe(path)
                stream = next((s for s in info.get('streams', []) if s.get('codec_type') == 'video'), {})
                width, height = stream.get('width', 0), stream.get('height', 0)
                duration = float(info.get('format', {}).get('duration', 0) or 0)
                raw = stream.get('tags', {}).get('creation_time') or info.get('format', {}).get('tags', {}).get('creation_time')
                if raw:
                    try:
                        captured = dt.datetime.fromisoformat(raw.replace('Z', '+00:00')).replace(tzinfo=None).isoformat()
                    except ValueError:
                        pass
                if shutil.which('ffmpeg'):
                    subprocess.run([shutil.which('ffmpeg'), '-v', 'error', '-y', '-i', str(path), '-frames:v', '1',
                                    '-vf', 'scale=720:720:force_original_aspect_ratio=decrease', str(thumb)],
                                   capture_output=True, timeout=60, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            source = 'capture date' if captured else 'file date'
            captured = captured or dt.datetime.fromtimestamp(stat.st_mtime).isoformat()
            with db() as c:
                c.execute('''INSERT INTO media(id,path,name,kind,captured,date_source,width,height,duration,mtime,size)
                  VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,
                  kind=excluded.kind,captured=excluded.captured,date_source=excluded.date_source,width=excluded.width,
                  height=excluded.height,duration=excluded.duration,mtime=excluded.mtime,size=excluded.size,face_done=0''',
                  (ident, str(path), path.name, kind, captured, source, width, height, duration, stat.st_mtime, stat.st_size))
                c.execute('DELETE FROM faces WHERE media_id=?', (ident,))
            (DATA / 'previews' / f'{ident}.jpg').unlink(missing_ok=True)
        except Exception as e:
            job_error(f'{path.name}: {e}')
    with db() as c:
        for row in c.execute('SELECT id,path FROM media').fetchall():
            if row['path'] not in files and not any(Path(row['path']).is_relative_to(Path(r)) for r in inaccessible):
                c.execute('DELETE FROM media WHERE id=?', (row['id'],))
        c.execute('DELETE FROM people WHERE id NOT IN (SELECT person_id FROM faces)')
    JOB.update(processed=len(files), phase='complete', message='Library is up to date')

def face_worker():
    import cv2
    import numpy as np
    cv2.setNumThreads(2)
    detector_path, recognizer_path = BASE / 'models/yunet.onnx', BASE / 'models/sface.onnx'
    if not detector_path.exists() or not recognizer_path.exists():
        raise ValueError('Face models are missing. Run setup.ps1 first.')
    detector = cv2.FaceDetectorYN.create(str(detector_path), '', (320, 320), 0.85)
    recognizer = cv2.FaceRecognizerSF.create(str(recognizer_path), '')
    with db() as c:
        rows = c.execute("SELECT * FROM media WHERE kind='photo' AND face_done=0").fetchall()
        groups = [(r['id'], np.frombuffer(r['feature'], dtype=np.float32).copy()) for r in c.execute('SELECT * FROM people')]
    JOB.update(total=len(rows), phase='grouping')
    for i, row in enumerate(rows):
        JOB.update(processed=i, message=row['name'])
        try:
            if not allowed(row['path']):
                continue
            im, _ = photo_data(row['path'])
            im.thumbnail((1600, 1600))
            frame = cv2.cvtColor(np.asarray(im), cv2.COLOR_RGB2BGR)
            detector.setInputSize((frame.shape[1], frame.shape[0]))
            _, detected = detector.detect(frame)
            new_groups = []
            with db() as c:
                for j, face in enumerate(detected if detected is not None else []):
                    feature = recognizer.feature(recognizer.alignCrop(frame, face)).flatten()
                    feature /= max(float(np.linalg.norm(feature)), 1e-8)
                    score, pid = max(((float(np.dot(feature, f)), p) for p, f in groups + new_groups), default=(0, None))
                    if score < 0.45:
                        pid = c.execute('INSERT INTO people(feature) VALUES(?)', (feature.tobytes(),)).lastrowid
                        new_groups.append((pid, feature.copy()))
                    x, y, w, h = face[:4]
                    crop = f'{row["id"]}-{j}.jpg'
                    im.crop((max(0, int(x-w*.2)), max(0, int(y-h*.2)), min(im.width, int(x+w*1.2)), min(im.height, int(y+h*1.2)))).resize((180, 180)).save(DATA / 'faces' / crop)
                    c.execute('INSERT INTO faces(media_id,person_id,crop,feature) VALUES(?,?,?,?)', (row['id'], pid, crop, feature.tobytes()))
                c.execute('UPDATE media SET face_done=1 WHERE id=?', (row['id'],))
            groups.extend(new_groups)
            JOB['processed'] = i + 1
        except Exception as e:
            job_error(f'{row["name"]}: {e}')
    JOB.update(processed=len(rows), phase='complete', message='Face grouping complete')

def start_job(kind, worker):
    if not LOCK.acquire(blocking=False):
        return jsonify(error='A library job is already running.'), 409
    JOB.update(running=True, kind=kind, phase='starting', discovered=0, processed=0, total=0, errors=[], error_count=0, message='Starting…')
    def run():
        try:
            worker()
        except Exception as e:
            job_error(str(e))
            JOB['message'] = 'Completed with errors'
        finally:
            JOB['running'] = False
            LOCK.release()
            if kind == 'scan' and JOB['queued_faces']:
                JOB['queued_faces'] = False
                with app.app_context():
                    start_job('faces', face_worker)
    threading.Thread(target=run, daemon=True).start()
    return jsonify(JOB), 202

@app.before_request
def local_only():
    if request.host not in {'127.0.0.1:4317', 'localhost:4317'}:
        abort(403)
    if request.method != 'GET' and request.headers.get('X-Memorylane-Token') != TOKEN:
        abort(403)

@app.after_request
def secure(response):
    if request.path.startswith('/api/'):
        response.headers['Cache-Control'] = 'no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['Content-Security-Policy'] = "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; media-src 'self'; frame-ancestors 'none'"
    return response

@app.errorhandler(400)
@app.errorhandler(404)
@app.errorhandler(409)
def error_response(error):
    return jsonify(error=str(error.description)), error.code

@app.get('/')
def home():
    return send_file(BASE / 'static/index.html')

@app.get('/api/status')
def status():
    with db() as c:
        stats = dict(c.execute("SELECT count(*) count,coalesce(sum(size),0) bytes,coalesce(sum(kind='video'),0) videos FROM media").fetchone())
        stats['people'] = c.execute('SELECT count(*) FROM people').fetchone()[0]
    return jsonify(token=TOKEN, folders=folders(), job=JOB, stats=stats,
                   faces_ready=all((BASE / 'models' / n).exists() for n in ['yunet.onnx','sface.onnx']), ffmpeg=bool(shutil.which('ffmpeg')))

@app.post('/api/settings')
def settings():
    if LOCK.locked():
        abort(409, 'Wait for the current library job to finish.')
    payload = request.get_json()
    values = payload.get('folders', payload.get('folder_text'))
    if isinstance(values, str):
        parsed = []
        for line in values.splitlines():
            line = line.strip()
            if not line:
                continue
            # Keep a valid literal directory containing a comma intact.
            if Path(line).is_dir():
                parsed.append(line)
            else:
                parsed.extend(next(csv.reader([line], skipinitialspace=True)))
        values = [v.strip() for v in parsed if v.strip()]
    if not isinstance(values, list) or not all(isinstance(v, str) for v in values):
        abort(400, 'Folders must be a list of paths.')
    roots = []
    for value in values:
        p = Path(value.strip()).expanduser()
        if not p.is_absolute() or not p.is_dir():
            abort(400, f'Folder not found: {value}')
        p = p.resolve()
        if str(p) not in roots:
            roots.append(str(p))
    with db() as c:
        c.execute("INSERT OR REPLACE INTO settings VALUES('folders',?)", (json.dumps(roots),))
        for row in c.execute('SELECT id,path FROM media').fetchall():
            if not any(Path(row['path']).is_relative_to(Path(r)) for r in roots):
                c.execute('DELETE FROM media WHERE id=?', (row['id'],))
        c.execute('DELETE FROM people WHERE id NOT IN (SELECT person_id FROM faces)')
    return start_job('scan', scan_worker)

@app.get('/api/storage')
def storage():
    def measure(path, names=None):
        size, count = 0, 0
        if names is not None:
            candidates = [path / n for n in names]
        else:
            # DirEntry.stat reuses directory metadata on Windows, avoiding two
            # filesystem round trips for every thumbnail in a large library.
            pending = [path]
            while pending:
                try:
                    with os.scandir(pending.pop()) as entries:
                        for entry in entries:
                            if entry.is_symlink():
                                continue
                            if entry.is_dir(follow_symlinks=False):
                                pending.append(entry.path)
                            elif entry.is_file(follow_symlinks=False):
                                size += entry.stat(follow_symlinks=False).st_size
                                count += 1
                except OSError:
                    continue
            return dict(bytes=size, files=count)
        for p in candidates:
            try:
                if p.is_file() and not p.is_symlink():
                    size += p.stat().st_size
                    count += 1
            except OSError:
                pass
        return dict(bytes=size, files=count)
    categories = []
    for key, label, path, names, is_cache in [
        ('thumbnails', 'Photo & video thumbnails', DATA/'thumbs', None, True),
        ('previews', 'Full-screen photo previews', DATA/'previews', None, True),
        ('faces', 'Face thumbnails', DATA/'faces', None, True),
        ('database', 'Library index, names & face data', DATA, ['library.sqlite','library.sqlite-wal','library.sqlite-shm'], False),
        ('models', 'Offline face models', BASE/'models', ['yunet.onnx','sface.onnx'], False),
        ('downloads', 'Cached installation packages', BASE/'.wheels', None, True),
        ('temporary', 'Incomplete model downloads', BASE/'models', ['yunet.download','sface.download'], True),
    ]:
        categories.append(dict(key=key, label=label, path=str(path), cache=is_cache, **measure(path,names)))
    return jsonify(location=str(DATA.resolve()), categories=categories,
                   cache_bytes=sum(r['bytes'] for r in categories if r['cache']),
                   total_bytes=sum(r['bytes'] for r in categories), measured_at=dt.datetime.now().isoformat())

@app.post('/api/scan')
def scan():
    return start_job('scan', scan_worker)

@app.post('/api/faces')
def faces():
    if LOCK.locked() and JOB['kind'] == 'scan':
        JOB['queued_faces'] = True
        return jsonify(JOB), 202
    return start_job('faces', face_worker)

def media_filters():
    clauses, args = ['1=1'], []
    if request.args.get('kind') in ('photo', 'video'):
        clauses.append('kind=?'); args.append(request.args['kind'])
    if request.args.get('favorite'):
        clauses.append('favorite=1')
    if request.args.get('person'):
        clauses.append('id IN (SELECT media_id FROM faces WHERE person_id=?)'); args.append(request.args['person'])
    if request.args.get('q'):
        clauses.append('(name LIKE ? OR captured LIKE ?)'); args.extend(['%'+request.args['q']+'%']*2)
    return clauses, args

@app.get('/api/timeline')
def timeline():
    clauses, args = media_filters()
    with db() as c:
        rows = [dict(r) for r in c.execute("SELECT substr(captured,1,7) month,count(*) count FROM media WHERE " +
                ' AND '.join(clauses) + ' GROUP BY substr(captured,1,7) ORDER BY month DESC', args)]
    return jsonify(rows)

@app.get('/api/media')
def media_list():
    clauses, args = media_filters()
    if request.args.get('from_month'):
        try:
            month = dt.datetime.strptime(request.args['from_month'], '%Y-%m')
            next_month = month.replace(year=month.year+1, month=1) if month.month == 12 else month.replace(month=month.month+1)
        except ValueError:
            abort(400, 'Invalid month')
        clauses.append('captured < ?'); args.append(next_month.isoformat())
    if request.args.get('cursor'):
        try:
            date, ident = json.loads(request.args['cursor'])
        except Exception:
            abort(400, 'Invalid page cursor')
        clauses.append('(captured < ? OR (captured = ? AND id < ?))'); args.extend([date, date, ident])
    with db() as c:
        rows = [dict(r) for r in c.execute('SELECT * FROM media WHERE '+' AND '.join(clauses)+' ORDER BY captured DESC,id DESC LIMIT 61', args)]
    more = len(rows) > 60
    rows = rows[:60]
    return jsonify(items=rows, next=json.dumps([rows[-1]['captured'], rows[-1]['id']]) if more else None)

def get_media(ident):
    with db() as c:
        row = c.execute('SELECT * FROM media WHERE id=?', (ident,)).fetchone()
    if not row or not allowed(row['path']) or not Path(row['path']).is_file():
        abort(404, 'Original is unavailable. Check the folder and rescan.')
    return row

@app.get('/media/<ident>/<variant>')
def serve_media(ident, variant):
    row = get_media(ident)
    if variant == 'thumb':
        p = DATA / 'thumbs' / f'{ident}.jpg'
        if not p.exists():
            return send_file(BASE / 'static/video.svg')
    elif variant == 'view' and row['kind'] == 'photo':
        p = DATA / 'previews' / f'{ident}.jpg'
        if not p.exists():
            im, _ = photo_data(row['path'])
            im.thumbnail((2560,2560))
            im.save(p, quality=92)
    elif variant in ('original','view'):
        p = Path(row['path'])
    else:
        abort(404)
    return send_file(p, conditional=True, max_age=3600)

@app.post('/api/media/<ident>/favorite')
def favorite(ident):
    get_media(ident)
    with db() as c:
        c.execute('UPDATE media SET favorite=1-favorite WHERE id=?', (ident,))
        value = c.execute('SELECT favorite FROM media WHERE id=?', (ident,)).fetchone()[0]
    return jsonify(favorite=value)

@app.get('/api/memories')
def memories():
    today = dt.date.today()
    stories = []
    with db() as c:
        years = [r[0] for r in c.execute('SELECT DISTINCT substr(captured,1,4) FROM media ORDER BY captured DESC')]
        for year in years:
            ago = today.year - int(year)
            if ago < 1:
                continue
            target = today.replace(year=int(year), day=min(today.day, 28) if today.month == 2 else today.day)
            start, end = target - dt.timedelta(days=7), target + dt.timedelta(days=7)
            rows = [dict(r) for r in c.execute('SELECT * FROM media WHERE captured>=? AND captured<? ORDER BY captured LIMIT 100', (start.isoformat(), (end+dt.timedelta(days=1)).isoformat()))]
            if rows:
                stories.append(dict(title=f'{ago} year'+('s' if ago != 1 else '')+' ago', subtitle='Around this time', items=rows))
    return jsonify(stories)

@app.get('/api/people')
def people():
    with db() as c:
        rows = [dict(r) for r in c.execute('''SELECT p.id,p.name,count(DISTINCT f.media_id) count,min(f.crop) crop
            FROM people p JOIN faces f ON f.person_id=p.id GROUP BY p.id ORDER BY count DESC''')]
    return jsonify(rows)

@app.get('/face/<name>')
def face_crop(name):
    with db() as c:
        row = c.execute('SELECT media_id FROM faces WHERE crop=?', (name,)).fetchone()
    if not row:
        abort(404)
    get_media(row['media_id'])
    return send_file(DATA / 'faces' / name, conditional=True)

@app.post('/api/people/<int:ident>')
def name_person(ident):
    name = request.get_json().get('name', '')
    if not isinstance(name, str) or len(name) > 100:
        abort(400, 'Use a name under 100 characters.')
    with db() as c:
        c.execute('UPDATE people SET name=? WHERE id=?', (name.strip(), ident))
    return jsonify(ok=True)

@app.post('/api/people/<int:ident>/merge')
def merge_person(ident):
    if LOCK.locked():
        abort(409, 'Wait for the current job to finish.')
    target = request.get_json().get('target')
    with db() as c:
        if target == ident or not c.execute('SELECT id FROM people WHERE id=?', (target,)).fetchone():
            abort(400, 'Choose another person.')
        c.execute('UPDATE faces SET person_id=? WHERE person_id=?', (target, ident))
        c.execute('DELETE FROM people WHERE id=?', (ident,))
    return jsonify(ok=True)

if __name__ == '__main__':
    from waitress import serve
    print('Memorylane is ready at http://127.0.0.1:4317', flush=True)
    serve(app, host='127.0.0.1', port=4317, threads=6)
