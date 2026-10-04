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
import math
from collections import OrderedDict
from contextlib import contextmanager
from flask import Flask, request, jsonify, send_file, abort
from PIL import Image, ImageOps
from pillow_heif import register_heif_opener
from jobs import JOBS, LOCKS, STOPS, launch, record_error
from search_cache import SearchCache
from intelligence import LocalCLIP, AI_BUDGET, photo_coordinates, video_frames

register_heif_opener()
BASE = Path(__file__).resolve().parent
DATA = Path(os.environ.get('MEMORYLANE_DATA', str(BASE / 'data')))
DATA.mkdir(parents=True, exist_ok=True)
for folder in ('thumbs', 'faces', 'previews'):
    (DATA / folder).mkdir(exist_ok=True)
app = Flask(__name__, static_folder=str(BASE / 'static'))
app.config['MAX_CONTENT_LENGTH'] = 1024 * 1024
TOKEN = secrets.token_urlsafe(32)
LOCK = LOCKS['scan']
JOB = JOBS['scan']
CLIP = LocalCLIP(BASE / 'models/clip')
VISUAL_INDEX = SearchCache(DATA / 'search-vectors.npz')
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
    columns = {r[1] for r in c.execute('PRAGMA table_info(media)')}
    for name, definition in [('latitude','REAL'), ('longitude','REAL'), ('gps_done','INTEGER DEFAULT 0')]:
        if name not in columns:
            c.execute(f'ALTER TABLE media ADD COLUMN {name} {definition}')
    if 'seconds' not in {r[1] for r in c.execute('PRAGMA table_info(faces)')}:
        c.execute('ALTER TABLE faces ADD COLUMN seconds REAL DEFAULT 0')
    c.executescript('''CREATE INDEX IF NOT EXISTS media_gps ON media(latitude,longitude);
      CREATE INDEX IF NOT EXISTS media_faces_pending ON media(face_done,captured DESC);
      CREATE INDEX IF NOT EXISTS media_gps_pending ON media(gps_done,captured DESC);
      CREATE TABLE IF NOT EXISTS embeddings(media_id TEXT PRIMARY KEY REFERENCES media(id) ON DELETE CASCADE,
        vector BLOB, mtime REAL, size INTEGER);
      CREATE INDEX IF NOT EXISTS embedding_cache_meta ON embeddings(media_id,mtime,size);
      CREATE TABLE IF NOT EXISTS embedding_errors(media_id TEXT PRIMARY KEY REFERENCES media(id) ON DELETE CASCADE,
        mtime REAL, size INTEGER);
    ''')

def folders():
    with db() as c:
        row = c.execute("SELECT value FROM settings WHERE key='folders'").fetchone()
    return json.loads(row[0]) if row else []

def allowed(path):
    p = Path(path).resolve()
    return any(p.is_relative_to(Path(root).resolve()) for root in folders())

