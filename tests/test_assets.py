import copy
import csv
import hashlib
from http.cookiejar import CookieJar
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
import server


class AssetWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.original_db = server.DB
        server.DB = Path(self.temp.name) / 'knowledge.db'
        # A legacy database without users must retain assets while adding login support.
        with server.connect() as connection:
            connection.executescript('CREATE TABLE assets(id INTEGER PRIMARY KEY, body TEXT); CREATE TABLE templates(id INTEGER PRIMARY KEY, body TEXT); CREATE TABLE history(id INTEGER PRIMARY KEY, created TEXT, body TEXT); CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT);')
            seed = server.BASE / 'seed.json'
            if not seed.exists(): seed = server.BASE / 'data' / 'seed.json'
            self.seed = json.loads(seed.read_text())
            server.write_state(connection, self.seed)
            connection.execute("INSERT INTO meta VALUES('initialized','1')")
        server.initialize()
        self.http = server.AppServer(('127.0.0.1', 0), server.Handler)
        self.worker = threading.Thread(target=self.http.serve_forever)
        self.worker.start()
        self.client = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))
        self.request('/api/login', {'username':'admin', 'password':'admin'})

    def tearDown(self):
        self.http.shutdown();self.http.server_close();self.worker.join()
        server.DB = self.original_db
        self.temp.cleanup()

    def request(self, path, state=None, client=None, raw=False, extra_headers=None):
        headers = {'Content-Type':'application/json', 'X-Local-Token':server.TOKEN}
        headers.update(extra_headers or {})
        data = None if state is None else json.dumps(state).encode()
        url = 'http://127.0.0.1:' + str(self.http.server_port) + path
        with (client or self.client).open(urllib.request.Request(url, data=data, headers=headers)) as response:
            return response.read() if raw else json.load(response)

    def test_login_failure_authentication_and_logout(self):
        anonymous = urllib.request.build_opener()
        for path, state in [('/api/state', None), ('/api/backup.csv', None), ('/api/tag', {'title':'a','content':'b','domain':'c'}), ('/api/state', self.seed)]:
            with self.assertRaises(urllib.error.HTTPError) as error:
                self.request(path,state,client=anonymous,extra_headers={'X-Demo-User':'admin'.encode().hex()})
            self.assertEqual(error.exception.code,401)
        for username,password in [('admin','wrong'),('wrong','admin')]:
            with self.assertRaises(urllib.error.HTTPError) as error:
                self.request('/api/login',{'username':username,'password':password},client=anonymous)
            self.assertEqual(error.exception.code,401)
        self.assertEqual(self.request('/api/session')['user'],'admin')
        self.request('/api/logout',{})
        with self.assertRaises(urllib.error.HTTPError) as error:self.request('/api/state')
        self.assertEqual(error.exception.code,401)

    def test_owner_tag_and_legacy_data(self):
        state = self.request('/api/state')
        self.assertEqual(len(state['assets']),len(self.seed['assets']))
        self.assertTrue(all(r['owner']=='待指定' and r['tag'] for r in state['assets']))
        new = copy.deepcopy(state['assets'][0]);new.update(id=100,owner='伪造负责人',tag='',title='组织绩效',content='职责与绩效')
        state['assets'].append(new);self.request('/api/state',state)
        saved = self.request('/api/state');asset = next(r for r in saved['assets'] if r['id']==100)
        self.assertEqual((asset['owner'],asset['tag']),('admin','组织绩效'))
        self.assertEqual(saved['assets'][0]['owner'],'待指定')
        asset.update(owner='李四',tag='人工标签',tag_source='manual')
        self.request('/api/state',saved)
        actual = next(r for r in self.request('/api/state')['assets'] if r['id']==100)
        self.assertEqual((actual['owner'],actual['tag'],actual['tag_source']),('admin','人工标签','manual'))
        with server.connect() as connection:
            self.assertEqual(connection.execute('SELECT count(*) FROM history').fetchone()[0],2)
            self.assertNotEqual(connection.execute("SELECT password_hash FROM users WHERE username='admin'").fetchone()[0],'admin')

    def test_template_metadata_and_tag_limit(self):
        state = self.request('/api/state')
        template=state['templates'][0]
        template.update(audience='公司领导',scenario='季度汇报',content_tags=['市场','财务','组织'])
        self.request('/api/state',state)
        self.assertEqual(self.request('/api/state')['templates'][0]['content_tags'],['市场','财务','组织'])
        for tags in [['a','b','c','d'],['a','a'],['']]:
            template['content_tags']=tags
            with self.assertRaises(urllib.error.HTTPError) as error:self.request('/api/state',state)
            self.assertEqual(error.exception.code,400)

    def test_csv_preview_and_backup_roundtrip(self):
        original=self.request('/api/state')
        original['assets'][0].update(content='首行，含逗号\n第二行包含"引号"',owner='admin')
        self.request('/api/state',original)
        raw=self.request('/api/backup.csv',raw=True)
        self.assertTrue(raw.startswith(b'\xef\xbb\xbf'))
        text=raw.decode('utf-8-sig')
        restored=self.request('/api/csv/preview',{'csv':text,'restore':True})['assets']
        self.assertEqual(restored,self.request('/api/state')['assets'])
        self.request('/api/restore',{'assets':restored,'templates':original['templates']})
        self.assertEqual(self.request('/api/state')['assets'],restored)
        self.assertEqual(len(self.request('/api/csv/preview',{'csv':'标题,正文,来源\r\n组织绩效,组织与绩效,内部资料\r\n'})['assets']),1)
        self.assertEqual(len(self.request('/api/state')['assets']),len(original['assets']))
        template=self.request('/api/csv-template',raw=True).decode('utf-8-sig')
        self.assertIn('标题,正文,来源',template)

    def test_csv_invalid_rows_are_atomic(self):
        original=self.request('/api/state')['assets']
        for text in ['标题,正文\n示例,内容', '标题,正文,来源\n示例,内容,来源\n缺少正文,,来源', '标题,标题,正文,来源\na,b,c,d', '标题,正文,来源,日期\na,b,c,not-a-date', '标题,正文,来源\n"unterminated,b,c']:
            with self.assertRaises(urllib.error.HTTPError) as error:self.request('/api/csv/preview',{'csv':text})
            self.assertEqual(error.exception.code,400)
        self.assertEqual(self.request('/api/state')['assets'],original)

    def test_static_entry_and_local_file_restrictions(self):
        page=self.request('/',client=urllib.request.build_opener(),raw=True).decode()
        self.assertIn('id="login-password"',page)
        self.assertIn('默认账号：admin',page)
        self.assertEqual(self.request('/web/index.html',raw=True),page.encode())
        with self.assertRaises(urllib.error.HTTPError) as error:self.request('/data/knowledge.db',raw=True)
        self.assertEqual(error.exception.code,404)


if __name__=='__main__':unittest.main()
