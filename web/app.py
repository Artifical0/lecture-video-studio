"""录课视频工坊 · 网页端：上传课件(PDF/PPTX) + 讲稿 → 克隆音色配音 → 带字幕视频。
启动见 run.sh。任务排队、一次只跑一个；每个任务是独立子进程，跑完显存即释放。
数据目录（LVS_DATA）：jobs/<id>/ 每个任务的输入、页图、输出和日志；voices/<id>/ 上传的音色。"""
import os, re, sys, json, time, uuid, hmac, shutil, signal, hashlib, threading, subprocess, pathlib
from fastapi import FastAPI, Request, UploadFile, File, Form, HTTPException
from fastapi.responses import HTMLResponse, FileResponse, RedirectResponse, JSONResponse, PlainTextResponse

WEB = pathlib.Path(__file__).resolve().parent
LVS = WEB.parent / 'lvs'
sys.path.insert(0, str(LVS))
import config, script as S, slides
import number_reading
from pronunciation import Pronunciation

JOBS = config.DATA / 'jobs'; VOICES = config.DATA / 'voices'
for d in (JOBS, VOICES):
    d.mkdir(parents=True, exist_ok=True)
PASSWORD, SECRET = config.get('LVS_PASSWORD'), config.get('LVS_SECRET')
if not PASSWORD or not SECRET:
    raise SystemExit('请在 .env 里设置 LVS_PASSWORD 和 LVS_SECRET（见 .env.example）')
TOKEN = hmac.new(SECRET.encode(), PASSWORD.encode(), hashlib.sha256).hexdigest()
ID_RE = re.compile(r'^[0-9a-f]{12}$')
HAS_BUILTIN = bool(config.BUILTIN_VOICE_WAV and pathlib.Path(config.BUILTIN_VOICE_WAV).exists())
# 配音模型。rate = 1.0 倍速时的语速（字/分，含句间停顿）；gen = 生成耗时 / 视频时长（RTX 5070 Ti 实测）
ENGINES = {'cosyvoice': {'name': 'CosyVoice3', 'rate': 201 / 0.85, 'gen': 0.47, 'speed': (0.6, 1.3)},
           'voxcpm': {'name': 'VoxCPM2', 'rate': 228, 'gen': 0.5, 'speed': (0.7, 1.2)}}

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


# ---------- 登录 ----------
@app.middleware('http')
async def auth(request: Request, call_next):
    if request.url.path in ('/login', '/favicon.svg') or hmac.compare_digest(request.cookies.get('lvs', ''), TOKEN):
        return await call_next(request)
    if request.url.path.startswith('/api/'):
        return JSONResponse({'detail': '未登录'}, status_code=401)
    return RedirectResponse('/login', status_code=303)

@app.get('/login', response_class=HTMLResponse)
def login_page(err: int = 0):
    return (WEB / 'static/login.html').read_text(encoding='utf-8').replace('{{ERR}}', '密码不对，再试一次' if err else '')

@app.post('/login')
def login(password: str = Form(...)):
    if not hmac.compare_digest(password.strip(), PASSWORD):
        time.sleep(1)
        return RedirectResponse('/login?err=1', status_code=303)
    r = RedirectResponse('/', status_code=303)
    r.set_cookie('lvs', TOKEN, max_age=30 * 86400, httponly=True, samesite='lax')
    return r

@app.get('/', response_class=HTMLResponse)
def index():
    return (WEB / 'static/index.html').read_text(encoding='utf-8')

@app.get('/favicon.svg')
def favicon():
    return FileResponse(WEB / 'static/favicon.svg', media_type='image/svg+xml')

@app.get('/template.md')
def template():
    return FileResponse(WEB / 'template.md', filename='讲稿模板.md', media_type='text/markdown; charset=utf-8')


# ---------- 讲稿检查 ----------
def read_script(raw: bytes):
    try:
        return S.read_text(raw)
    except ValueError as e:
        raise HTTPException(400, str(e))

def pronunciation_settings(text, engine, tiao, numbers=''):
    try:
        return Pronunciation(text, engine, tiao == 'yes', numbers == 'yes')
    except ValueError as e:
        raise HTTPException(400, str(e))


