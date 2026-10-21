"""音频/视频 → 中文 .srt 字幕（Qwen3-ASR-1.7B + 强制对齐，GPU）。按逐字时间戳断行，每行不超过 --max 字。
用法: envs/asr/bin/python lvs/transcribe.py 录音.mp4 [--out x.srt] [--terms 术语,术语] [--burn]
--terms 专有名词作为识别上下文；--burn 识别完直接把字幕烧进视频，输出“原名_字幕.mp4”。显存约 6GB，跑完即释放。"""
import sys, time, argparse, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent))
import os, burn as B, config, qasr
os.environ['PATH'] = config.FFMPEG_DIR + os.pathsep + os.environ['PATH']

ap = argparse.ArgumentParser()
ap.add_argument('input'); ap.add_argument('--out'); ap.add_argument('--max', type=int, default=22)
ap.add_argument('--terms', default='', help='额外的专有名词，逗号分隔，帮助识别')
ap.add_argument('--burn', action='store_true')
a = ap.parse_args()
src = pathlib.Path(a.input)
out = pathlib.Path(a.out) if a.out else src.with_suffix('.srt')

t0 = time.time()
m = qasr.load()
text, ws = qasr.words(m, src, context='、'.join(x.strip() for x in a.terms.replace('，', ',').split(',') if x.strip()))
del m

STOP = '。？！?!'; BREAK = '，、；：,;:' + STOP
cues, line, b, e, last = [], '', None, None, 0.0
for ws_, we, t in ws:
    # 停顿超过 1 秒也断开，避免一条字幕跨过长静音
    if line and (len((line + t).rstrip(B.DROP_TAIL)) > a.max or ws_ - last > 1.0):
        cues.append((b, e, line)); line, b = '', None
    if b is None:
        b, t = ws_, t.lstrip()
    line += t; e = last = we
    if t[-1] in STOP or (t[-1] in BREAK and len(line) >= a.max * 0.6):
        cues.append((b, e, line)); line, b = '', None
if line:
    cues.append((b, e, line))
cues = [(b, max(e, b + 0.6), t.strip().rstrip(B.DROP_TAIL)) for b, e, t in cues if t.strip().rstrip(B.DROP_TAIL)]
B.write_srt(cues, out)
dur = ws[-1][1] if ws else 0
print(text)
print(f'输出 {out}：{len(cues)} 条，语音约 {dur/60:.1f} 分钟，用时 {time.time()-t0:.0f}s')
if a.burn:
    v, s, n = B.burn(src, out, maxlen=a.max)
    print(f'已烧录字幕：{v}')
