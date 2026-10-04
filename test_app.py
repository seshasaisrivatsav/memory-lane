import datetime as dt
import importlib
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from PIL import Image

class LibraryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        os.environ['MEMORYLANE_DATA'] = str(cls.root / 'index')
        cls.module = importlib.import_module('app')
        cls.client = cls.module.app.test_client()
        cls.headers = {'Host':'127.0.0.1:4317','X-Memorylane-Token':cls.module.TOKEN}
        cls.photos = cls.root / 'photos'
        cls.photos.mkdir()
        now = dt.datetime.now()
        for i in range(65):
            im = Image.new('RGB', (120,80), (i*3,100,80))
            exif = Image.Exif()
            exif[306] = now.replace(year=now.year-1).strftime('%Y:%m:%d %H:%M:%S')
            im.save(cls.photos / f'photo-{i:03d}.jpg', exif=exif)
        cls.original = (cls.photos / 'photo-000.jpg').read_bytes()
        (cls.photos / '._photo-000.jpg').write_bytes(b'Mac metadata is not an image')

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def req(self, method, path, data=None):
        return getattr(self.client, method)(path, json=data, headers=self.headers)

    def wait_job(self, kind='scan'):
        job=self.module.JOBS[kind]
        deadline=time.monotonic()+20
        while job['running'] and time.monotonic()<deadline:
            time.sleep(.02)
        self.assertFalse(job['running'])
        self.assertEqual(job['errors'], [])

    def test_01_local_security(self):
        self.assertEqual(self.client.get('/api/status', headers={'Host':'evil.example'}).status_code,403)
        self.assertEqual(self.client.post('/api/scan',headers={'Host':'127.0.0.1:4317'}).status_code,403)

    def test_02_scan_pagination_and_memories(self):
        response=self.req('post','/api/settings',{'folders':[str(self.photos),str(self.photos)]})
        self.assertEqual(response.status_code,202)
        self.wait_job()
        first=self.req('get','/api/media').json
        self.assertEqual(len(first['items']),60)
        from urllib.parse import urlencode
        second=self.req('get','/api/media?'+urlencode({'cursor':first['next']})).json
        self.assertEqual(len(second['items']),5)
        self.assertFalse(set(r['id'] for r in first['items']) & set(r['id'] for r in second['items']))
        self.assertIsNone(second['next'])
        stories=self.req('get','/api/memories').json
        self.assertEqual(stories[0]['title'],'1 year ago')
        self.assertEqual(len(stories[0]['items']),65)
        self.assertEqual(len(self.module.folders()),1)

    def test_03_view_and_favorite(self):
        item=self.req('get','/api/media?q=photo-000').json['items'][0]
        ident=item['id']
        for variant in ('thumb','view','original'):
            response=self.req('get',f'/media/{ident}/{variant}')
            self.assertEqual(response.status_code,200)
            response.close()
        self.assertEqual(self.req('post',f'/api/media/{ident}/favorite',{}).json['favorite'],1)
        self.assertEqual(len(self.req('get','/api/media?favorite=1').json['items']),1)
        self.assertEqual((self.photos / 'photo-000.jpg').read_bytes(),self.original)
        self.assertEqual(self.req('get','/media/not-real/original').status_code,404)

    def test_03b_month_jumps(self):
        months=self.req('get','/api/timeline').json
        self.assertEqual(sum(m['count'] for m in months),65)
        month=months[0]['month']
        items=self.req('get','/api/media?from_month='+month).json['items']
        self.assertEqual(len(items),60)
        self.assertTrue(all(i['captured'].startswith(month) for i in items))
        self.assertEqual(self.req('get','/api/media?from_month=invalid').status_code,400)
        self.assertEqual(self.req('get','/api/timeline?kind=video').json,[])

    def test_03c_bidirectional_pagination(self):
        from urllib.parse import urlencode
        first=self.req('get','/api/media').json
        old=self.req('get','/api/media?'+urlencode({'cursor':first['next']})).json
        self.assertIsNotNone(old['previous'])
        newer=self.req('get','/api/media?'+urlencode({'newer':old['previous']})).json
        self.assertEqual([r['id'] for r in newer['items']],[r['id'] for r in first['items']])
        self.assertIsNone(newer['previous'])

    def test_04_face_models_and_empty_detection(self):
        self.assertEqual(self.req('post','/api/faces',{}).status_code,202)
        self.wait_job('faces')
        self.assertEqual(self.req('get','/api/people').json,[])
        with self.module.db() as c:
            self.assertEqual(c.execute('SELECT count(*) FROM media WHERE face_done=1').fetchone()[0],65)

    def add_media(self, name, image=None, kind='photo', duration=0):
        path=self.photos/name
        if image is not None:image.save(path)
        stat=path.stat()
        with self.module.db() as c:
            c.execute('INSERT INTO media(id,path,name,kind,captured,mtime,size,duration) VALUES(?,?,?,?,?,?,?,?)',
                (name,str(path),name,kind,'2020-01-01T12:00:00',stat.st_mtime,stat.st_size,duration))
        return path

    def test_04b_gps_and_places(self):
        from intelligence import exif_coordinates,photo_coordinates
        gps={1:'S',2:(33,30,0),3:'W',4:(70,40,0)}
        self.assertEqual(exif_coordinates(gps),(-33.5,-70-40/60))
        self.assertEqual(exif_coordinates({}), (None,None))
        self.assertEqual(exif_coordinates({1:'N',2:(91,0,0),3:'E',4:(1,0,0)}),(None,None))
        image=Image.new('RGB',(120,80),'green')
        path=self.add_media('gps.jpg',image)
        exif=Image.Exif();exif[34853]={1:'N',2:(20.,30.,0.),3:'E',4:(78.,15.,0.)}
        image.save(path,exif=exif)
        stat=path.stat()
        with self.module.db() as c:c.execute('UPDATE media SET mtime=?,size=? WHERE id=?',(stat.st_mtime,stat.st_size,'gps.jpg'))
        self.assertEqual(photo_coordinates(path),(20.5,78.25))
        self.assertEqual(self.req('post','/api/places/index',{}).status_code,202)
        self.wait_job('places')
        result=self.req('get','/api/places').json
        self.assertEqual(result['summary']['count'],1)
        self.assertEqual(result['groups'][0]['latitude'],20.5)
        self.assertEqual(len(self.req('get','/api/places/media?south=20&north=21&west=78&east=79').json['items']),1)
        self.assertEqual(self.req('get','/api/places?south=nan').status_code,400)

    def test_04c_parallel_jobs(self):
        import threading
        from jobs import launch,JOBS
        release=threading.Event();started=threading.Event()
        def scanning():
            started.set();release.wait(5)
        self.assertTrue(launch('scan',scanning));started.wait(1)
        try:
            self.assertTrue(launch('faces',lambda:None))
            self.wait_job('faces')
            self.assertTrue(JOBS['scan']['running'])
        finally:release.set()
        self.wait_job()

    def test_04d_video_sampling_and_people(self):
        import shutil,subprocess
        from intelligence import sample_times,video_frames
        self.assertEqual(len(sample_times(3600)),24)
        self.assertEqual(len(sample_times(2)),1)
        if not shutil.which('ffmpeg'):self.skipTest('FFmpeg unavailable')
        path=self.photos/'sample.mp4'
        subprocess.run([shutil.which('ffmpeg'),'-v','error','-y','-f','lavfi','-i','color=c=blue:s=160x120:d=2','-c:v','libx264',str(path)],check=True,capture_output=True)
        self.add_media('sample.mp4',kind='video',duration=2)
        frames=list(video_frames(path,2));self.assertEqual(len(frames),1)
        self.assertEqual(self.req('post','/api/faces',{}).status_code,202)
        self.wait_job('faces')
        with self.module.db() as c:
            self.assertEqual(c.execute("SELECT face_done FROM media WHERE id='sample.mp4'").fetchone()[0],1)
            pid=c.execute("INSERT INTO people(name) VALUES('Test person')").lastrowid
            c.execute('INSERT INTO faces(media_id,person_id,seconds) VALUES(?,?,?)',('sample.mp4',pid,1.25))
        result=self.req('get',f'/api/media?person={pid}').json
        self.assertEqual(result['items'][0]['face_time'],1.25)
        ranged=self.client.get('/media/sample.mp4/original',headers={**self.headers,'Range':'bytes=0-99'})
        self.assertEqual(ranged.status_code,206);ranged.close()

    def test_04e_semantic_model_and_ranking(self):
        import numpy as np
        model=self.module.CLIP
        if not model.ready():self.skipTest('CLIP models unavailable')
        for name,color in [('red.jpg','red'),('blue.jpg','blue')]:
            image=Image.new('RGB',(224,224),color)
            path=self.add_media(name,image);vector=model.image_vector(image)
            with self.module.db() as c:c.execute('INSERT INTO embeddings VALUES(?,?,?,?)',(name,vector.tobytes(),path.stat().st_mtime,path.stat().st_size))
        query=model.text_vector('a solid blue square')
        self.assertEqual(query.shape,(512,))
        self.assertAlmostEqual(float(np.linalg.norm(query)),1,places=5)
        result=self.req('get','/api/search?q=a+solid+blue+square').json
        self.assertTrue(result['semantic'])
        self.assertEqual(result['items'][0]['id'],'blue.jpg')

    def test_05_invalid_folder_preserves_settings(self):
        self.assertEqual(self.req('post','/api/settings',{'folders':['Z:/nonexistent-memorylane-test']}).status_code,400)
        self.assertEqual(self.module.folders(),[str(self.photos.resolve())])

    def test_06_remove_folder_preserves_originals(self):
        self.assertEqual(self.req('post','/api/settings',{'folders':[]}).status_code,202)
        self.wait_job()
        self.assertEqual(self.req('get','/api/media').json['items'],[])
        self.assertEqual(len(list(self.photos.glob('photo-*.jpg'))),65)

    def test_07_comma_and_newline_folder_input(self):
        other=self.root/'other, photos'
        other.mkdir()
        value=f'{self.photos}, "{other}"'
        self.assertEqual(self.req('post','/api/settings',{'folder_text':value}).status_code,202)
        self.wait_job()
        self.assertEqual(self.module.folders(),[str(self.photos),str(other)])
        self.assertEqual(self.req('post','/api/settings',{'folder_text':str(other)}).status_code,202)
        self.wait_job()
        self.assertEqual(self.module.folders(),[str(other)])

    def test_08_storage_and_html_identifiers(self):
        result=self.req('get','/api/storage').json
        self.assertEqual(result['location'],str((self.root/'index').resolve()))
        self.assertEqual(result['total_bytes'],sum(r['bytes'] for r in result['categories']))
        self.assertGreater(next(r for r in result['categories'] if r['key']=='thumbnails')['bytes'],0)
        from html.parser import HTMLParser
        class IDs(HTMLParser):
            ids=[]
            def handle_starttag(self, tag, attrs):
                self.ids.extend(v for k,v in attrs if k=='id')
        parser=IDs()
        parser.feed((self.module.BASE/'static/index.html').read_text(encoding='utf-8'))
        self.assertEqual(len(parser.ids),len(set(parser.ids)), 'Duplicate DOM IDs break selectors')