def check_script(pages, npdf, speed, start, end, engine='cosyvoice'):
    """返回 (错误, 警告, 统计)。"""
    errs, warns = [], []
    if not pages:
        errs.append('讲稿里没找到“## P01｜标题”这样的页标题，请对照模板检查格式（“｜”是全角竖线）。')
        return errs, warns, {}
    sel = [p for p in pages if (not start or p['no'] >= start) and (not end or p['no'] <= end)]
    if not sel:
        errs.append('所选页码范围内没有讲稿页。')
    nos = [p['no'] for p in pages]
    dup = sorted({n for n in nos if nos.count(n) > 1})
    if dup:
        errs.append('页码重复：' + '、'.join(f'P{n:02d}' for n in dup))
    if npdf:
        over = [p['no'] for p in sel if p['no'] > npdf]
        if over:
            errs.append(f'课件只有 {npdf} 页，讲稿里有 ' + '、'.join(f'P{n:02d}' for n in over))
        miss = [n for n in range(start or 1, min(end or npdf, npdf) + 1) if n not in nos]
        if miss:
            warns.append('这些课件页没有讲稿，会被跳过：' + '、'.join(f'P{n:02d}' for n in miss[:30]) + ('…' if len(miss) > 30 else ''))
    empty = [p['no'] for p in sel if not p['paras']]
    if empty:
        warns.append('这些页没有口播内容，只会静默停留约 1.5 秒：' + '、'.join(f'P{n:02d}' for n in empty))
    eng = ENGINES.get(engine, ENGINES['cosyvoice'])
    chars = sum(p['chars'] for p in sel)
    minutes = chars / (eng['rate'] * speed) + len(sel) * 1.5 / 60
    return errs, warns, {'pages': len(sel), 'chars': chars, 'minutes': round(minutes, 1),
                         'gen_minutes': round(minutes * eng['gen'] + 1, 0)}

@app.post('/api/check')
def api_check(script: UploadFile = File(...), ppt: UploadFile | None = File(None), speed: float = Form(0.85),
              start: int = Form(0), end: int = Form(0), engine: str = Form('cosyvoice'),
              pronunciation: str = Form(''), tiao: str = Form(''), numbers: str = Form('')):
    pron = pronunciation_settings(pronunciation, engine, tiao, numbers)
    title, pages = S.parse(read_script(script.file.read()))
    npdf = None
    if ppt and ppt.filename and ppt.filename.lower().endswith('.pdf'):
        tmp = config.DATA / f'check-{uuid.uuid4().hex}.pdf'
        try:
            with tmp.open('wb') as f:
                shutil.copyfileobj(ppt.file, f)
            npdf = slides.page_count(tmp)
        finally:
            tmp.unlink(missing_ok=True)
    errs, warns, stats = check_script(pages, npdf, speed, start, end, engine)
    return {'title': title, 'pdf_pages': npdf, 'errors': errs, 'warnings': warns, 'stats': stats,
            'pronunciation': {**pron.preview(pages, start, end), 'numbers_available': number_reading.available()},
            'pages': [{'no': p['no'], 'title': p['title'], 'chars': p['chars']} for p in pages]}


# ---------- 音色 ----------
def voice_dir(vid):
    if not ID_RE.match(vid) or not (VOICES / vid / 'meta.json').exists():
        raise HTTPException(404, '没有这个音色')
    return VOICES / vid

def voice_files(vid):
    if vid in ('builtin', 'lecturer') and HAS_BUILTIN:      # lecturer = 旧版内置音色 ID
        return pathlib.Path(config.BUILTIN_VOICE_WAV), pathlib.Path(config.BUILTIN_VOICE_TXT)
    d = voice_dir(vid)
    return d / 'prompt.wav', d / 'prompt.txt'

def list_voices():
    out = []
    if HAS_BUILTIN:
        out.append({'id': 'builtin', 'name': config.BUILTIN_VOICE_NAME or '内置音色', 'builtin': True,
                    'text': pathlib.Path(config.BUILTIN_VOICE_TXT).read_text(encoding='utf-8').strip()})
    for d in sorted(VOICES.iterdir(), key=lambda p: p.stat().st_mtime):
        try:
            meta = json.loads((d / 'meta.json').read_text(encoding='utf-8'))
            meta['text'] = (d / 'prompt.txt').read_text(encoding='utf-8').strip()
            out.append(meta)
        except FileNotFoundError:
            continue
    return out

@app.get('/api/voices')
def api_voices():
    return list_voices()

