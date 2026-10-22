"""讲稿 + 课件 → 克隆音色配音 → 录课视频（每页一张图，翻页对齐配音）+ 字幕。

用法:
  envs/main/bin/python lvs/make_video.py --script 讲稿.md --slides 课件.pdf --voice-wav 参考.wav --voice-text 参考.txt
      [--out 名称] [--out-dir output] [--pages 3-10] [--engine cosyvoice|voxcpm] [--speed 0.85] [--subs burn|soft|none]

每页朗读“口播正文”和“过渡”，不读其他小节。每页时长 = 前留白 + 音频 + 后留白。
输出 名称.mp4、名称.srt（按句），以及默认的 名称_字幕.mp4（字幕烧进画面，长句按标点拆成短行）。
--engine voxcpm 用 VoxCPM2（48kHz；本身没有语速参数，--speed 用 ffmpeg atempo 后期变速；会自动切到 VoxCPM2 的 Python 环境）。
"""
import os, sys, re, time, argparse, subprocess, pathlib
HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import config, script as S, slides
from pronunciation import Pronunciation

ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
ap.add_argument('--script', required=True, help='讲稿 .md')
ap.add_argument('--slides', help='课件 .pdf / .pptx（和 --img-dir 二选一）')
ap.add_argument('--img-dir', help='已转好的页图目录，内含 p-01.png…')
ap.add_argument('--out', help='输出名称（默认取讲稿文件名）'); ap.add_argument('--out-dir', default='output')
ap.add_argument('--work-dir', help='中间文件目录（默认 输出目录/.work/名称）')
ap.add_argument('--pages', help='页码范围，如 3-10')
ap.add_argument('--voice-wav', default=config.BUILTIN_VOICE_WAV, help='参考录音（10–30 秒清晰人声）')
ap.add_argument('--voice-text', default=config.BUILTIN_VOICE_TXT, help='参考录音对应文字（文件路径）')
ap.add_argument('--engine', choices=['cosyvoice', 'voxcpm'], default='cosyvoice')
ap.add_argument('--speed', type=float, help='语速（默认 CosyVoice3 0.85，VoxCPM2 0.9）')
ap.add_argument('--vox-mode', choices=['ultimate', 'reference'], default='ultimate',
                help='VoxCPM2：ultimate 参考音频+文字续写（最像）/ reference 只用参考音频（可加 --style）')
ap.add_argument('--style', default='', help='VoxCPM2 reference 模式的风格描述，如“沉稳、亲切”')
ap.add_argument('--cfg', type=float, default=2.0); ap.add_argument('--steps', type=int, default=10)
ap.add_argument('--gap', type=float, default=0.5, help='句间停顿（秒）'); ap.add_argument('--para-gap', type=float, default=0.8)
ap.add_argument('--lead', type=float, default=0.6, help='翻页后开口前的留白'); ap.add_argument('--tail', type=float, default=0.9)
ap.add_argument('--tiao', action='store_true', help='“调”统一读 tiáo（调参、电调…；强调/调度/对调除外），控制类课程用')
ap.add_argument('--pronunciation', help='读音规则 UTF-8 文件，每行 原词 => 读法；只改配音，字幕保留原文')
ap.add_argument('--subs', choices=['burn', 'soft', 'none'], default='burn', help='burn 硬字幕 / soft 软字幕轨 / none 只出 .srt')
a = ap.parse_args()
try:
    pron = Pronunciation(pathlib.Path(a.pronunciation).read_text(encoding='utf-8-sig') if a.pronunciation else '', a.engine, a.tiao)
except (OSError, UnicodeError, ValueError) as e:
    ap.error(str(e))
assert a.slides or a.img_dir, '需要 --slides 或 --img-dir'
assert a.voice_wav and a.voice_text, '需要 --voice-wav 和 --voice-text（或在 .env 里配置内置音色）'
if a.speed is None:
    a.speed = 0.9 if a.engine == 'voxcpm' else 0.85

# VoxCPM2 的依赖装在单独的 venv 里，换解释器重新执行自己
# venv 的 python 是主环境 python 的软链接，所以按 sys.prefix（环境目录）判断
if a.engine == 'voxcpm' and pathlib.Path(sys.prefix).resolve() != pathlib.Path(config.PY_VOXCPM).parent.parent.resolve():
    os.execve(config.PY_VOXCPM, [config.PY_VOXCPM, '-u'] + sys.argv, config.subprocess_env())
os.environ['PATH'] = config.FFMPEG_DIR + os.pathsep + os.environ['PATH']

doc_title, pages = S.parse(pathlib.Path(a.script).read_text(encoding='utf-8'))
if a.pages:
    lo, _, hi = a.pages.partition('-')
    pages = [p for p in pages if int(lo) <= p['no'] <= int(hi or lo)]
assert pages, '讲稿里没有匹配的页（格式见 web/template.md）'
print(f'共 {len(pages)} 页', flush=True)

name = a.out or pathlib.Path(a.script).stem
outdir = pathlib.Path(a.out_dir); outdir.mkdir(parents=True, exist_ok=True)
work = pathlib.Path(a.work_dir) if a.work_dir else outdir / '.work' / name; work.mkdir(parents=True, exist_ok=True)
img_dir = pathlib.Path(a.img_dir) if a.img_dir else work / 'img'
if not a.img_dir:
    slides.to_images(slides.to_pdf(a.slides, work), img_dir)