class FaceGroupingTests(unittest.TestCase):
    def test_visual_cache_updates_and_removes_stale_vectors(self):
        import sqlite3
        import numpy as np
        from search_cache import SearchCache
        c=sqlite3.connect(':memory:')
        c.executescript('CREATE TABLE media(id TEXT); CREATE TABLE embeddings(media_id TEXT PRIMARY KEY,vector BLOB,mtime REAL,size INTEGER); CREATE INDEX embedding_cache_meta ON embeddings(media_id,mtime,size);')
        a=np.zeros(512,dtype=np.float32);a[0]=1
        b=np.zeros(512,dtype=np.float32);b[1]=1
        for ident,v in [('a',a),('b',b)]:
            c.execute('INSERT INTO media VALUES(?)',(ident,))
            c.execute('INSERT INTO embeddings VALUES(?,?,1,1)',(ident,v.tobytes()))
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'vectors.npz'
            cache=SearchCache(path)
            self.assertEqual(cache.rank(c,a,['1=1'],[])[0][0],'a')
            cache=SearchCache(path)
            c.execute("DELETE FROM embeddings WHERE media_id='a'")
            c.execute("UPDATE embeddings SET vector=?,mtime=2 WHERE media_id='b'",(a.tobytes(),))
            self.assertEqual(cache.rank(c,a,['1=1'],[]),[('b',1.0)])
        c.close()

    def test_duplicate_consolidation_preserves_names_and_cooccurrence(self):
        import sqlite3
        import numpy as np
        from face_groups import consolidate
        c=sqlite3.connect(':memory:');c.row_factory=sqlite3.Row
        c.executescript('CREATE TABLE people(id INTEGER PRIMARY KEY,name TEXT,feature BLOB); CREATE TABLE media(id TEXT,kind TEXT); CREATE TABLE faces(id INTEGER PRIMARY KEY,person_id INTEGER,media_id TEXT,feature BLOB);')
        v=np.array([1.,0.,0.],dtype=np.float32).tobytes()
        for i,name in [(1,'Known'),(2,''),(3,'Different')]:
            c.execute('INSERT INTO people VALUES(?,?,?)',(i,name,v))
            c.execute('INSERT INTO media VALUES(?,?)',(str(i),'photo'))
            c.execute('INSERT INTO faces VALUES(?,?,?,?)',(i,i,str(i),v))
        self.assertEqual(consolidate(c),1)
        self.assertEqual([r[0] for r in c.execute('SELECT id FROM people ORDER BY id')],[1,3])
        self.assertEqual(c.execute('SELECT target FROM face_merge_history').fetchone()[0],1)
        self.assertEqual(consolidate(c),0) # Distinct human names are protected.
        c.execute("UPDATE people SET name='' WHERE id=3")
        c.execute("UPDATE faces SET media_id='1' WHERE person_id=3")
        self.assertEqual(consolidate(c),0) # Same-photo faces cannot be merged.
        c.close()

    def test_portrait_preserves_geometry(self):
        from face_groups import portrait
        import numpy as np
        # A circular feature remains circular, even inside a tall face box.
        from PIL import ImageDraw
        image=Image.new('RGB',(240,240),'black')
        ImageDraw.Draw(image).ellipse((95,95,145,145),fill='white')
        result=np.asarray(portrait(image,(85,65,70,110)))[:,:,0]>127
        y,x=np.where(result)
        self.assertLessEqual(abs((x.max()-x.min())-(y.max()-y.min())),1)

    def test_matching_uses_multiple_views_and_avoids_ambiguity(self):
        import numpy as np
        from face_groups import match
        front=np.array([1.,0.,0.],dtype=np.float32)
        side=np.array([.3,.953939,0.],dtype=np.float32)
        other=np.array([0.,0.,1.],dtype=np.float32)
        self.assertEqual(match(side,{1:[front,side,side],2:[other]}),1)
        self.assertIsNone(match(front,{1:[front],2:[front]}))
        self.assertIsNone(match(front,{1:[front]},excluded={1}))

if __name__=='__main__':
    unittest.main()
