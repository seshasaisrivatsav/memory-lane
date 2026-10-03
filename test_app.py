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

    def wait_job(self):
        deadline=time.monotonic()+20
        while self.module.JOB['running'] and time.monotonic()<deadline:
            time.sleep(.02)
        self.assertFalse(self.module.JOB['running'])
        self.assertEqual(self.module.JOB['errors'], [])

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

    def test_04_face_models_and_empty_detection(self):
        self.assertEqual(self.req('post','/api/faces',{}).status_code,202)
        self.wait_job()
        self.assertEqual(self.req('get','/api/people').json,[])
        with self.module.db() as c:
            self.assertEqual(c.execute('SELECT count(*) FROM media WHERE face_done=1').fetchone()[0],65)

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

if __name__=='__main__':
    unittest.main()
