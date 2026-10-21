"""Qwen3-ASR 识别 + 强制对齐，返回带标点的“词”列表 [(开始秒, 结束秒, 文本)]。
标点挂在前一个词后面，空格挂在后一个词前面（英文单词之间）。长音频由 qwen_asr 自动按 3 分钟切段并修正偏移。"""
import torch
import config

def load():
    from qwen_asr import Qwen3ASRModel
    return Qwen3ASRModel.from_pretrained(
        str(config.ASR_MODEL), forced_aligner=str(config.ALIGNER_MODEL),
        forced_aligner_kwargs=dict(dtype=torch.bfloat16, device_map='cuda:0'),
        dtype=torch.bfloat16, device_map='cuda:0', max_inference_batch_size=1, max_new_tokens=1024)

def words(model, audio, context=''):
    r = model.transcribe(str(audio), context=context, language='Chinese', return_time_stamps=True)[0]
    text = r.text.translate(str.maketrans(',?!;:', '，？！；：'))
    out, pos = [], 0
    for it in (r.time_stamps or []):
        tok = it.text.strip()
        if not tok:
            continue
        i = text.find(tok, pos)
        if i < 0:                                  # 对齐结果和文本对不上时，原样追加
            out.append([it.start_time, it.end_time, tok]); continue
        gap = text[pos:i]
        punct = gap.rstrip(); space = gap[len(punct):]
        if out and punct:
            out[-1][2] += punct.strip()
        out.append([it.start_time, it.end_time, space[:1] + tok])
        pos = i + len(tok)
    if out and text[pos:].strip():
        out[-1][2] += text[pos:].strip()
    return text, [tuple(w) for w in out]