@app.post('/api/voices')
def api_voice_add(name: str = Form(...), consent: str = Form(''), audio: UploadFile = File(...)):
    if consent != 'yes':
        raise HTTPException(400, '请先确认已取得声音本人的同意')
    name = name.strip()[:30]
    if not name:
        raise HTTPException(400, '请填写音色名称')
    vid = uuid.uuid4().hex[:12]
    d = VOICES / vid; d.mkdir()
    raw = d / ('source' + re.sub(r'[^a-z0-9.]', '', pathlib.Path(audio.filename or 'a.wav').suffix.lower())[:8])
    with raw.open('wb') as f:
        shutil.copyfileobj(audio.file, f)
    with VOICE_LOCK:      # 同时只处理一个音色
        r = subprocess.run([config.PY_ASR, str(LVS / 'voice_prep.py'), str(raw), str(d)],
                           capture_output=True, text=True, timeout=600, env=config.subprocess_env())
    if r.returncode != 0:
        shutil.rmtree(d, ignore_errors=True)
        msg = r.stderr.strip().splitlines()[-1] if r.stderr.strip() else '未知错误'
        if 'OutOfMemory' in msg:
            msg = '显卡显存不够（可能正在生成视频），请稍后再试'
        raise HTTPException(400, f'处理录音失败：{msg}')
    info = json.loads(r.stdout.strip().splitlines()[-1])
    raw.unlink(missing_ok=True)
    meta = {'id': vid, 'name': name, 'builtin': False, 'created': time.time(), 'duration': info['duration']}
    (d / 'meta.json').write_text(json.dumps(meta, ensure_ascii=False), encoding='utf-8')
    return {**meta, 'text': info['text'], 'warn': info['warn']}

@app.put('/api/voices/{vid}')
def api_voice_edit(vid: str, text: str = Form(''), name: str = Form('')):
    if vid == 'builtin':
        raise HTTPException(400, '内置音色不能修改')
    d = voice_dir(vid)
    if text.strip():
        (d / 'prompt.txt').write_text(text.strip(), encoding='utf-8')
    if name.strip():
        meta = json.loads((d / 'meta.json').read_text(encoding='utf-8'))
        meta['name'] = name.strip()[:30]
        (d / 'meta.json').write_text(json.dumps(meta, ensure_ascii=False), encoding='utf-8')
    return {'ok': True}

@app.delete('/api/voices/{vid}')
def api_voice_del(vid: str):
    if vid == 'builtin':
        raise HTTPException(400, '内置音色不能删除')
    shutil.rmtree(voice_dir(vid))
    return {'ok': True}

@app.get('/api/voices/{vid}/audio')
def api_voice_audio(vid: str):
    return FileResponse(voice_files(vid)[0], media_type='audio/wav')


# ---------- 任务 ----------
LOCK = threading.Lock()            # 保护 job.json 读写
VOICE_LOCK = threading.Lock()
WAKE = threading.Event()
CURRENT = {'id': None, 'proc': None}

def job_dir(jid):
    if not ID_RE.match(jid) or not (JOBS / jid / 'job.json').exists():
        raise HTTPException(404, '没有这个任务')
    return JOBS / jid

def load(jid):
    return json.loads((JOBS / jid / 'job.json').read_text(encoding='utf-8'))

def save(job):
    p = JOBS / job['id'] / 'job.json'
    tmp = p.with_suffix('.tmp'); tmp.write_text(json.dumps(job, ensure_ascii=False), encoding='utf-8'); tmp.replace(p)

def update(jid, **kw):
    with LOCK:
        job = load(jid); job.update(kw); save(job)
        return job

def all_jobs():
    out = []
    for d in JOBS.iterdir():
        try:
            out.append(load(d.name))
        except (FileNotFoundError, NotADirectoryError, json.JSONDecodeError):
            pass
    return sorted(out, key=lambda j: j['created'], reverse=True)

def safe_name(s):
    s = re.sub(r'[\\/:*?"<>|\s\'%]+', '_', s).strip('._')
    return s[:60] or '录课视频'

