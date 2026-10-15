"""Offline strategy knowledge workbench. Python 3.8+, standard library only."""
import argparse
import csv
import io
import errno
import hashlib
from http.cookies import SimpleCookie, CookieError
import time
import json
import sqlite3
import secrets
import threading
import webbrowser
from datetime import datetime
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse

BASE = Path(__file__).resolve().parent
DB = BASE / 'data' / 'knowledge.db'
TOKEN = secrets.token_urlsafe(32)

TAG_RULES = [
    ('组织绩效', ('绩效', '组织', '职责')),
    ('产业政策', ('政策', '金融', '申报')),
    ('市场分析', ('市场', '份额', '订单', '交付量')),
    ('国际合作', ('合作', '谈判', '成果归属')),
    ('服务运营', ('维修', '服务', '在役')),
    ('研发制造', ('研发', '制造', '小批产', '供应链')),
]

CSV_FIELDS = [
    ('资产编号', 'id'), ('标题', 'title'), ('正文', 'content'), ('来源', 'source'),
    ('业务领域', 'domain'), ('业务类型', 'type'), ('资产类型', 'kind'),
    ('审核状态', 'status'), ('日期', 'date'), ('负责人', 'owner'),
    ('标签', 'tag'), ('标签来源', 'tag_source'),
]

def csv_text(assets):
    out = io.StringIO(newline='')
    writer = csv.writer(out, lineterminator='\r\n')
    writer.writerow([label for label, key in CSV_FIELDS])
    for asset in assets:
        row = dict(enrich_asset(dict(asset)))
        row['tag_source'] = '离线规则建议' if row['tag_source'] == 'offline_rule' else '人工确认'
        writer.writerow([row.get(key, '') for label, key in CSV_FIELDS])
    return '\ufeff' + out.getvalue()


def parse_asset_csv(text, user, existing_ids=(), restore=False):
    if not isinstance(text, str): raise ValueError('请选择CSV文件')
    reader = csv.DictReader(io.StringIO(text.lstrip('\ufeff'), newline=''), strict=True)
    fields = reader.fieldnames
    if not fields or len(fields) != len(set(fields)):
        raise ValueError('CSV表头为空或重复，请使用下载的模板')
    allowed = {label for label, key in CSV_FIELDS}
    if set(fields) - allowed:
        raise ValueError('CSV有未知列：' + '、'.join(set(fields) - allowed))
    if not {'标题', '正文', '来源'}.issubset(fields):
        raise ValueError('CSV必须包含“标题、正文、来源”列')
    assets, used = [], set(existing_ids) if not restore else set()
    next_id = max(int(time.time()*1000), max(used, default=0)+1)
    defaults = {'domain':'产业与市场', 'type':'专题研究', 'kind':'段落素材',
                'status':'待审核', 'date':datetime.now().date().isoformat()}
    for number, row in enumerate(reader, 2):
        if not any(value for value in row.values()): continue
        if None in row or any(value is None for value in row.values()):
            raise ValueError('CSV第%d条记录列数不匹配，请用引号包住含逗号或换行的单元格' % number)
        asset = {key: row.get(label, '') for label, key in CSV_FIELDS}
        for key in ('title', 'content', 'source'):
            if not asset[key].strip(): raise ValueError('CSV第%d条记录缺少标题、正文或来源' % number)
        for key, value in defaults.items():
            asset[key] = asset[key].strip() or value
        try: datetime.strptime(asset['date'], '%Y-%m-%d')
        except ValueError: raise ValueError('CSV第%d条记录日期必须为YYYY-MM-DD' % number)
        if asset['status'] not in ('待审核', '已审核', '待更新'):
            raise ValueError('CSV第%d条记录审核状态应为待审核、已审核或待更新' % number)
        if asset['kind'] not in ('段落素材', '战略数据'):
            raise ValueError('CSV第%d条记录资产类型应为段落素材或战略数据' % number)
        if restore and asset['id'].strip():
            try: asset_id = int(asset['id'])
            except ValueError: raise ValueError('CSV第%d条记录资产编号错误' % number)
            if asset_id <= 0 or asset_id in used: raise ValueError('CSV资产编号必须为不同的正整数')
        else:
            while next_id in used: next_id += 1
            asset_id = next_id; next_id += 1
        used.add(asset_id);asset['id'] = asset_id
        asset['owner'] = asset['owner'].strip() or '待指定' if restore else user
        asset['tag'] = asset['tag'].strip()
        if len(asset['tag']) > 50: raise ValueError('CSV标签最多50字')
        source = asset['tag_source'].strip()
        if source not in ('', 'offline_rule', 'manual', '离线规则建议', '人工确认'):
            raise ValueError('CSV第%d条记录标签来源应为离线规则建议或人工确认' % number)
        asset['tag_source'] = 'offline_rule' if source in ('offline_rule', '离线规则建议') else 'manual'
        enrich_asset(asset)
        assets.append(asset)
    if not assets and not restore: raise ValueError('CSV中没有素材数据')
    validate({'assets': assets, 'templates': []})
    return assets


