"""数字、单位、日期按中文读：配音前把“2026年”“3.7V”“5%”改写成“二零二六年”“三点七V”“百分之五”。

规则来自 WeTextProcessing 的中文文本正则化（TN），用 wetext 加载（CosyVoice / VoxCPM 的环境里都有）。
wetext 0.1 起自带规则；更早的版本用 models/wetext-zh-tn/zh_tn_tagger.fst、zh_tn_verbalizer.fst
（install.sh 从 PyPI 的 WeTextProcessing 包里取出）。都没有时这一步自动跳过，不影响生成。
"""
import difflib
import functools
import re

import config

TN_DIR = config.path('LVS_TN_DIR', config.MODELS / 'wetext-zh-tn')
HAS_DIGIT = re.compile(r'[0-9０-９]')
CLAUSE = re.compile(r'([，。：；？！、（）“”‘’《》\s])')


@functools.lru_cache(maxsize=1)
def _normalizer():
    try:
        from wetext import Normalizer
    except ImportError:
        return None
    try:
        import wetext.config  # noqa: F401  wetext 0.1 起自带规则文件
        return Normalizer(lang='zh', operator='tn')
    except ImportError:
        pass
    # wetext 0.0.x 运行时要去 ModelScope 下载规则文件，这里改用本地的 WeTextProcessing 规则
    tagger, verbalizer = TN_DIR / 'zh_tn_tagger.fst', TN_DIR / 'zh_tn_verbalizer.fst'
    if not (tagger.is_file() and verbalizer.is_file()):
        return None
    return Normalizer(tagger_path=str(tagger), verbalizer_path=str(verbalizer), lang='zh', operator='tn')


def available():
    return _normalizer() is not None


def changes(text, normalize=None):
    """返回 [(原文片段, 读法)]：没变的片段读法为 None。拼起来的读法就是要送给配音模型的文字。"""
    if normalize is None:
        reader = _normalizer()
        normalize = reader.normalize if reader else None
    if not normalize or not HAS_DIGIT.search(text):
        return [(text, None)] if text else []
    out = []

    def keep(part):
        if part:
            if out and out[-1][1] is None:
                out[-1] = (out[-1][0] + part, None)
            else:
                out.append((part, None))

    # 按中文标点切成分句逐段处理：数字不会跨越这些标点；标点不送进规则，也就不会被换成半角，
    # 否则比对时句尾的“.”可能和“12.5”里的小数点错配
    for part in CLAUSE.split(text):
        if not HAS_DIGIT.search(part):
            keep(part)
            continue
        spoken = normalize(part)
        for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, part, spoken, autojunk=False).get_opcodes():
            before, after = part[i1:i2], spoken[j1:j2]
            if op == 'equal':
                keep(before)
            elif out and out[-1][1] is not None:
                # 相邻的改动合并成一段，例如“-10℃”→“负十摄氏度”
                out[-1] = (out[-1][0] + before, out[-1][1] + after)
            else:
                out.append((before, after))
    return out
