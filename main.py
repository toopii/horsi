import os, json, time, math, sqlite3, threading, subprocess, secrets, hashlib, base64
from pathlib import Path
from datetime import datetime, timezone
from urllib.parse import urlencode
from zoneinfo import ZoneInfo
import requests
from cryptography.fernet import Fernet
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse
from itsdangerous import URLSafeTimedSerializer, BadSignature

ROOT=Path(__file__).resolve().parent
DATA=Path(os.getenv('DATA_DIR','/data')); DATA.mkdir(parents=True,exist_ok=True)
(DATA/'videos').mkdir(exist_ok=True)
DB=str(DATA/'autotube.sqlite')
SECRET=os.getenv('APP_SECRET','')
ADMIN=os.getenv('ADMIN_PASSWORD','')
if len(SECRET)<32 or len(ADMIN)<12: raise RuntimeError('Set APP_SECRET (32+ chars) and ADMIN_PASSWORD (12+ chars)')
FERNET=Fernet(base64.urlsafe_b64encode(hashlib.sha256(SECRET.encode()).digest()))
SIGN=URLSafeTimedSerializer(SECRET)
app=FastAPI(title='AutoTube AI')

def db():
 c=sqlite3.connect(DB,timeout=30); c.row_factory=sqlite3.Row; return c

def init():
 with db() as c:
  c.execute('CREATE TABLE IF NOT EXISTS settings (name TEXT PRIMARY KEY,value TEXT NOT NULL)')
  c.execute('CREATE TABLE IF NOT EXISTS api_keys (id INTEGER PRIMARY KEY AUTOINCREMENT,provider TEXT NOT NULL,secret TEXT NOT NULL,disabled INTEGER DEFAULT 0,fail_until REAL DEFAULT 0)')
  c.execute('''CREATE TABLE IF NOT EXISTS jobs (id INTEGER PRIMARY KEY AUTOINCREMENT, topic TEXT,style TEXT,language TEXT,seconds INTEGER,mode TEXT,scheduled TEXT,timezone TEXT, status TEXT,progress TEXT, video TEXT, title TEXT,description TEXT, youtube_id TEXT,error TEXT,created TEXT,started TEXT)''')
  c.execute('CREATE TABLE IF NOT EXISTS oauth (name TEXT PRIMARY KEY, encrypted TEXT)')
init()

def cfg(k,default=''):
 with db() as c:
  row=c.execute('SELECT value FROM settings WHERE name=?',(k,)).fetchone()
 return row['value'] if row else default

def require(req):
 try: return SIGN.loads(req.cookies.get('session',''),max_age=604800)== 'admin'
 except (BadSignature,Exception): return False

def auth(req):
 if not require(req): raise HTTPException(401,'Login required')

def csrf(req):
 auth(req)
 if req.headers.get('origin') and req.headers['origin'].rstrip('/')!=os.getenv('PUBLIC_URL','').rstrip('/'): raise HTTPException(403,'Invalid origin')

def enc(s): return FERNET.encrypt(s.encode()).decode()
def dec(s): return FERNET.decrypt(s.encode()).decode()

@app.get('/')
def home(): return FileResponse(ROOT/'index.html')
@app.post('/api/login')
async def login(req:Request):
 body=await req.json()
 if not secrets.compare_digest(str(body.get('password','')),ADMIN): raise HTTPException(401,'Wrong password')
 res=RedirectResponse('/',status_code=303)
 res.set_cookie('session',SIGN.dumps('admin'),httponly=True,secure=os.getenv('PUBLIC_URL','').startswith('https:'),samesite='lax',max_age=604800)
 return res
@app.post('/api/logout')
def logout(req:Request):
 csrf(req); r=RedirectResponse('/',status_code=303);r.delete_cookie('session');return r
@app.get('/api/state')
def state(req:Request):
 if not require(req): return {'authenticated':False}
 with db() as c:
  keys=[dict(id=x['id'],provider=x['provider'],disabled=bool(x['disabled']),cooldown=x['fail_until']>time.time()) for x in c.execute('SELECT id,provider,disabled,fail_until FROM api_keys')]
  jobs=[dict(x) for x in c.execute('SELECT * FROM jobs ORDER BY id DESC LIMIT 100')]
  connected=bool(c.execute("SELECT 1 FROM oauth WHERE name='google'").fetchone())
 return {'authenticated':True,'keys':keys,'jobs':jobs,'connected':connected,'settings':{'style':cfg('style','سینمایی'),'language':cfg('language','فارسی')}}
@app.post('/api/settings')
async def settings(req:Request):
 csrf(req);b=await req.json()
 with db() as c:
  for k in ('style','language'):
   if k in b: c.execute('INSERT OR REPLACE INTO settings VALUES (?,?)',(k,str(b[k])[:120]))
 return {'ok':True}