def job_error(message):
    record_error(message)

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
            if STOPS['scan'].is_set():
                return
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
        if STOPS['scan'].is_set():
            return
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
                    subprocess.run([shutil.which('ffmpeg'), '-v', 'error', '-y', '-threads', '1', '-i', str(path), '-frames:v', '1',
                                    '-vf', 'scale=720:720:force_original_aspect_ratio=decrease', '-threads','1', str(thumb)],
                                   capture_output=True, timeout=60, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            source = 'capture date' if captured else 'file date'
            captured = captured or dt.datetime.fromtimestamp(stat.st_mtime).isoformat()
            with db() as c:
                c.execute('''INSERT INTO media(id,path,name,kind,captured,date_source,width,height,duration,mtime,size)
                  VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,
                  kind=excluded.kind,captured=excluded.captured,date_source=excluded.date_source,width=excluded.width,
                  height=excluded.height,duration=excluded.duration,mtime=excluded.mtime,size=excluded.size,face_done=0,
                  gps_done=0,latitude=NULL,longitude=NULL''',
                  (ident, str(path), path.name, kind, captured, source, width, height, duration, stat.st_mtime, stat.st_size))
                c.execute('DELETE FROM faces WHERE media_id=?', (ident,))
                c.execute('DELETE FROM embeddings WHERE media_id=?', (ident,))
                c.execute('DELETE FROM embedding_errors WHERE media_id=?', (ident,))
            (DATA / 'previews' / f'{ident}.jpg').unlink(missing_ok=True)
        except Exception as e:
            job_error(f'{path.name}: {e}')
    with db() as c:
        for row in c.execute('SELECT id,path FROM media').fetchall():
            if row['path'] not in files and not any(Path(row['path']).is_relative_to(Path(r)) for r in inaccessible):
                c.execute('DELETE FROM media WHERE id=?', (row['id'],))
        c.execute('DELETE FROM people WHERE id NOT IN (SELECT person_id FROM faces)')
    JOB.update(processed=len(files), phase='complete', message='Library is up to date')

from workers import LibraryWorkers
WORKERS = LibraryWorkers(db, DATA, BASE, photo_data, allowed, ffprobe, CLIP)

def face_worker():
    WORKERS.faces()

def start_job(kind, worker):
    if not launch(kind, worker):
        return jsonify(error=f'The {kind} job is already running.'), 409
    return jsonify(JOBS[kind]), 202

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
        stats['search_indexed'] = c.execute('SELECT count(*) FROM embeddings').fetchone()[0]
        stats['geotagged'] = c.execute('SELECT count(*) FROM media WHERE latitude IS NOT NULL').fetchone()[0]
    return jsonify(token=TOKEN, folders=folders(), job=JOB, jobs=JOBS, search_ready=CLIP.ready(), stats=stats,
                   faces_ready=all((BASE / 'models' / n).exists() for n in ['yunet.onnx','sface.onnx']), ffmpeg=bool(shutil.which('ffmpeg')))

@app.post('/api/settings')
def settings():
    if any(lock.locked() for lock in LOCKS.values()):
        abort(409, 'Pause background jobs before changing folders or merging groups.')
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
        ('search_cache', 'Visual search cache', DATA, ['search-vectors.npz','search-vectors.tmp'], True),
        ('database', 'Library index, names & face data', DATA, ['library.sqlite','library.sqlite-wal','library.sqlite-shm'], False),
        ('models', 'Offline face models', BASE/'models', ['yunet.onnx','sface.onnx'], False),
        ('search_models', 'Offline descriptive search models', BASE/'models/clip', None, False),
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
    return start_job('faces', face_worker)

@app.post('/api/places/index')
def index_places():
    return start_job('places', WORKERS.places)

@app.post('/api/search/index')
def index_search():
    return start_job('search', WORKERS.search)

@app.post('/api/jobs/<kind>/pause')
def pause_job(kind):
    if kind not in STOPS:
        abort(404)
    STOPS[kind].set()
    return jsonify(ok=True)

def media_filters(include_query=True):
    clauses, args = ['1=1'], []
    if request.args.get('kind') in ('photo', 'video'):
        clauses.append('kind=?'); args.append(request.args['kind'])
    if request.args.get('favorite'):
        clauses.append('favorite=1')
    if request.args.get('person'):
        clauses.append('id IN (SELECT media_id FROM faces WHERE person_id=?)'); args.append(request.args['person'])
    if include_query and request.args.get('q'):
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
    base_clauses, base_args = list(clauses), list(args)
    newer = request.args.get('newer')
    if request.args.get('from_month') and not newer:
        try:
            month = dt.datetime.strptime(request.args['from_month'], '%Y-%m')
            next_month = month.replace(year=month.year+1, month=1) if month.month == 12 else month.replace(month=month.month+1)
        except ValueError:
            abort(400, 'Invalid month')
        clauses.append('captured < ?'); args.append(next_month.isoformat())
    if request.args.get('cursor') or newer:
        try:
            date, ident = json.loads(newer or request.args['cursor'])
        except Exception:
            abort(400, 'Invalid page cursor')
        op = '>' if newer else '<'
        clauses.append(f'(captured {op} ? OR (captured = ? AND id {op} ?))'); args.extend([date, date, ident])
    with db() as c:
        order = 'ASC' if newer else 'DESC'
        rows = [dict(r) for r in c.execute('SELECT * FROM media WHERE '+' AND '.join(clauses)+f' ORDER BY captured {order},id {order} LIMIT 61', args)]
        more = len(rows) > 60
        rows = rows[:60]
        if newer:
            rows.reverse()
        previous = None
        if rows:
            first = rows[0]
            has_previous = c.execute('SELECT 1 FROM media WHERE '+' AND '.join(base_clauses)+
                ' AND (captured > ? OR (captured = ? AND id > ?)) LIMIT 1', base_args+[first['captured'],first['captured'],first['id']]).fetchone()
            if has_previous:
                previous = json.dumps([first['captured'],first['id']])
        if request.args.get('person'):
            for row in rows:
                row['face_time'] = c.execute('SELECT min(seconds) FROM faces WHERE media_id=? AND person_id=?',
                    (row['id'],request.args['person'])).fetchone()[0] or 0
    return jsonify(items=rows, next=json.dumps([rows[-1]['captured'], rows[-1]['id']]) if rows and more else None, previous=previous)

SEARCH_CACHE = OrderedDict()
SEARCH_LOCK = threading.Lock()

@app.get('/api/search')
def semantic_search():
    import numpy as np
    query = request.args.get('q','').strip()
    if not query or len(query)>500:
        abort(400,'Describe what you want to find in 500 characters or less.')
    if not CLIP.ready():
        abort(409,'Install the local search models with setup.ps1 first.')
    clauses,args=media_filters(include_query=False)
    key=(query,tuple(clauses),tuple(args))
    try:
        offset=int(request.args.get('cursor','0'))
        if not 0<=offset<=300:raise ValueError()
    except ValueError:
        abort(400,'Invalid search cursor')
    with SEARCH_LOCK:
        cached=SEARCH_CACHE.get(key)
        if not cached or time.monotonic()-cached[0]>90:
            query_vector=CLIP.text_vector(query)
            with db() as c:
                best=VISUAL_INDEX.rank(c,query_vector,clauses,args)
            cached=(time.monotonic(),best)
            SEARCH_CACHE[key]=cached
            while len(SEARCH_CACHE)>8:SEARCH_CACHE.popitem(last=False)
    results=[]
    with db() as c:
        for ident,score in cached[1][offset:offset+60]:
            row=c.execute('SELECT * FROM media WHERE id=?',(ident,)).fetchone()
            if row:
                item=dict(row);item['similarity']=score;results.append(item)
    return jsonify(items=results,next=str(offset+60) if len(cached[1])>offset+60 else None,previous=None,
                   semantic=True,ranked=len(cached[1]))

def geographic_bounds():
    try:
        south=float(request.args.get('south',-90))
        north=float(request.args.get('north',90))
        west=float(request.args.get('west',-180))
        east=float(request.args.get('east',180))
        if not all(math.isfinite(v) for v in [south,north,west,east]) or south>north or west>east:
            raise ValueError()
        return max(-90,south),min(90,north),max(-180,west),min(180,east)
    except ValueError:
        abort(400,'Invalid map bounds')

@app.get('/api/places')
def places_map():
    south,north,west,east=geographic_bounds()
    try:
        zoom=max(1,min(18,int(request.args.get('zoom',2))))
    except ValueError:
        abort(400,'Invalid zoom')
    cell=360/(2**zoom*4)
    with db() as c:
        summary=dict(c.execute('''SELECT count(*) count,min(latitude) south,max(latitude) north,
             min(longitude) west,max(longitude) east FROM media WHERE latitude IS NOT NULL''').fetchone())
        rows=c.execute('''SELECT cast((latitude+90)/? AS INTEGER) y,cast((longitude+180)/? AS INTEGER) x,
             avg(latitude) latitude,avg(longitude) longitude,count(*) count,min(id) cover
             FROM media WHERE latitude BETWEEN ? AND ? AND longitude BETWEEN ? AND ?
             GROUP BY y,x ORDER BY count DESC LIMIT 1501''',(cell,cell,south,north,west,east)).fetchall()
    groups=[]
    for row in rows[:1500]:
        item=dict(row)
        item.update(south=item.pop('y')*cell-90,west=item.pop('x')*cell-180)
        item.update(north=item['south']+cell,east=item['west']+cell)
        groups.append(item)
    return jsonify(summary=summary,groups=groups,truncated=len(rows)>1500)

@app.get('/api/places/media')
def place_media():
    south,north,west,east=geographic_bounds()
    clauses=['latitude>=?','latitude<?','longitude>=?','longitude<?']
    args=[south,north,west,east]
    if request.args.get('cursor'):
        try:date,ident=json.loads(request.args['cursor'])
        except Exception:abort(400,'Invalid cursor')
        clauses.append('(captured < ? OR (captured=? AND id<?))');args.extend([date,date,ident])
    with db() as c:
        rows=[dict(r) for r in c.execute('SELECT * FROM media WHERE '+' AND '.join(clauses)+' ORDER BY captured DESC,id DESC LIMIT 61',args)]
    more=len(rows)>60;rows=rows[:60]
    return jsonify(items=rows,next=json.dumps([rows[-1]['captured'],rows[-1]['id']]) if more else None)

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

@app.get('/api/people/suggestions')
def people_suggestions():
    from face_groups import suggestions
    with db() as c:
        return jsonify(suggestions(c))

@app.get('/api/people')
def people():
    with db() as c:
        rows = [dict(r) for r in c.execute('''SELECT p.id,p.name,count(DISTINCT f.media_id) count,min(f.crop) crop,
            count(DISTINCT CASE WHEN m.kind='video' THEN m.id END) videos
            FROM people p JOIN faces f ON f.person_id=p.id JOIN media m ON m.id=f.media_id GROUP BY p.id ORDER BY count DESC''')]
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
    target = request.get_json().get('target')
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        if target == ident or not c.execute('SELECT id FROM people WHERE id=?', (target,)).fetchone():
            abort(400, 'Choose another person.')
        c.execute('UPDATE faces SET person_id=? WHERE person_id=?', (target, ident))
        c.execute('DELETE FROM people WHERE id=?', (ident,))
        c.execute("INSERT INTO settings(key,value) VALUES('face_revision','1') ON CONFLICT(key) DO UPDATE SET value=CAST(value AS INTEGER)+1")
    return jsonify(ok=True)

if __name__ == '__main__':
    from waitress import serve
    print('Memorylane is ready at http://127.0.0.1:4317', flush=True)
    serve(app, host='127.0.0.1', port=4317, threads=6)
