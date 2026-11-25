"""RamFreeMCHosting v2 platform services (development prototype).
User accounts, points, streaks, spins, cosmetics, referrals, tickets, user files and backups.
This module intentionally does not claim to isolate/control the legacy Minecraft process.
"""
import os, re, sqlite3, secrets, hashlib, hmac, time, json, zipfile, io, shutil
from datetime import datetime, date, timedelta
from functools import wraps
from flask import Blueprint, request, session, jsonify, render_template, send_file, abort

bp = Blueprint('platform_v2', __name__)
ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, 'platform_data')
DB = os.path.join(DATA, 'platform.sqlite3')
USER_FILES = os.path.join(DATA, 'users')
BACKUPS = os.path.join(DATA, 'backups')
MAX_USER_STORAGE = 512 * 1024 * 1024
MAX_UPLOAD = 25 * 1024 * 1024
os.makedirs(USER_FILES, exist_ok=True); os.makedirs(BACKUPS, exist_ok=True)


def connect():
    c=sqlite3.connect(DB, timeout=20); c.row_factory=sqlite3.Row
    c.execute('PRAGMA foreign_keys=ON'); return c

def init_db():
    with connect() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, username TEXT UNIQUE COLLATE NOCASE, email TEXT UNIQUE COLLATE NOCASE, password_hash TEXT, salt TEXT, created_at TEXT, membership TEXT DEFAULT 'Free', membership_source TEXT DEFAULT 'none', membership_until TEXT, ram_points INTEGER DEFAULT 0, spin_points INTEGER DEFAULT 0, gems INTEGER DEFAULT 0, streak INTEGER DEFAULT 0, last_claim TEXT, referral_code TEXT UNIQUE, referred_by INTEGER, leaderboard_optout INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS ledger(id INTEGER PRIMARY KEY, user_id INTEGER, currency TEXT, amount INTEGER, reason TEXT, created_at TEXT, FOREIGN KEY(user_id) REFERENCES users(id));
        CREATE TABLE IF NOT EXISTS spins(id INTEGER PRIMARY KEY, user_id INTEGER, reward TEXT, amount INTEGER, created_at TEXT, FOREIGN KEY(user_id) REFERENCES users(id));
        CREATE TABLE IF NOT EXISTS owned_cosmetics(id INTEGER PRIMARY KEY, user_id INTEGER, item_key TEXT, bought_at TEXT, UNIQUE(user_id,item_key), FOREIGN KEY(user_id) REFERENCES users(id));
        CREATE TABLE IF NOT EXISTS tickets(id INTEGER PRIMARY KEY, user_id INTEGER, subject TEXT, message TEXT, status TEXT DEFAULT 'Open', created_at TEXT, FOREIGN KEY(user_id) REFERENCES users(id));
        CREATE TABLE IF NOT EXISTS referrals(id INTEGER PRIMARY KEY, referrer_id INTEGER, new_user_id INTEGER UNIQUE, created_at TEXT, FOREIGN KEY(referrer_id) REFERENCES users(id), FOREIGN KEY(new_user_id) REFERENCES users(id));
        CREATE TABLE IF NOT EXISTS announcements(id INTEGER PRIMARY KEY, title TEXT, body TEXT, created_at TEXT);
        ''')
        if c.execute('SELECT COUNT(*) FROM announcements').fetchone()[0] == 0:
            c.execute('INSERT INTO announcements(title,body,created_at) VALUES(?,?,?)',('Welcome to RamFreeMCHosting v2','Platform rewards and account tools are in development. Paid memberships are not available yet.',datetime.utcnow().isoformat()))

init_db()

def now(): return datetime.utcnow().isoformat(timespec='seconds')
def today(): return date.today().isoformat()
def pw_hash(password, salt): return hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), 240000).hex()
def user_dir(uid):
    p=os.path.abspath(os.path.join(USER_FILES,str(uid)))
    if os.path.commonpath([os.path.abspath(USER_FILES),p]) != os.path.abspath(USER_FILES): raise ValueError('Invalid user path')
    os.makedirs(p,exist_ok=True); return p
def require_user(fn):
    @wraps(fn)
    def inner(*a,**kw):
        uid=session.get('v2_uid')
        if not uid: return jsonify(ok=False,message='Please login first.'),401
        with connect() as c: u=c.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone()
        if not u: session.pop('v2_uid',None); return jsonify(ok=False,message='Session expired.'),401
        if u['membership_until']:
            try:
                if date.fromisoformat(u['membership_until']) < date.today() and u['membership'] != 'Free':
                    with connect() as c:
                        c.execute("UPDATE users SET membership='Free',membership_source='expired',membership_until=NULL WHERE id=?",(uid,))
                    with connect() as c: u=c.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone()
            except ValueError:
                pass
        return fn(u,*a,**kw)
    return inner
def add_points(c,uid,currency,amount,reason):
    col={'ram':'ram_points','spin':'spin_points','gems':'gems'}[currency]
    c.execute(f'UPDATE users SET {col}=MAX(0,{col}+?) WHERE id=?',(amount,uid))
    c.execute('INSERT INTO ledger(user_id,currency,amount,reason,created_at) VALUES(?,?,?,?,?)',(uid,currency,amount,reason,now()))

def user_payload(u):
    return {'id':u['id'],'username':u['username'],'email':u['email'],'membership':u['membership'],'membership_source':u['membership_source'],'membership_until':u['membership_until'],'ram_points':u['ram_points'],'spin_points':u['spin_points'],'gems':u['gems'],'streak':u['streak'],'referral_code':u['referral_code'],'leaderboard_optout':bool(u['leaderboard_optout'])}

@bp.get('/platform')
def platform_page(): return render_template('platform.html')

@bp.post('/api/v2/register')
def register():
    d=request.get_json(silent=True) or {}; username=str(d.get('username','')).strip(); email=str(d.get('email','')).strip().lower(); password=str(d.get('password',''))
    if not re.fullmatch(r'[A-Za-z0-9_]{3,20}',username): return jsonify(ok=False,message='Username must be 3-20 letters, numbers or underscore.'),400
    if not re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+',email): return jsonify(ok=False,message='Enter a valid email.'),400
    if len(password)<10 or len(password)>128: return jsonify(ok=False,message='Password must be 10-128 characters.'),400
    salt=secrets.token_hex(16); code=secrets.token_urlsafe(6).replace('-','').replace('_','')[:10].upper(); ref=str(d.get('referral_code','')).strip().upper()
    try:
        with connect() as c:
            parent=c.execute('SELECT id FROM users WHERE referral_code=?',(ref,)).fetchone() if ref else None
            cur=c.execute('INSERT INTO users(username,email,password_hash,salt,created_at,referral_code,referred_by) VALUES(?,?,?,?,?,?,?)',(username,email,pw_hash(password,salt),salt,now(),code,parent['id'] if parent else None))
            uid=cur.lastrowid
            if parent: c.execute('INSERT INTO referrals(referrer_id,new_user_id,created_at) VALUES(?,?,?)',(parent['id'],uid,now()))
        session['v2_uid']=uid; user_dir(uid)
        return jsonify(ok=True,user={'username':username,'membership':'Free'},message='Account created. Referral rewards are pending verification.')
    except sqlite3.IntegrityError: return jsonify(ok=False,message='Username or email already exists.'),409

@bp.post('/api/v2/login')
def login():
    d=request.get_json(silent=True) or {}; ident=str(d.get('identity','')).strip(); password=str(d.get('password',''))
    with connect() as c: u=c.execute('SELECT * FROM users WHERE username=? COLLATE NOCASE OR email=? COLLATE NOCASE',(ident,ident)).fetchone()
    if not u or not hmac.compare_digest(pw_hash(password,u['salt']),u['password_hash']): return jsonify(ok=False,message='Invalid login details.'),401
    session.clear(); session['v2_uid']=u['id']; session['csrf_token']=secrets.token_urlsafe(32); return jsonify(ok=True,user=user_payload(u),csrf_token=session['csrf_token'])

@bp.post('/api/v2/logout')
def logout(): session.pop('v2_uid',None); return jsonify(ok=True)

@bp.get('/api/v2/me')
@require_user
def me(u): return jsonify(ok=True,user=user_payload(u))

@bp.post('/api/v2/leaderboard-preference')
@require_user
def leaderboard_preference(u):
    val=1 if (request.get_json(silent=True) or {}).get('optout') else 0
    with connect() as c: c.execute('UPDATE users SET leaderboard_optout=? WHERE id=?',(val,u['id']))
    return jsonify(ok=True)

@bp.get('/api/v2/leaderboards')
def leaderboards():
    with connect() as c:
        ram=c.execute('SELECT username,ram_points AS score FROM users WHERE leaderboard_optout=0 ORDER BY ram_points DESC LIMIT 50').fetchall()
        spin=c.execute('SELECT username,spin_points AS score FROM users WHERE leaderboard_optout=0 ORDER BY spin_points DESC LIMIT 50').fetchall()
        vip=c.execute("SELECT username,membership,membership_source FROM users WHERE leaderboard_optout=0 AND membership IN ('Premium','Ultimate','Special Guest') ORDER BY CASE membership WHEN 'Ultimate' THEN 0 WHEN 'Premium' THEN 1 ELSE 2 END LIMIT 50").fetchall()
    return jsonify(ok=True,ram=[dict(x) for x in ram],spin=[dict(x) for x in spin],vip=[dict(x) for x in vip])

@bp.post('/api/v2/daily-claim')
@require_user
def daily_claim(u):
    t=today()
    if u['last_claim']==t: return jsonify(ok=False,message='Daily reward already claimed today.'),409
    yesterday=(date.today()-timedelta(days=1)).isoformat(); streak=(u['streak']+1) if u['last_claim']==yesterday else 1
    with connect() as c:
        c.execute('UPDATE users SET streak=?,last_claim=? WHERE id=?',(streak,t,u['id']))
        add_points(c,u['id'],'ram',5,'Daily login reward')
        if streak==7: add_points(c,u['id'],'gems',25,'7-day streak milestone')
        if streak==30: add_points(c,u['id'],'gems',100,'30-day streak milestone')
        if streak==99:
            c.execute("UPDATE users SET membership='Premium',membership_source='99-day reward',membership_until=? WHERE id=?",((date.today()+timedelta(days=30)).isoformat(),u['id']))
            add_points(c,u['id'],'gems',200,'99-day streak reward')
    return jsonify(ok=True,streak=streak,message='Daily reward claimed. Day 99 grants 30 days Premium + 200 Lucky Gems.')

@bp.post('/api/v2/spin')
@require_user
def spin(u):
    # Transparent development reward: one daily spin for Free, five for Premium, fifteen for Ultimate.
    limits={'Free':1,'Premium':5,'Ultimate':15,'Special Guest':1}; limit=limits.get(u['membership'],1)
    with connect() as c:
        used=c.execute('SELECT COUNT(*) FROM spins WHERE user_id=? AND date(created_at)=date(?)',(u['id'],today())).fetchone()[0]
        if used>=limit: return jsonify(ok=False,message=f'Daily spin limit reached ({limit}).'),409
        # Equal, disclosed odds across three Spin Point outcomes; no cash/paid spin.
        reward=secrets.choice([10,20,50]); add_points(c,u['id'],'spin',reward,'Daily spin reward')
        c.execute('INSERT INTO spins(user_id,reward,amount,created_at) VALUES(?,?,?,?)',(u['id'],'Spin Points',reward,now()))
    return jsonify(ok=True,reward=f'{reward} Spin Points',amount=reward,spins_left=limit-used-1,odds='10, 20 or 50 Spin Points: 1/3 each.')

@bp.get('/api/v2/spin-history')
@require_user
def spin_history(u):
    with connect() as c: rows=c.execute('SELECT reward,amount,created_at FROM spins WHERE user_id=? ORDER BY id DESC LIMIT 20',(u['id'],)).fetchall()
    return jsonify(ok=True,items=[dict(x) for x in rows])

@bp.get('/api/v2/membership')
def membership():
    return jsonify(ok=True,plans=[{'name':'Free','price':'₹0','status':'Available','daily_spins':1},{'name':'Premium','price':'₹99','status':'Coming Soon','daily_spins':5},{'name':'Ultimate','price':'₹299','status':'Coming Soon','daily_spins':15}],payments_enabled=False)

@bp.get('/api/v2/cosmetics')
@require_user
def cosmetics(u):
    catalog=[{'key':'neon_badge','name':'Neon Badge','currency':'gems','price':150},{'key':'profile_glow','name':'Profile Glow','currency':'gems','price':250},{'key':'animated_frame','name':'Animated Frame','currency':'gems','price':350}]
    with connect() as c: owned={r['item_key'] for r in c.execute('SELECT item_key FROM owned_cosmetics WHERE user_id=?',(u['id'],))}
    return jsonify(ok=True,items=[dict(x,owned=x['key'] in owned) for x in catalog])

@bp.post('/api/v2/cosmetics/buy')
@require_user
def buy_cosmetic(u):
    key=str((request.get_json(silent=True) or {}).get('key',''))
    prices={'neon_badge':150,'profile_glow':250,'animated_frame':350}
    if key not in prices: return jsonify(ok=False,message='Unknown cosmetic.'),404
    with connect() as c:
        if c.execute('SELECT 1 FROM owned_cosmetics WHERE user_id=? AND item_key=?',(u['id'],key)).fetchone(): return jsonify(ok=False,message='Already owned.'),409
        fresh=c.execute('SELECT gems FROM users WHERE id=?',(u['id'],)).fetchone()['gems']
        if fresh<prices[key]: return jsonify(ok=False,message='Not enough Lucky Gems.'),400
        add_points(c,u['id'],'gems',-prices[key],'Cosmetic purchase: '+key)
        c.execute('INSERT INTO owned_cosmetics(user_id,item_key,bought_at) VALUES(?,?,?)',(u['id'],key,now()))
    return jsonify(ok=True,message='Cosmetic permanently added to your account.')

@bp.post('/api/v2/tickets')
@require_user
def create_ticket(u):
    d=request.get_json(silent=True) or {}; subject=str(d.get('subject','')).strip()[:100]; message=str(d.get('message','')).strip()[:3000]
    if len(subject)<4 or len(message)<10: return jsonify(ok=False,message='Subject (4+) and message (10+) required.'),400
    with connect() as c: cur=c.execute('INSERT INTO tickets(user_id,subject,message,created_at) VALUES(?,?,?,?)',(u['id'],subject,message,now()))
    return jsonify(ok=True,ticket_id=cur.lastrowid)

@bp.get('/api/v2/tickets')
@require_user
def list_tickets(u):
    with connect() as c: rows=c.execute('SELECT id,subject,message,status,created_at FROM tickets WHERE user_id=? ORDER BY id DESC',(u['id'],)).fetchall()
    return jsonify(ok=True,tickets=[dict(x) for x in rows])

@bp.get('/api/v2/announcements')
def announcements():
    with connect() as c: rows=c.execute('SELECT id,title,body,created_at FROM announcements ORDER BY id DESC LIMIT 30').fetchall()
    return jsonify(ok=True,announcements=[dict(x) for x in rows])

@bp.get('/api/v2/referrals')
@require_user
def referrals(u):
    with connect() as c: count=c.execute('SELECT COUNT(*) FROM referrals WHERE referrer_id=?',(u['id'],)).fetchone()[0]
    return jsonify(ok=True,code=u['referral_code'],successful_signups=count,reward_status='Rewards are held until referral verification is implemented.')

@bp.get('/api/v2/files')
@require_user
def list_files(u):
    root=user_dir(u['id']); items=[]
    for base,dirs,files in os.walk(root):
        dirs[:]=[d for d in dirs if not d.startswith('.')]
        for name in files:
            p=os.path.join(base,name); rel=os.path.relpath(p,root).replace('\\','/')
            items.append({'name':rel,'size':os.path.getsize(p)})
    return jsonify(ok=True,files=items,limit_bytes=MAX_USER_STORAGE)

@bp.post('/api/v2/files/upload')
@require_user
def upload_file(u):
    f=request.files.get('file'); rel=str(request.form.get('path','')).replace('\\','/').strip('/')
    if not f or not rel or '..' in rel.split('/') or rel.startswith('/'): return jsonify(ok=False,message='Invalid file/path.'),400
    if not re.fullmatch(r'[A-Za-z0-9._/-]{1,180}',rel): return jsonify(ok=False,message='Filename contains unsupported characters.'),400
    root=user_dir(u['id']); dest=os.path.abspath(os.path.join(root,rel))
    if os.path.commonpath([root,dest])!=root: return jsonify(ok=False,message='Invalid path.'),400
    os.makedirs(os.path.dirname(dest),exist_ok=True)
    if os.path.exists(dest): return jsonify(ok=False,message='File already exists; delete it first.'),409
    total=sum(os.path.getsize(os.path.join(b,n)) for b,_,fs in os.walk(root) for n in fs)
    if request.content_length and request.content_length>MAX_UPLOAD+65536: return jsonify(ok=False,message='Upload exceeds 25 MB.'),413
    data=f.read(MAX_UPLOAD+1)
    if len(data)>MAX_UPLOAD or total+len(data)>MAX_USER_STORAGE: return jsonify(ok=False,message='Upload or account storage limit exceeded.'),413
    with open(dest,'xb') as out: out.write(data)
    return jsonify(ok=True,message='Uploaded.')

@bp.get('/api/v2/files/download')
@require_user
def download_file(u):
    rel=str(request.args.get('path','')).replace('\\','/')
    if not rel or '..' in rel.split('/') or rel.startswith('/'): abort(400)
    root=user_dir(u['id']); path=os.path.abspath(os.path.join(root,rel))
    if os.path.commonpath([root,path])!=root or not os.path.isfile(path): abort(404)
    return send_file(path,as_attachment=True,download_name=os.path.basename(path))

@bp.delete('/api/v2/files')
@require_user
def delete_file(u):
    rel=str((request.get_json(silent=True) or {}).get('path','')).replace('\\','/')
    if not rel or '..' in rel.split('/') or rel.startswith('/'): return jsonify(ok=False,message='Invalid path.'),400
    root=user_dir(u['id']); path=os.path.abspath(os.path.join(root,rel))
    if os.path.commonpath([root,path])!=root or not os.path.isfile(path): return jsonify(ok=False,message='File not found.'),404
    os.remove(path); return jsonify(ok=True)

@bp.post('/api/v2/backups/create')
@require_user
def create_backup(u):
    root=user_dir(u['id']); buf=io.BytesIO(); stamp=datetime.utcnow().strftime('%Y%m%d_%H%M%S')
    with zipfile.ZipFile(buf,'w',zipfile.ZIP_DEFLATED) as z:
        for base,_,files in os.walk(root):
            for n in files:
                p=os.path.join(base,n); z.write(p,os.path.relpath(p,root))
    raw=buf.getvalue()
    if len(raw)>MAX_USER_STORAGE: return jsonify(ok=False,message='Backup too large.'),413
    folder=os.path.join(BACKUPS,str(u['id'])); os.makedirs(folder,exist_ok=True); path=os.path.join(folder,f'account_{stamp}.zip')
    with open(path,'wb') as f: f.write(raw)
    return jsonify(ok=True,backup=os.path.basename(path),size=len(raw),message='Account-file backup created. Minecraft world backups require the isolated server engine.')

@bp.get('/api/v2/backups')
@require_user
def list_backups(u):
    folder=os.path.join(BACKUPS,str(u['id'])); os.makedirs(folder,exist_ok=True)
    return jsonify(ok=True,backups=[{'name':n,'size':os.path.getsize(os.path.join(folder,n))} for n in sorted(os.listdir(folder),reverse=True) if n.endswith('.zip')])

@bp.get('/api/v2/backups/download')
@require_user
def get_backup(u):
    name=os.path.basename(request.args.get('name','')); folder=os.path.join(BACKUPS,str(u['id'])); path=os.path.join(folder,name)
    if not name.endswith('.zip') or not os.path.isfile(path): abort(404)
    return send_file(path,as_attachment=True)

@bp.get('/api/v2/health')
def health(): return jsonify(ok=True,build='v2-development',database='sqlite',payments_enabled=False,admin_dashboard=False)