@app.post('/api/keys')
async def addkey(req:Request):
 csrf(req);b=await req.json();provider=b.get('provider');secret=str(b.get('key','')).strip()
 if provider not in ('deapi','pollinations') or len(secret)<8:raise HTTPException(400,'Invalid key')
 with db() as c: c.execute('INSERT INTO api_keys(provider,secret) VALUES (?,?)',(provider,enc(secret)))
 return {'ok':True}
@app.delete('/api/keys/{keyid}')
def delkey(keyid:int,req:Request):
 csrf(req)
 with db() as c:c.execute('DELETE FROM api_keys WHERE id=?',(keyid,))
 return {'ok':True}
@app.post('/api/jobs')
async def create(req:Request):
 csrf(req);b=await req.json();topic=str(b.get('topic','')).strip();seconds=int(b.get('seconds',0));kind=b.get('kind');mode=b.get('mode');scheduled=b.get('scheduled') or '';tz=b.get('timezone') or 'Asia/Tehran'
 if not topic or len(topic)>1000 or kind not in ('short','long') or not ((10<=seconds<=20) if kind=='short' else (30<=seconds<=40)) or mode not in ('review','auto'):raise HTTPException(400,'Invalid job configuration')
 try:
  ZoneInfo(tz)
  if scheduled: datetime.fromisoformat(scheduled)
 except Exception:raise HTTPException(400,'Invalid timezone or date')
 with db() as c:
  cur=c.execute('INSERT INTO jobs(topic,style,language,seconds,mode,scheduled,timezone,status,progress,created) VALUES (?,?,?,?,?,?,?,?,?,?)',(topic,cfg('style','سینمایی'),cfg('language','فارسی'),seconds,mode,scheduled,tz,'queued','در صف',datetime.now(timezone.utc).isoformat()))
 return {'id':cur.lastrowid}
@app.post('/api/jobs/{jid}/approve')
def approve(jid:int,req:Request):
 csrf(req)
 with db() as c:
  row=c.execute('SELECT status FROM jobs WHERE id=?',(jid,)).fetchone()
  if not row or row['status']!='review':raise HTTPException(409,'Job not awaiting approval')
  c.execute("UPDATE jobs SET status='upload',progress='در صف انتشار' WHERE id=?",(jid,))
 return {'ok':True}
@app.post('/api/jobs/{jid}/retry')
def retry(jid:int,req:Request):
 csrf(req)
 with db() as c:
  row=c.execute('SELECT status FROM jobs WHERE id=?',(jid,)).fetchone()
  if not row or row['status']!='failed':raise HTTPException(409,'Only failed jobs can retry')
  c.execute("UPDATE jobs SET status='queued',error=NULL,progress='در صف مجدد' WHERE id=?",(jid,))
 return {'ok':True}
@app.get('/api/videos/{jid}')
def video(jid:int,req:Request):
 auth(req)
 with db() as c: row=c.execute('SELECT video FROM jobs WHERE id=?',(jid,)).fetchone()
 if not row or not row['video']:raise HTTPException(404)
 path=DATA/'videos'/row['video']
 if not path.is_file():raise HTTPException(404)
 return FileResponse(path,media_type='video/mp4')

GOOGLE_AUTH='https://accounts.google.com/o/oauth2/v2/auth'
GOOGLE_TOKEN='https://oauth2.googleapis.com/token'
REDIRECT=lambda:os.environ['PUBLIC_URL'].rstrip('/')+'/api/google/callback'
@app.get('/api/google/start')
def google_start(req:Request):
 auth(req)
 if not os.getenv('GOOGLE_CLIENT_ID') or not os.getenv('GOOGLE_CLIENT_SECRET'):raise HTTPException(503,'Google OAuth environment variables missing')
 nonce=secrets.token_urlsafe(24)
 res=RedirectResponse(GOOGLE_AUTH+'?'+urlencode({'client_id':os.environ['GOOGLE_CLIENT_ID'],'redirect_uri':REDIRECT(),'response_type':'code','scope':'https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube.readonly','access_type':'offline','prompt':'consent','state':SIGN.dumps(nonce)}))
 res.set_cookie('oauth_state',nonce,httponly=True,secure=REDIRECT().startswith('https:'),samesite='lax',max_age=600)
 return res
@app.get('/api/google/callback')
def google_callback(req:Request,code:str='',state:str=''):
 auth(req)
 try: valid=SIGN.loads(state,max_age=600)==req.cookies.get('oauth_state')
 except Exception:valid=False
 if not valid or not code:raise HTTPException(400,'Invalid OAuth state')
 r=requests.post(GOOGLE_TOKEN,data={'code':code,'client_id':os.environ['GOOGLE_CLIENT_ID'],'client_secret':os.environ['GOOGLE_CLIENT_SECRET'],'redirect_uri':REDIRECT(),'grant_type':'authorization_code'},timeout=40);r.raise_for_status();token=r.json()
 if not token.get('refresh_token'):raise HTTPException(400,'Google did not issue a refresh token')
 with db() as c:c.execute("INSERT OR REPLACE INTO oauth VALUES ('google',?)",(enc(token['refresh_token']),))
 res=RedirectResponse('/');res.delete_cookie('oauth_state');return res