def suggest_tag(asset):
    title = asset.get('title', '')
    content = asset.get('content', '')
    scores = [(sum(3 * title.count(word) + content.count(word) for word in words), tag)
              for tag, words in TAG_RULES]
    score, tag = max(scores, key=lambda item: item[0])
    return tag if score else asset.get('domain', '专题研究') or '专题研究'

def enrich_asset(asset):
    asset.setdefault('owner', '待指定')
    if not asset.get('tag'):
        asset['tag'] = suggest_tag(asset)
        asset['tag_source'] = 'offline_rule'
    asset.setdefault('tag_source', 'manual')
    return asset


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
        CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS users(username TEXT PRIMARY KEY, display_name TEXT NOT NULL, salt TEXT NOT NULL, password_hash TEXT NOT NULL);''')
        if not c.execute("SELECT 1 FROM users WHERE username='admin'").fetchone():
            salt = secrets.token_hex(16)
            password_hash = hashlib.pbkdf2_hmac('sha256', b'admin', bytes.fromhex(salt), 200000).hex()
            c.execute('INSERT INTO users VALUES(?,?,?,?)', ('admin', 'admin', salt, password_hash))
        if not c.execute("SELECT 1 FROM meta WHERE key='initialized'").fetchone():
            seed_file = BASE / 'seed.json'
            if not seed_file.is_file(): seed_file = BASE / 'data' / 'seed.json'
            seed = json.loads(seed_file.read_text(encoding='utf-8'))
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
        for k in ('owner', 'tag'):
            if not isinstance(r.get(k), str) or not r[k].strip():
                raise ValueError('资产字段不完整: ' + k)
        if r.get('tag_source') not in ('offline_rule', 'manual'):
            raise ValueError('标签来源错误')
    for t in s['templates']:
        for k in ('name','domain','type','audience','length','outline'):
            if not isinstance(t.get(k),str): raise ValueError('模板字段不完整: '+k)
        if not isinstance(t.get('scenario', ''), str): raise ValueError('使用场景格式错误')
        tags = t.get('content_tags', [])
        if not isinstance(tags, list) or len(tags)>3 or any(not isinstance(tag, str) or not tag.strip() or len(tag)>50 for tag in tags):
            raise ValueError('主要内容最多3个非空标签，每个最多50字')
        if len(set(tags)) != len(tags): raise ValueError('主要内容标签不能重复')

class AppServer(ThreadingHTTPServer):
    def __init__(self, *args, **kwargs):
        self.sessions = {}
        self.session_lock = threading.Lock()
        super().__init__(*args, **kwargs)


class Handler(BaseHTTPRequestHandler):
    def allowed_host(self):
        return self.headers.get('Host') in ('127.0.0.1:'+str(self.server.server_port),'localhost:'+str(self.server.server_port))

    def cookie_name(self):
        return 'aero_session_' + str(self.server.server_port)

    def session_id(self):
        try:
            cookie = SimpleCookie(self.headers.get('Cookie', ''))
            return cookie[self.cookie_name()].value if self.cookie_name() in cookie else ''
        except CookieError:
            return ''

    def current_user(self):
        with self.server.session_lock:
            session = self.server.sessions.get(self.session_id())
            if session and session['expires'] > time.monotonic():
                return session['user']
        return None

    def reply(self, status, data, cookie=None):
        raw=json.dumps(data,ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Content-Length',str(len(raw)))
        self.send_header('Cache-Control','no-store')
        if cookie: self.send_header('Set-Cookie', cookie)
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if not self.allowed_host(): return self.reply(403,{'error':'仅允许本机访问'})
        path=urlparse(self.path).path
        if path in ('/', '/index.html', '/web/index.html'):
            index = BASE / 'index.html'
            if not index.is_file(): index = BASE / 'web' / 'index.html'
            raw = index.read_bytes()
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(raw)))
            self.send_header('Cache-Control', 'no-store, max-age=0')
            self.end_headers()
            self.wfile.write(raw)
            return
        if path in ('/api/backup.csv', '/api/csv-template'):
            if not self.current_user(): return self.reply(401, {'error':'请先登录'})
            if path == '/api/backup.csv':
                with connect() as c:
                    assets = [json.loads(row[0]) for row in c.execute('SELECT body FROM assets ORDER BY rowid')]
                text = csv_text(assets)
            else:
                text = '\ufeff' + ','.join(label for label, key in CSV_FIELDS) + '\r\n'
            raw = text.encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'text/csv; charset=utf-8')
            self.send_header('Content-Disposition', 'attachment; filename="aero-assets.csv"')
            self.send_header('Content-Length', str(len(raw)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers();self.wfile.write(raw);return
        if path in ('/api/state', '/api/session'):
            user = self.current_user()
            if not user: return self.reply(401, {'error': '请先登录'})
            if path == '/api/session': return self.reply(200, {'user': user, 'token': TOKEN})
            with connect() as c:
                s={'assets':[json.loads(r[0]) for r in c.execute('SELECT body FROM assets ORDER BY rowid')],
                   'templates':[json.loads(r[0]) for r in c.execute('SELECT body FROM templates ORDER BY id')]}
            for asset in s['assets']: enrich_asset(asset)
            for template in s['templates']:
                template.setdefault('scenario', '')
                template.setdefault('content_tags', [])
            s['token']=TOKEN
            return self.reply(200,s)
        return self.reply(404, {'error': '接口或文件不存在'})

    def do_POST(self):
        if not self.allowed_host(): return self.reply(403, {'error': '仅允许本机访问'})
        origin = self.headers.get('Origin')
        if origin and origin != 'http://' + self.headers.get('Host', ''):
            return self.reply(403, {'error': '请求来源无效'})
        path = urlparse(self.path).path
        if path not in ('/api/login', '/api/logout', '/api/state', '/api/tag', '/api/csv/preview', '/api/restore'):
            return self.reply(404, {'error': '接口不存在'})
        user = self.current_user()
        if path != '/api/login':
            if not user: return self.reply(401, {'error': '请先登录'})
            if self.headers.get('X-Local-Token') != TOKEN:
                return self.reply(403, {'error': '无效请求，请刷新页面'})
        try:
            n=int(self.headers.get('Content-Length','0'))
            if n<=0 or n>10*1024*1024: raise ValueError('数据大小超过限制')
            state=json.loads(self.rfile.read(n).decode('utf-8'))
            if path == '/api/login':
                if not isinstance(state, dict): raise ValueError('登录格式错误')
                username, password = state.get('username'), state.get('password')
                if not isinstance(username, str) or not isinstance(password, str) or len(username)>50 or len(password)>256:
                    return self.reply(401, {'error': '账号或密码错误'})
                with connect() as c:
                    row = c.execute('SELECT display_name,salt,password_hash FROM users WHERE username=?', (username,)).fetchone()
                salt = row[1] if row else '0' * 32
                digest = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), bytes.fromhex(salt), 200000).hex()
                if not row or not secrets.compare_digest(digest, row[2]):
                    return self.reply(401, {'error': '账号或密码错误'})
                sid = secrets.token_urlsafe(32)
                with self.server.session_lock:
                    now = time.monotonic()
                    self.server.sessions = {k:v for k,v in self.server.sessions.items() if v['expires'] > now}
                    self.server.sessions.pop(self.session_id(), None)
                    self.server.sessions[sid] = {'user': row[0], 'expires': now + 12*60*60}
                cookie = self.cookie_name() + '=' + sid + '; HttpOnly; SameSite=Strict; Path=/'
                return self.reply(200, {'user': row[0], 'token': TOKEN}, cookie)
            if path == '/api/logout':
                with self.server.session_lock: self.server.sessions.pop(self.session_id(), None)
                return self.reply(200, {'logged_out': True}, self.cookie_name() + '=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0')
            if path == '/api/csv/preview':
                if not isinstance(state, dict): raise ValueError('CSV格式错误')
                with connect() as c:
                    ids = [row[0] for row in c.execute('SELECT id FROM assets')]
                assets = parse_asset_csv(state.get('csv'), user, ids, restore=state.get('restore') is True)
                return self.reply(200, {'assets': assets, 'count': len(assets)})
            if urlparse(self.path).path == '/api/tag':
                if not isinstance(state, dict) or any(not isinstance(state.get(k), str) for k in ('title', 'content', 'domain')):
                    raise ValueError('请填写标题、内容和业务领域')
                return self.reply(200, {'tag': suggest_tag(state), 'tag_source': 'offline_rule'})
            if not isinstance(state, dict) or not isinstance(state.get('assets'), list):
                raise ValueError('资产格式错误')
            with connect() as c:
                old={'assets':[json.loads(r[0]) for r in c.execute('SELECT body FROM assets')],
                     'templates':[json.loads(r[0]) for r in c.execute('SELECT body FROM templates ORDER BY id')]}
                existing = {r['id']: r for r in old['assets']}
                for asset in state['assets']:
                    if not isinstance(asset, dict): raise ValueError('资产格式错误')
                    prior = existing.get(asset.get('id'))
                    if path == '/api/restore':
                        asset.setdefault('owner', '待指定')
                    elif prior and prior.get('owner') not in (None, '', '待指定'):
                        asset['owner'] = prior['owner']
                    elif prior:
                        asset['owner'] = user if asset.get('owner') == user else '待指定'
                    else:
                        asset['owner'] = user
                    enrich_asset(asset)
                validate(state)
                c.execute('INSERT INTO history(created,body) VALUES(?,?)',(datetime.now().isoformat(),json.dumps(old,ensure_ascii=False)))
                write_state(c,state)
            return self.reply(200,{'saved':True})
        except (ValueError,KeyError,TypeError,sqlite3.Error,csv.Error) as e:
            return self.reply(400,{'error':str(e)})

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--port', type=int, default=None)
    p.add_argument('--no-browser', action='store_true')
    a=p.parse_args()
    ports = [a.port] if a.port is not None else range(8000, 8011)
    server = None
    for port in ports:
        try:
            server = AppServer(('127.0.0.1', port), Handler)
            break
        except OSError as error:
            if error.errno not in (errno.EADDRINUSE, getattr(errno, 'WSAEADDRINUSE', 10048)) or a.port is not None:
                p.exit(1, 'Port unavailable. Close the old service or try --port 8001.\n')
    if server is None: p.exit(1, 'Ports 8000-8010 unavailable. Close the old service.\n')
    try:
        initialize()
    except Exception:
        server.server_close()
        raise
    url='http://127.0.0.1:'+str(server.server_port)+'/?login=1'
    print('Offline knowledge workbench v1.2: '+url+'\nKeep this window open. Ctrl+C to stop.\nDatabase: '+str(DB))
    if not a.no_browser: threading.Timer(.5,lambda:webbrowser.open(url)).start()
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close()
if __name__=='__main__':main()