missing = [p['no'] for p in pages if not (img_dir / f"p-{p['no']:02d}.png").exists()]
assert not missing, f'课件没有这些页：{missing}'

import torch, torchaudio
voice_txt = pathlib.Path(a.voice_text).read_text(encoding='utf-8').strip()
if a.engine == 'cosyvoice':
    sys.path[:0] = [str(config.COSYVOICE_DIR), str(config.COSYVOICE_DIR / 'third_party/Matcha-TTS')]
    from cosyvoice.cli.cosyvoice import AutoModel
    m = AutoModel(model_dir=str(config.COSYVOICE_MODEL))
    sr = m.sample_rate
    assert m.add_zero_shot_spk('You are a helpful assistant.<|endofprompt|>' + voice_txt, a.voice_wav, 'spk')
    def tts(s):
        return torch.cat([j['tts_speech'] for j in m.inference_zero_shot(s, '', '', zero_shot_spk_id='spk', stream=False, speed=a.speed)], 1)
else:
    from voxcpm import VoxCPM
    vm = VoxCPM.from_pretrained(str(config.VOXCPM_MODEL), load_denoiser=False)
    sr = vm.tts_model.sample_rate
    kw = dict(prompt_wav_path=a.voice_wav, prompt_text=voice_txt) if a.vox_mode == 'ultimate' else {}
    def stretch(w):
        r = subprocess.run(['ffmpeg', '-v', 'error', '-f', 'f32le', '-ar', str(sr), '-ac', '1', '-i', 'pipe:0',
                            '-af', f'atempo={a.speed}', '-f', 'f32le', 'pipe:1'], input=w.numpy().tobytes(), capture_output=True, check=True)
        return torch.frombuffer(bytearray(r.stdout), dtype=torch.float32).unsqueeze(0)
    def tts(s):
        text = f'({a.style}){s}' if a.style and a.vox_mode == 'reference' else s     # 续写模式会把括号念出来，只在 reference 模式加
        w = torch.from_numpy(vm.generate(text=text, reference_wav_path=a.voice_wav, cfg_value=a.cfg,
                                         inference_timesteps=a.steps, **kw)).float()
        return stretch(w) if abs(a.speed - 1) > 1e-3 else w.unsqueeze(0)

sil = lambda s: torch.zeros(1, int(round(s * sr)))

concat, srt, clock, t0 = [], [], 0.0, time.time()
for p in pages:
    pieces, cur = [sil(a.lead)], a.lead
    for pi, para in enumerate(p['paras']):
        sents = S.sentences(para)
        for si, s in enumerate(sents):
            w = tts(pron.apply(s))
            d = w.shape[1] / sr
            srt.append((clock + cur, clock + cur + d, s.strip()))
            pieces.append(w); cur += d
            if not (pi == len(p['paras']) - 1 and si == len(sents) - 1):
                g = a.para_gap if si == len(sents) - 1 else a.gap
                pieces.append(sil(g)); cur += g
    pieces.append(sil(a.tail)); cur += a.tail
    audio = torch.cat(pieces, 1)
    audio = audio / max(1.0, audio.abs().max().item() / 0.95)
    wav = work / f"P{p['no']:02d}.wav"; torchaudio.save(str(wav), audio, sr)
    concat.append((img_dir / f"p-{p['no']:02d}.png", wav, audio.shape[1] / sr))
    clock += audio.shape[1] / sr
    print(f"P{p['no']:02d} {p['title']}: {audio.shape[1]/sr:.1f}s", flush=True)

print('编码视频…', flush=True)
# 每页先单独编码成一段，再无损拼接，保证翻页与音频严格对齐
parts = []
for img, wav, d in concat:
    seg = work / (wav.stem + '.mp4')
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-loop', '1', '-framerate', '25', '-i', str(img), '-i', str(wav),
                    '-c:v', 'libx264', '-tune', 'stillimage', '-preset', 'medium', '-crf', '18', '-pix_fmt', 'yuv420p',
                    '-c:a', 'aac', '-b:a', '192k', '-ar', '48000', '-t', f'{d:.3f}', '-shortest', str(seg)], check=True)
    parts.append(seg)
lst = work / 'parts.txt'; lst.write_text(''.join(f"file '{p}'\n" for p in parts), encoding='utf-8')
mp4 = outdir / f'{name}.mp4'
subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'concat', '-safe', '0', '-i', str(lst), '-c', 'copy', '-movflags', '+faststart', str(mp4)], check=True)
import burn
burn.write_srt(srt, outdir / f'{name}.srt')
if a.subs != 'none':
    print('烧录字幕…', flush=True)
    sub_mp4, _, ncue = burn.burn(mp4, outdir / f'{name}.srt', soft=a.subs == 'soft')
    print(f'字幕版 {sub_mp4}（{ncue} 条）')
nchar = sum(p['chars'] for p in pages)
print(f'输出 {mp4}  时长 {clock/60:.1f} 分钟（{clock:.0f}s），{nchar}字，含停顿 {nchar/clock*60:.0f}字/分，用时 {time.time()-t0:.0f}s')