@app.post('/api/google/disconnect')
def disconnect(req:Request):
 csrf(req)
 with db() as c:c.execute("DELETE FROM oauth WHERE name='google'")
 return {'ok':True}

def google_access():
 with db() as c:r=c.execute("SELECT encrypted FROM oauth WHERE name='google'").fetchone()
 if not r:raise RuntimeError('YouTube not connected')
 resp=requests.post(GOOGLE_TOKEN,data={'client_id':os.environ['GOOGLE_CLIENT_ID'],'client_secret':os.environ['GOOGLE_CLIENT_SECRET'],'refresh_token':dec(r['encrypted']),'grant_type':'refresh_token'},timeout=40);resp.raise_for_status();return resp.json()['access_token']

def provider_keys(provider):
 with db() as c:rows=c.execute('SELECT * FROM api_keys WHERE provider=? AND disabled=0 AND fail_until<? ORDER BY id',(provider,time.time())).fetchall()
 return [(r['id'],dec(r['secret'])) for r in rows]

def cooldown(kid,seconds):
 with db() as c:c.execute('UPDATE api_keys SET fail_until=? WHERE id=?',(time.time()+seconds,kid))

def call_poll(prompt):
 keys=provider_keys('pollinations')
 if not keys:raise RuntimeError('No available Pollinations key')
 for kid,key in keys:
  try:
   r=requests.post(os.getenv('POLLINATIONS_TEXT_URL','https://gen.pollinations.ai/v1/chat/completions'),headers={'Authorization':'Bearer '+key},json={'model':os.getenv('POLLINATIONS_MODEL','openai'),'messages':[{'role':'system','content':'Respond with valid JSON only, keys: title, description, scenes (array of concise English visual prompts). Avoid copyrighted characters. Scenes must share exactly the same character appearance and location.'},{'role':'user','content':prompt}]},timeout=90)
   if r.status_code in (429,500,502,503,504):cooldown(kid,120);continue
   r.raise_for_status();return json.loads(r.json()['choices'][0]['message']['content'].strip().removeprefix('```json').removesuffix('```').strip())
  except (requests.RequestException,ValueError,KeyError):cooldown(kid,60)
 raise RuntimeError('Pollinations requests failed')

def download_video(url,path):
 if not url.startswith('https://'):raise RuntimeError('Video provider must return HTTPS URL')
 with requests.get(url,stream=True,timeout=180) as r:
  r.raise_for_status()
  with open(path,'wb') as f:
   size=0
   for chunk in r.iter_content(65536):
    size+=len(chunk)
    if size>150_000_000:raise RuntimeError('Video too large')
    f.write(chunk)

def deapi_scene(prompt,duration,path):
 endpoint=os.getenv('DEAPI_VIDEO_URL','')
 if not endpoint:raise RuntimeError('DEAPI_VIDEO_URL not configured; check provider API docs')
 keys=provider_keys('deapi')
 if not keys:raise RuntimeError('No available deAPI key')
 for kid,key in keys:
  try:
   r=requests.post(endpoint,headers={'Authorization':'Bearer '+key},json={'model':os.getenv('DEAPI_MODEL',''),'prompt':prompt,'duration':duration,'aspect_ratio':'9:16'},timeout=90)
   if r.status_code in (429,500,502,503,504):cooldown(kid,120);continue
   r.raise_for_status();data=r.json()
   url=data.get('video_url') or data.get('url') or data.get('output',{}).get('url') if isinstance(data.get('output',{}),dict) else None
   jobid=data.get('job_id') or data.get('id') or data.get('request_id')
   if not url and jobid:
    status_url=os.getenv('DEAPI_STATUS_URL','')
    if not status_url:raise RuntimeError('DEAPI_STATUS_URL required for asynchronous jobs')
    for _ in range(120):
     time.sleep(5)
     pollurl=status_url.replace('{job_id}',str(jobid)) if '{job_id}' in status_url else status_url.rstrip('/')+'/'+str(jobid)
     s=requests.get(pollurl,headers={'Authorization':'Bearer '+key},timeout=40);s.raise_for_status();d=s.json()
     if d.get('status') in ('failed','error','cancelled'):raise RuntimeError('deAPI generation failed: '+str(d.get('error','unknown')))
     url=d.get('video_url') or d.get('url')
     if not url and isinstance(d.get('output'),dict):url=d['output'].get('url')
     if url:break
   if not url:raise RuntimeError('deAPI returned no video URL')
   download_video(url,path);return
  except requests.RequestException:cooldown(kid,120)
 raise RuntimeError('All deAPI keys unavailable or provider response incompatible; verify API endpoint/response mapping')

