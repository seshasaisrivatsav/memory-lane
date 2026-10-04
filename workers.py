"""Restartable metadata and AI workers; expensive work stays outside SQLite writes."""
import io
from pathlib import Path
import numpy as np
from PIL import Image
from jobs import JOBS, STOPS, record_error
from intelligence import AI_BUDGET, video_frames, photo_coordinates
from face_groups import portrait, representatives, match, consolidate

class LibraryWorkers:
    def __init__(self, db, data, base, photo_data, allowed, probe, clip):
        self.db, self.data, self.base = db, data, base
        self.photo_data, self.allowed, self.probe, self.clip = photo_data, allowed, probe, clip

    def batches(self, kind, condition, batch=16):
        job = JOBS[kind]
        while not STOPS[kind].is_set():
            with self.db() as c:
                rows = c.execute('SELECT * FROM media m WHERE '+condition+' ORDER BY captured DESC,id DESC LIMIT ?', (batch,)).fetchall()
                remaining = c.execute('SELECT count(*) FROM media m WHERE '+condition).fetchone()[0]
            job['total'] = job['processed'] + remaining
            if not rows:
                if JOBS['scan']['running']:
                    job.update(phase='waiting', message='Caught up. Waiting for newly indexed files…')
                    STOPS[kind].wait(1)
                    continue
                return
            for row in rows:
                if STOPS[kind].is_set():
                    return
                job.update(phase='processing', message=row['name'])
                yield row
                job['processed'] += 1

    @staticmethod
    def unchanged(c, row):
        latest = c.execute('SELECT mtime,size FROM media WHERE id=?', (row['id'],)).fetchone()
        return latest and latest['mtime'] == row['mtime'] and latest['size'] == row['size']

    def faces(self):
        import cv2
        cv2.setNumThreads(2)
        detector = cv2.FaceDetectorYN.create(str(self.base/'models/yunet.onnx'), '', (320,320), 0.85)
        recognizer = cv2.FaceRecognizerSF.create(str(self.base/'models/sface.onnx'), '')
        with self.db() as c:
            c.execute('UPDATE media SET face_done=0 WHERE face_done=-1')
        JOBS['faces'].update(phase='refining groups',message='Combining high-confidence duplicate groups')
        for _ in range(4):
            if STOPS['faces'].is_set():
                return
            with self.db() as c:
                c.execute('BEGIN IMMEDIATE')
                merged=consolidate(c)
            if not merged:
                break
        self.repair_portraits(detector, recognizer, cv2)
        with self.db() as c:
            groups = representatives(c)
            revision = c.execute("SELECT value FROM settings WHERE key='face_revision'").fetchone()
            revision = revision[0] if revision else None
        for row in self.batches('faces', "face_done=0 AND substr(name,1,2)!='._'"):
            try:
                if not self.allowed(row['path']):
                    raise ValueError('Folder is no longer connected')
                if row['kind'] == 'photo':
                    image, _ = self.photo_data(row['path'])
                    image.thumbnail((1600,1600))
                    frames = [(0.0, image)]
                else:
                    duration = row['duration']
                    if not duration:
                        duration = float(self.probe(row['path']).get('format',{}).get('duration',0))
                    frames = video_frames(row['path'], duration)
                found = []
                for seconds, image in frames:
                    if STOPS['faces'].is_set():
                        return
                    frame = cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2BGR)
                    JOBS['faces']['message'] = row['name'] + (f' · frame {seconds:.1f}s' if row['kind']=='video' else '')
                    with AI_BUDGET:
                        detector.setInputSize((frame.shape[1],frame.shape[0]))
                        _, detections = detector.detect(frame)
                        for face in detections if detections is not None else []:
                            feature = recognizer.feature(recognizer.alignCrop(frame,face)).flatten()
                            feature /= max(float(np.linalg.norm(feature)),1e-8)
                            # One representative per matching face in a video.
                            if any(float(np.dot(feature,f[0])) >= .55 for f in found):
                                continue
                            crop=portrait(image, face)
                            found.append((feature,seconds,crop))
                # Crop files are prepared before holding the write transaction.
                for i,(_,_,crop) in enumerate(found):
                    crop.save(self.data/'faces'/f'{row["id"]}-{i}-square.jpg')
                with self.db() as c:
                    c.execute('BEGIN IMMEDIATE')
                    if not self.unchanged(c,row):
                        continue
                    latest=c.execute("SELECT value FROM settings WHERE key='face_revision'").fetchone()
                    latest=latest[0] if latest else None
                    if latest!=revision:
                        groups=representatives(c)
                        revision=latest
                    c.execute('DELETE FROM faces WHERE media_id=?',(row['id'],))
                    seen=set()
                    for i,(feature,seconds,_) in enumerate(found):
                        pid=match(feature, groups, seen if row['kind']=='photo' else ())
                        if pid is None:
                            pid=c.execute('INSERT INTO people(feature) VALUES(?)',(feature.tobytes(),)).lastrowid
                            groups[pid]=[]
                        groups[pid]=(groups[pid]+[feature])[-9:]
                        if pid in seen:
                            continue
                        seen.add(pid)
                        c.execute('INSERT INTO faces(media_id,person_id,crop,feature,seconds) VALUES(?,?,?,?,?)',
                                  (row['id'],pid,f'{row["id"]}-{i}-square.jpg',feature.tobytes(),seconds))
                    c.execute('UPDATE media SET face_done=1 WHERE id=?',(row['id'],))
            except Exception as error:
                record_error(f'{row["name"]}: {error}', 'faces')
                with self.db() as c:
                    c.execute('UPDATE media SET face_done=-1 WHERE id=? AND mtime=? AND size=?',(row['id'],row['mtime'],row['size']))
        JOBS['faces']['message']='Photo and video face grouping complete'

    def repair_portraits(self, detector, recognizer, cv2):
        with self.db() as c:
            rows=c.execute("""SELECT m.*,f.id face_id,f.feature,f.crop,f.seconds FROM faces f
              JOIN media m ON m.id=f.media_id WHERE f.crop IN
              (SELECT min(crop) FROM faces GROUP BY person_id) AND f.crop NOT LIKE '%-square.jpg'
              ORDER BY (SELECT count(*) FROM faces x WHERE x.person_id=f.person_id) DESC""").fetchall()
        for n,row in enumerate(rows):
            if STOPS['faces'].is_set():
                return
            JOBS['faces'].update(phase='repairing portraits', processed=n,total=len(rows),message='Correcting existing face crops')
            try:
                if not self.allowed(row['path']):
                    continue
                if row['kind']=='photo':
                    image,_=self.photo_data(row['path'])
                    image.thumbnail((1600,1600))
                else:
                    # Original representative is usually the first sampled frame.
                    image=next(video_frames(row['path'],row['duration'],times=[row['seconds'] or 0]))[1]
                frame=cv2.cvtColor(np.asarray(image),cv2.COLOR_RGB2BGR)
                target=np.frombuffer(row['feature'],dtype=np.float32)
                best=None
                with AI_BUDGET:
                    detector.setInputSize((frame.shape[1],frame.shape[0]))
                    _,found=detector.detect(frame)
                    for face in found if found is not None else []:
                        vector=recognizer.feature(recognizer.alignCrop(frame,face)).flatten()
                        vector/=max(float(np.linalg.norm(vector)),1e-8)
                        score=float(vector @ target)
                        if best is None or score>best[0]:
                            best=(score,face)
                if best is not None and best[0]>=.45:
                    filename=Path(row['crop']).stem+'-square.jpg'
                    portrait(image,best[1]).save(self.data/'faces'/filename)
                    with self.db() as c:
                        c.execute('UPDATE faces SET crop=? WHERE id=?',(filename,row['face_id']))
            except Exception:
                # Unavailable originals keep their old cover; recognition continues.
                pass
        JOBS['faces'].update(processed=0,total=0)

    def places(self):
        with self.db() as c:
            c.execute('UPDATE media SET gps_done=0 WHERE gps_done=-1')
        for row in self.batches('places', "gps_done=0 AND kind='photo' AND substr(name,1,2)!='._'",64):
            try:
                if not self.allowed(row['path']):
                    raise ValueError('Folder is no longer connected')
                lat,lon=photo_coordinates(row['path'])
                with self.db() as c:
                    c.execute('UPDATE media SET latitude=?,longitude=?,gps_done=1 WHERE id=? AND mtime=? AND size=?',
                              (lat,lon,row['id'],row['mtime'],row['size']))
            except Exception as error:
                record_error(f'{row["name"]}: {error}','places')
                with self.db() as c:
                    c.execute('UPDATE media SET gps_done=-1 WHERE id=? AND mtime=? AND size=?',(row['id'],row['mtime'],row['size']))
        JOBS['places']['message']='Photo GPS indexing complete'

    def search(self):
        self.clip.load()
        with self.db() as c:
            c.execute('DELETE FROM embedding_errors')
        condition="substr(name,1,2)!='._' AND "+'NOT EXISTS(SELECT 1 FROM embeddings e WHERE e.media_id=m.id) AND NOT EXISTS(SELECT 1 FROM embedding_errors e WHERE e.media_id=m.id)'
        for row in self.batches('search',condition,32):
            try:
                if not self.allowed(row['path']):
                    raise ValueError('Folder is no longer connected')
                # Thumbnail encoding is bounded; videos use their poster frame.
                with Image.open(self.data/'thumbs'/f'{row["id"]}.jpg') as image:
                    vector=self.clip.image_vector(image)
                with self.db() as c:
                    c.execute('BEGIN IMMEDIATE')
                    if self.unchanged(c,row):
                        c.execute('INSERT OR REPLACE INTO embeddings VALUES(?,?,?,?)',(row['id'],vector.tobytes(),row['mtime'],row['size']))
            except Exception as error:
                record_error(f'{row["name"]}: {error}','search')
                with self.db() as c:
                    if self.unchanged(c,row):
                        c.execute('INSERT OR REPLACE INTO embedding_errors VALUES(?,?,?)',(row['id'],row['mtime'],row['size']))
        JOBS['search']['message']='Descriptive search indexing complete'