@app.post('/api/jobs')
def api_job_add(ppt: UploadFile = File(...), script: UploadFile = File(...), title: str = Form(''),
                voice: str = Form(''), speed: float = Form(0.85), start: int = Form(0), end: int = Form(0),
                subs: str = Form('burn'), tiao: str = Form(''), engine: str = Form('cosyvoice'),
                vox_mode: str = Form('ultimate'), style: str = Form(''), cfg: float = Form(2.0), steps: int = Form(10),
                pronunciation: str = Form(''), numbers: str = Form('')):
    pron = pronunciation_settings(pronunciation, engine, tiao, numbers)
    ext = pathlib.Path(ppt.filename or '').suffix.lower()
    if ext not in ('.pdf', '.pptx', '.ppt'):
        raise HTTPException(400, '课件请上传 .pdf（推荐）或 .pptx')
    if subs not in ('burn', 'soft', 'none'):
        raise HTTPException(400, '字幕选项不对')
    if engine not in ENGINES:
        raise HTTPException(400, '没有这个配音模型')
    lo, hi = ENGINES[engine]['speed']
    if not lo <= speed <= hi:
        raise HTTPException(400, f'语速范围 {lo}–{hi}')
    if engine == 'voxcpm':
        if vox_mode not in ('ultimate', 'reference'):
            raise HTTPException(400, '克隆方式不对')
        if not (1.0 <= cfg <= 3.0 and 4 <= steps <= 30):
            raise HTTPException(400, 'CFG 范围 1.0–3.0，推理步数 4–30')
        style = re.sub(r'[()（）\n]', '', style).strip()[:40] if vox_mode == 'reference' else ''
    if not voice:
        raise HTTPException(400, '请先选择或添加一个音色')
    voice_files(voice)                                  # 校验音色存在
    src = read_script(script.file.read())
    mdtitle, pages = S.parse(src)
    jid = uuid.uuid4().hex[:12]
    d = JOBS / jid; d.mkdir()
    (d / 'script.md').write_text(src, encoding='utf-8')
    with (d / f'input{ext}').open('wb') as f:
        shutil.copyfileobj(ppt.file, f)
    npdf = slides.page_count(d / 'input.pdf') if ext == '.pdf' else None
    errs, warns, stats = check_script(pages, npdf, speed, start, end, engine)
    if errs:
        shutil.rmtree(d)
        raise HTTPException(400, '\n'.join(errs))
    voice_name = next((v['name'] for v in list_voices() if v['id'] == voice), voice)
    job = {'id': jid, 'title': (title.strip() or mdtitle or pathlib.Path(script.filename or '').stem or '录课视频')[:80],
           'created': time.time(), 'status': 'queued', 'stage': '排队中', 'pages_total': stats['pages'], 'pages_done': 0,
           'est_minutes': stats['minutes'], 'est_gen_minutes': stats['gen_minutes'], 'voice': voice, 'voice_name': voice_name,
           'ppt_name': ppt.filename, 'script_name': script.filename, 'speed': speed, 'start': start, 'end': end,
           'subs': subs, 'tiao': tiao == 'yes', 'engine': engine, 'engine_name': ENGINES[engine]['name'],
           'pronunciation': pronunciation, 'numbers': pron.numbers,
           'pronunciation_preview': pron.preview(pages, start, end),
           **({'vox_mode': vox_mode, 'style': style, 'cfg': cfg, 'steps': steps} if engine == 'voxcpm' else {}),
           'warnings': warns, 'outputs': {}, 'error': ''}
    with LOCK:
        save(job)
    WAKE.set()
    return job

@app.get('/api/jobs')
def api_jobs():
    jobs = all_jobs()
    queued = sorted((j for j in jobs if j['status'] == 'queued'), key=lambda j: j['created'])
    for i, j in enumerate(queued, 1):
        j['queue_pos'] = i
    for j in jobs:
        j['has_thumb'] = (JOBS / j['id'] / 'thumb.jpg').exists()
    return {'jobs': jobs, 'gpu': gpu_status(), 'now': time.time()}

@app.get('/api/jobs/{jid}/thumb')
def api_job_thumb(jid: str):
    p = job_dir(jid) / 'thumb.jpg'
    if not p.exists():
        raise HTTPException(404)
    return FileResponse(p, media_type='image/jpeg', headers={'Cache-Control': 'max-age=86400'})

@app.get('/api/jobs/{jid}/log', response_class=PlainTextResponse)
def api_job_log(jid: str):
    p = job_dir(jid) / 'log.txt'
    if not p.exists():
        return ''
    lines = [l.split('\r')[-1] for l in p.read_text(encoding='utf-8', errors='replace').splitlines()]
    return '\n'.join(l for l in lines if l.strip())[-6000:]