def setjob(jid,**kwargs):
 with db() as c:c.execute('UPDATE jobs SET '+','.join(k+'=?' for k in kwargs)+' WHERE id=?',(*kwargs.values(),jid))

def render(j):
 jid=j['id'];seconds=j['seconds'];count=math.ceil(seconds/10)
 setjob(jid,status='running',progress='ساخت سناریو',started=datetime.now(timezone.utc).isoformat())
 plan=call_poll(f"Create {count} consecutive scene prompts for a {seconds}-second {j['style']} video about: {j['topic']}. Language for title and description: {j['language']}. Consistent same character, clothing, location, lighting, and camera continuity. Output exactly {count} scenes in JSON.")
 scenes=plan.get('scenes',[])
 if len(scenes)<count:raise RuntimeError('AI did not provide enough scenes')
 clips=[];remaining=seconds
 for i in range(count):
  duration=min(10,remaining);remaining-=duration
  setjob(jid,progress=f'تولید کلیپ {i+1} از {count}')
  path=DATA/'videos'/f'{jid}_scene_{i}.mp4'
  deapi_scene(str(scenes[i])+'; maintain identical subject and environment as previous scene; continuous shot',duration,path)
  clips.append(path)
 setjob(jid,progress='اتصال ویدیوها')
 norm=[]
 for i,p in enumerate(clips):
  n=DATA/'videos'/f'{jid}_norm_{i}.mp4'
  subprocess.run(['ffmpeg','-y','-i',str(p),'-t',str(min(10,seconds-i*10)),'-vf','scale=720:1280:force_original_aspect_ratio=decrease,pad=720:1280:(ow-iw)/2:(oh-ih)/2,fps=24,format=yuv420p','-an','-c:v','libx264','-preset','veryfast',str(n)],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,timeout=240)
  norm.append(n)
 lst=DATA/'videos'/f'{jid}_concat.txt';lst.write_text(''.join("file '"+str(p)+"'\n" for p in norm))
 out=DATA/'videos'/f'{jid}.mp4'
 subprocess.run(['ffmpeg','-y','-f','concat','-safe','0','-i',str(lst),'-c','copy',str(out)],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,timeout=180)
 for p in clips+norm+[lst]:p.unlink(missing_ok=True)
 setjob(jid,video=out.name,title=str(plan.get('title',j['topic']))[:100],description=str(plan.get('description',j['topic']))[:4500],status='review' if j['mode']=='review' else 'upload',progress='منتظر تأیید' if j['mode']=='review' else 'آماده انتشار')

def upload(j):
 if not j['video']:raise RuntimeError('No generated video')
 access=google_access();path=DATA/'videos'/j['video']
 metadata={'snippet':{'title':j['title'] or j['topic'],'description':j['description'] or '', 'categoryId':'22'},'status':{'privacyStatus':'private'}}
 start=requests.post('https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status',headers={'Authorization':'Bearer '+access,'Content-Type':'application/json; charset=UTF-8','X-Upload-Content-Type':'video/mp4','X-Upload-Content-Length':str(path.stat().st_size)},json=metadata,timeout=60)
 start.raise_for_status();location=start.headers.get('Location')
 if not location:raise RuntimeError('YouTube upload session missing')
 with open(path,'rb') as f:
  result=requests.put(location,headers={'Authorization':'Bearer '+access,'Content-Type':'video/mp4'},data=f,timeout=900)
 result.raise_for_status();videoid=result.json().get('id')
 if not videoid:raise RuntimeError('YouTube did not return video ID')
 setjob(j['id'],status='published',youtube_id=videoid,progress='آپلود شد (خصوصی)')

def ready(j):
 if not j['scheduled']:return True
 try:return datetime.now(timezone.utc)>=datetime.fromisoformat(j['scheduled']).replace(tzinfo=ZoneInfo(j['timezone'])).astimezone(timezone.utc)
 except Exception:return False

def worker():
 while True:
  try:
   with db() as c:jobs=[dict(r) for r in c.execute("SELECT * FROM jobs WHERE status IN ('queued','upload') ORDER BY id LIMIT 10")]
   for j in jobs:
    if not ready(j):continue
    try:
     if j['status']=='queued':render(j)
     else:upload(j)
    except Exception as e:setjob(j['id'],status='failed',error=str(e)[:1000],progress='خطا')
  except Exception as e:print('worker error',str(e),flush=True)
  time.sleep(5)
@app.on_event('startup')
def startup():
 with db() as c:c.execute("UPDATE jobs SET status='queued' WHERE status='running'")
 threading.Thread(target=worker,daemon=True).start()
