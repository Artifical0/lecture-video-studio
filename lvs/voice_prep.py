"""参考录音 → 音色样本：Qwen3-ASR 识别前 2 分钟，挑连续的完整句子凑够 12–28 秒，裁出 prompt.wav 并写 prompt.txt。
用识别环境运行：envs/asr/bin/python lvs/voice_prep.py 输入音视频 输出目录
结果以 JSON 打印到 stdout 最后一行。"""
import os, sys, json, subprocess, pathlib, tempfile
sys.path.insert(0, str(pathlib.Path(__file__).parent))
import config, qasr
os.environ['PATH'] = config.FFMPEG_DIR + os.pathsep + os.environ['PATH']

src, out = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
out.mkdir(parents=True, exist_ok=True)
MIN, MAX = 12.0, 28.0

with tempfile.TemporaryDirectory() as td:
    head = pathlib.Path(td) / 'head.wav'
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', str(src), '-t', '120', '-vn', '-ac', '1', '-ar', '16000', str(head)], check=True)
    m = qasr.load()
    _, ws = qasr.words(m, head)
    del m

# 逐字结果按句末标点或 0.6 秒以上停顿切成句子
segs, cur = [], []
for i, w in enumerate(ws):
    cur.append(w)
    nxt = ws[i + 1][0] if i + 1 < len(ws) else None
    if w[2][-1] in '。？！?!' or nxt is None or nxt - w[1] > 0.6:
        segs.append((cur[0][0], cur[-1][1], ''.join(x[2] for x in cur).strip())); cur = []
assert segs, '没有识别到说话内容，请换一段清晰的人声录音'
# 从每个起点往后凑句子，选总长落在 [MIN, MAX] 且最接近 20 秒的一段；都凑不够就用最长的一段
best = None
for i in range(len(segs)):
    for j in range(i, len(segs)):
        d = segs[j][1] - segs[i][0]
        if d > MAX:
            break
        score = abs(d - 20) if d >= MIN else 100 + (MIN - d)
        if best is None or score < best[0]:
            best = (score, i, j)
_, i, j = best
b, e = max(0.0, segs[i][0] - 0.15), segs[j][1] + 0.25
text = ''.join(s[2] for s in segs[i:j + 1])
text = text.rstrip('，、；：,;: ')
if text[-1] not in '。？！?!…':
    text += '。'
subprocess.run(['ffmpeg', '-v', 'error', '-y', '-ss', f'{b:.2f}', '-t', f'{e - b:.2f}', '-i', str(src), '-vn', '-ac', '1', '-ar', '24000',
                '-af', 'loudnorm=I=-18:TP=-2', str(out / 'prompt.wav')], check=True)
(out / 'prompt.txt').write_text(text, encoding='utf-8')
print(json.dumps({'start': round(b, 2), 'end': round(e, 2), 'duration': round(e - b, 2), 'text': text,
                  'warn': '' if e - b >= MIN else f'有效人声只有 {e - b:.0f} 秒，建议 15 秒以上，克隆效果会更稳'}, ensure_ascii=False))