@app.post('/api/jobs/{jid}/cancel')
def api_job_cancel(jid: str):
    job_dir(jid)
    job = load(jid)
    if job['status'] == 'queued':
        update(jid, status='canceled', stage='已取消')
    elif job['status'] == 'running' and CURRENT['id'] == jid:
        update(jid, cancel=True)
        p = CURRENT['proc']
        if p:
            os.killpg(p.pid, signal.SIGTERM)
    return {'ok': True}

@app.delete('/api/jobs/{jid}')
def api_job_del(jid: str):
    d = job_dir(jid)
    if load(jid)['status'] in ('running', 'queued'):
        raise HTTPException(400, '先取消任务再删除')
    shutil.rmtree(d)
    return {'ok': True}

@app.post('/api/jobs/{jid}/retry')
def api_job_retry(jid: str):
    """失败/取消的任务：用同样的材料和参数重新排队。"""
    d = job_dir(jid); old = load(jid)
    if old['status'] not in ('failed', 'canceled'):
        raise HTTPException(400, '只有失败或已取消的任务可以重新生成')
    voice_files(old['voice'])
    nid = uuid.uuid4().hex[:12]; nd = JOBS / nid; nd.mkdir()
    for f in [d / 'script.md'] + list(d.glob('input.*')):
        shutil.copy2(f, nd / f.name)
    keep = {k: v for k, v in old.items() if k not in ('id', 'created', 'status', 'stage', 'pages_done', 'outputs', 'error',
                                                      'started', 'finished', 'cancel', 'video_minutes', 'queue_pos', 'has_thumb')}
    job = {**keep, 'id': nid, 'created': time.time(), 'status': 'queued', 'stage': '排队中', 'pages_done': 0, 'outputs': {}, 'error': ''}
    with LOCK:
        save(job)
    WAKE.set()
    return job

@app.get('/api/jobs/{jid}/file/{kind}')
def api_job_file(jid: str, kind: str, dl: int = 1):
    d = job_dir(jid); job = load(jid)
    rel = job['outputs'].get(kind)
    if not rel:
        raise HTTPException(404, '文件不存在')
    p = d / rel
    media = {'.mp4': 'video/mp4', '.srt': 'application/x-subrip'}.get(p.suffix, 'application/octet-stream')
    return FileResponse(p, media_type=media, filename=p.name if dl else None)

def gpu_status():
    try:
        r = subprocess.run(['nvidia-smi', '--query-gpu=name,memory.used,memory.total,utilization.gpu', '--format=csv,noheader,nounits'],
                           capture_output=True, text=True, timeout=5)
        name, used, total, util = (x.strip() for x in r.stdout.strip().split(','))
        return {'name': name.replace('NVIDIA GeForce ', ''), 'used': int(used), 'total': int(total), 'util': int(util)}
    except Exception:
        return None


# ---------- 后台工作线程 ----------
def prepare_images(d, job, log):
    src = next(d.glob('input.*'))
    if src.suffix.lower() != '.pdf':
        update(job['id'], stage='PPTX 转 PDF')
    pdf = slides.to_pdf(src, d, log)
    if pdf != src:
        pdf.rename(d / 'input.pdf'); pdf = d / 'input.pdf'
        _, pages = S.parse((d / 'script.md').read_text(encoding='utf-8'))
        errs, warns, _ = check_script(pages, slides.page_count(pdf), job['speed'], job['start'], job['end'], job.get('engine', 'cosyvoice'))
        if errs:
            raise RuntimeError('\n'.join(errs))
        update(job['id'], warnings=warns)
    update(job['id'], stage='课件转图片')
    slides.to_images(pdf, d / 'img')
    first = job['start'] or 1
    cand = sorted(d.glob('img/p-*.png'))
    thumb = d / 'img' / f'p-{first:02d}.png'
    slides.thumbnail(thumb if thumb.exists() else cand[0], d / 'thumb.jpg')

