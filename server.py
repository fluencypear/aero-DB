"""Offline strategy knowledge workbench. Python 3.8+, standard library only."""
import argparse
import json
import sqlite3
import secrets
import threading
import webbrowser
from datetime import datetime
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse

BASE = Path(__file__).resolve().parent
DB = BASE / 'data' / 'knowledge.db'
TOKEN = secrets.token_urlsafe(32)

def connect():
    c = sqlite3.connect(str(DB), timeout=10)
    c.execute('PRAGMA foreign_keys=ON')
    return c

def initialize():
    DB.parent.mkdir(exist_ok=True)
    with connect() as c:
        c.executescript('''CREATE TABLE IF NOT EXISTS assets(id INTEGER PRIMARY KEY, body TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS templates(id INTEGER PRIMARY KEY, body TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS history(id INTEGER PRIMARY KEY, created TEXT NOT NULL, body TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);''')
        if not c.execute("SELECT 1 FROM meta WHERE key='initialized'").fetchone():
            seed = json.loads((BASE / 'data' / 'seed.json').read_text(encoding='utf-8'))
            write_state(c, seed)
            c.execute("INSERT INTO meta VALUES('initialized','1')")

def write_state(c, state):
    c.execute('DELETE FROM assets')
    c.execute('DELETE FROM templates')
    c.executemany('INSERT INTO assets VALUES(?,?)', [(r['id'], json.dumps(r, ensure_ascii=False)) for r in state['assets']])
    c.executemany('INSERT INTO templates VALUES(?,?)', [(i, json.dumps(t, ensure_ascii=False)) for i,t in enumerate(state['templates'])])

def validate(s):
    if not isinstance(s,dict) or not isinstance(s.get('assets'),list) or not isinstance(s.get('templates'),list):
        raise ValueError('资产或模板格式错误')
    ids = set()
    for r in s['assets']:
        if not isinstance(r,dict) or not isinstance(r.get('id'),int) or r['id'] in ids:
            raise ValueError('资产编号重复或错误')
        ids.add(r['id'])
        for k in ('title','content','source','domain','type','kind','status','date'):
            if not isinstance(r.get(k),str): raise ValueError('资产字段不完整: '+k)
    for t in s['templates']:
        for k in ('name','domain','type','audience','length','outline'):
            if not isinstance(t.get(k),str): raise ValueError('模板字段不完整: '+k)

class Handler(SimpleHTTPRequestHandler):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,directory=str(BASE/'web'),**kwargs)
    def allowed_host(self):
        return self.headers.get('Host') in ('127.0.0.1:'+str(self.server.server_port),'localhost:'+str(self.server.server_port))
    def reply(self, status, data):
        raw=json.dumps(data,ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Content-Length',str(len(raw)))
        self.send_header('Cache-Control','no-store')
        self.end_headers()
        self.wfile.write(raw)
    def do_GET(self):
        if not self.allowed_host(): return self.reply(403,{'error':'仅允许本机访问'})
        path=urlparse(self.path).path
        if path=='/api/state':
            with connect() as c:
                s={'assets':[json.loads(r[0]) for r in c.execute('SELECT body FROM assets ORDER BY rowid')],
                   'templates':[json.loads(r[0]) for r in c.execute('SELECT body FROM templates ORDER BY id')]}
            s['token']=TOKEN
            return self.reply(200,s)
        if path.startswith('/api/'): return self.reply(404,{'error':'接口不存在'})
        return super().do_GET()
    def do_POST(self):
        if not self.allowed_host() or self.headers.get('X-Local-Token')!=TOKEN:
            return self.reply(403,{'error':'无效请求，请刷新页面'})
        if urlparse(self.path).path!='/api/state':return self.reply(404,{'error':'接口不存在'})
        try:
            n=int(self.headers.get('Content-Length','0'))
            if n<=0 or n>10*1024*1024: raise ValueError('数据大小超过限制')
            state=json.loads(self.rfile.read(n).decode('utf-8'));validate(state)
            with connect() as c:
                old={'assets':[json.loads(r[0]) for r in c.execute('SELECT body FROM assets')],
                     'templates':[json.loads(r[0]) for r in c.execute('SELECT body FROM templates ORDER BY id')]}
                c.execute('INSERT INTO history(created,body) VALUES(?,?)',(datetime.now().isoformat(),json.dumps(old,ensure_ascii=False)))
                write_state(c,state)
            return self.reply(200,{'saved':True})
        except (ValueError,KeyError,TypeError,sqlite3.Error) as e:
            return self.reply(400,{'error':str(e)})

def main():
    p=argparse.ArgumentParser();p.add_argument('--port',type=int,default=8000);p.add_argument('--no-browser',action='store_true');a=p.parse_args()
    initialize()
    try: server=ThreadingHTTPServer(('127.0.0.1',a.port),Handler)
    except OSError:
        print('Port unavailable. Try: python server.py --port 8001');return
    url='http://127.0.0.1:'+str(server.server_port)
    print('Offline knowledge workbench: '+url+'\nKeep this window open. Ctrl+C to stop.\nDatabase: '+str(DB))
    if not a.no_browser: threading.Timer(.5,lambda:webbrowser.open(url)).start()
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close()
if __name__=='__main__':main()
