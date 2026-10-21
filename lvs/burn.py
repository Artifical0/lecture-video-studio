"""把 .srt 字幕烧进视频（硬字幕），长句按标点拆成短行。只用标准库 + ffmpeg。
用法: python burn.py 视频.mp4 [字幕.srt] [--out 输出.mp4] [--max 22] [--size 52] [--soft]
默认字幕取同名 .srt，输出“原名_字幕.mp4”，同时写出拆好的“原名_字幕.srt”。
--soft 不重新编码，只把字幕作为可开关的软字幕轨封装进去（秒出，但部分平台/播放器不显示）。"""
import re, sys, argparse, subprocess, pathlib

PUNCT = '，、；：。？！,;:?!…'
DROP_TAIL = '，、；：。,;:'          # 行尾这些标点去掉（中文字幕习惯），？！保留

def parse_srt(text):
    cues = []
    for blk in re.split(r'\n\s*\n', text.strip()):
        lines = blk.strip().splitlines()
        i = next((k for k, l in enumerate(lines) if '-->' in l), None)
        if i is None:
            continue
        b, e = (t.strip() for t in lines[i].split('-->'))
        cues.append((to_sec(b), to_sec(e), ' '.join(lines[i + 1:]).strip()))
    return cues

def to_sec(t):
    h, m, s = t.replace(',', '.').split(':')
    return int(h) * 3600 + int(m) * 60 + float(s)

def weight(s):                       # 估计朗读时长：汉字/字母数字按字计，标点算半个字的停顿
    return len(re.findall(r'[一-鿿A-Za-z0-9]', s)) + 0.5 * sum(c in PUNCT for c in s) or 1

SOFT_CUT = '的了和与及就要是在把被对从让而或也都还'   # 超长句没有标点时，优先在这些字后面断行

def bisect(p, maxlen):
    """超长且无标点的片段：在靠近中点的位置切开。优先顿号，其次虚词，最后任意位置（不拆英文/数字）。"""
    if len(p.rstrip(DROP_TAIL)) <= maxlen:
        return [p]
    mid = len(p) / 2
    ok = [i for i in range(2, len(p) - 1) if not (p[i - 1].isascii() and p[i - 1].isalnum() and p[i].isascii() and p[i].isalnum())]
    def best(cands):
        return min(cands, key=lambda i: abs(i - mid)) if cands else None
    i = best([i for i in ok if p[i - 1] == '、' and abs(i - mid) <= len(p) / 4]) \
        or best([i for i in ok if p[i - 1] in SOFT_CUT and abs(i - mid) <= len(p) / 4]) or best(ok) or round(mid)
    return bisect(p[:i], maxlen) + bisect(p[i:], maxlen)

def split_text(s, maxlen):
    """先按标点切成小段，再贪心合并到不超过 maxlen；仍超长的小段从中间找合适位置切开。"""
    out = []
    for p in (x for x in re.split(r'(?<=[，；：。？！,;:?!…])', s) if x.strip()):
        for q in bisect(p, maxlen):
            if out and len((out[-1] + q).rstrip(DROP_TAIL)) <= maxlen:
                out[-1] += q
            else:
                out.append(q)
    return out

def split_cues(cues, maxlen):
    res = []
    for b, e, s in cues:
        segs = split_text(s, maxlen)
        ws = [weight(x) for x in segs]
        t = b
        for x, w in zip(segs, ws):
            d = (e - b) * w / sum(ws)
            txt = x.strip().rstrip(DROP_TAIL).strip()
            if txt:
                res.append((t, t + d, txt))
            t += d
    return res

def ts_srt(t):
    h, r = divmod(t, 3600); m, s = divmod(r, 60)
    return f'{int(h):02d}:{int(m):02d}:{s:06.3f}'.replace('.', ',')

def ts_ass(t):
    h, r = divmod(t, 3600); m, s = divmod(r, 60)
    return f'{int(h)}:{int(m):02d}:{s:05.2f}'

def write_srt(cues, path):
    path.write_text(''.join(f'{i}\n{ts_srt(b)} --> {ts_srt(e)}\n{t}\n\n' for i, (b, e, t) in enumerate(cues, 1)), encoding='utf-8')

def write_ass(cues, path, w, h, size):
    # 白字 + 半透明黑底条（BorderStyle=3，OutlineColour 当底色），深浅页面都清楚。底边距约为高度的 4%
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {w}
PlayResY: {h}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Noto Sans CJK SC Medium,{size},&H00FFFFFF,&H00FFFFFF,&H5A000000,&H5A000000,0,0,0,0,100,100,1,0,3,{size/4:.0f},0,2,60,60,{round(h*0.04)},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    body = ''.join(f'Dialogue: 0,{ts_ass(b)},{ts_ass(e)},Default,,0,0,0,,{t}\n' for b, e, t in cues)
    path.write_text(head + body, encoding='utf-8')

def probe_size(mp4):
    r = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries', 'stream=width,height',
                        '-of', 'csv=p=0', str(mp4)], capture_output=True, text=True, check=True)
    w, h = r.stdout.strip().split(',')[:2]
    return int(w), int(h)

def burn(mp4, srt=None, out=None, maxlen=22, size=None, soft=False):
    mp4 = pathlib.Path(mp4)
    srt = pathlib.Path(srt) if srt else mp4.with_suffix('.srt')
    out = pathlib.Path(out) if out else mp4.with_name(mp4.stem + '_字幕.mp4')
    cues = split_cues(parse_srt(srt.read_text(encoding='utf-8')), maxlen)
    out_srt = out.with_suffix('.srt'); write_srt(cues, out_srt)
    if soft:
        cmd = ['ffmpeg', '-v', 'error', '-y', '-i', str(mp4), '-i', str(out_srt), '-map', '0', '-map', '1',
               '-c', 'copy', '-c:s', 'mov_text', '-metadata:s:s:0', 'language=chi', '-movflags', '+faststart', str(out)]
    else:
        w, h = probe_size(mp4)
        ass = out.with_suffix('.ass'); write_ass(cues, ass, w, h, size or round(h * 0.048))
        esc = str(ass).replace('\\', '\\\\').replace(':', '\\:').replace("'", "\\'")
        cmd = ['ffmpeg', '-v', 'error', '-y', '-i', str(mp4), '-vf', f"ass='{esc}'",
               '-c:v', 'libx264', '-tune', 'stillimage', '-preset', 'medium', '-crf', '18', '-pix_fmt', 'yuv420p',
               '-c:a', 'copy', '-movflags', '+faststart', str(out)]
    subprocess.run(cmd, check=True)
    return out, out_srt, len(cues)

if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('video'); ap.add_argument('srt', nargs='?'); ap.add_argument('--out')
    ap.add_argument('--max', type=int, default=22, help='每行最多字数')
    ap.add_argument('--size', type=int, help='字号（像素，默认画面高度的 4.8%%）')
    ap.add_argument('--soft', action='store_true')
    a = ap.parse_args()
    out, out_srt, n = burn(a.video, a.srt, a.out, a.max, a.size, a.soft)
    print(f'输出 {out}（{n} 条字幕），字幕文件 {out_srt}')