def run_job(job):
    jid = job['id']; d = JOBS / jid
    update(jid, status='running', stage='准备中', started=time.time())
    with (d / 'log.txt').open('a', encoding='utf-8') as log:
        try:
            prepare_images(d, job, log)
            _, pages = S.parse((d / 'script.md').read_text(encoding='utf-8'))
            nos = [p['no'] for p in pages]
            start = job['start'] or min(nos); end = job['end'] or max(nos)
            wav, txt = voice_files(job['voice'])
            name = safe_name(job['title'])
            cmd = [config.PY_MAIN, '-u', str(LVS / 'make_video.py'), '--script', str(d / 'script.md'), '--img-dir', str(d / 'img'),
                   '--pages', f'{start}-{end}', '--out', name, '--out-dir', str(d / 'out'), '--work-dir', str(d / 'work'),
                   '--voice-wav', str(wav), '--voice-text', str(txt), '--speed', str(job['speed']), '--subs', job['subs']]
            if job['tiao']:
                cmd.append('--tiao')
            if job.get('numbers'):
                cmd.append('--numbers')
            if job.get('pronunciation'):
                rules_file = d / 'pronunciation.txt'
                rules_file.write_text(job['pronunciation'], encoding='utf-8')
                cmd += ['--pronunciation', str(rules_file)]
            if job.get('engine') == 'voxcpm':
                cmd += ['--engine', 'voxcpm', '--vox-mode', job['vox_mode'], '--cfg', str(job['cfg']), '--steps', str(job['steps'])]
                if job.get('style'):
                    cmd += ['--style', job['style']]
            log.write('$ ' + ' '.join(cmd) + '\n'); log.flush()
            update(jid, stage='加载配音模型')
            p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
                                 start_new_session=True, env={**config.subprocess_env(), 'PYTHONUNBUFFERED': '1'})
            CURRENT['proc'] = p
            done = 0
            for line in p.stdout:
                log.write(line); log.flush()
                line = line.strip()
                if re.match(r'^P\d+ .*: [\d.]+s$', line):
                    done += 1
                    update(jid, pages_done=done, stage=f'配音中 {done}/{job["pages_total"]} 页')
                elif line == '编码视频…':
                    update(jid, stage='合成视频')
                elif line == '烧录字幕…':
                    update(jid, stage='烧录字幕')
                elif line.startswith('输出 '):
                    m = re.search(r'时长 ([\d.]+) 分钟', line)
                    if m:
                        update(jid, video_minutes=float(m.group(1)))
            rc = p.wait()
            if load(jid).get('cancel'):
                update(jid, status='canceled', stage='已取消', finished=time.time())
                return
            if rc != 0:
                tail = [l for l in (d / 'log.txt').read_text(encoding='utf-8', errors='replace').splitlines() if l.strip()][-3:]
                raise RuntimeError('生成失败：' + ' / '.join(t.split('\r')[-1] for t in tail)[-400:])
            out = d / 'out'
            outputs = {k: f'out/{f}' for k, f in (('mp4', f'{name}.mp4'), ('sub_mp4', f'{name}_字幕.mp4'),
                                                   ('srt', f'{name}_字幕.srt' if job['subs'] != 'none' else f'{name}.srt'))
                       if (out / f).exists()}
            shutil.rmtree(d / 'work', ignore_errors=True)       # 每页的中间音视频，删掉省空间
            update(jid, status='done', stage='完成', outputs=outputs, finished=time.time())
        except Exception as e:
            log.write(f'\n错误: {e}\n')
            update(jid, status='failed', stage='失败', error=str(e), finished=time.time())
        finally:
            CURRENT['proc'] = None

def worker():
    while True:
        queued = sorted((j for j in all_jobs() if j['status'] == 'queued'), key=lambda j: j['created'])
        if not queued:
            WAKE.wait(30); WAKE.clear()
            continue
        CURRENT['id'] = queued[0]['id']
        run_job(queued[0])
        CURRENT['id'] = None

@app.on_event('startup')
def start_worker():
    for j in all_jobs():                      # 服务重启时正在跑的任务视为失败
        if j['status'] == 'running':
            update(j['id'], status='failed', stage='失败', error='服务重启，任务中断，可点“重新生成”')
        d = JOBS / j['id']
        pngs = sorted(d.glob('img/p-*.png'))
        if pngs and not (d / 'thumb.jpg').exists():   # 旧任务补缩略图
            try:
                slides.thumbnail(pngs[0], d / 'thumb.jpg')
            except Exception:
                pass
    threading.Thread(target=worker, daemon=True).start()
