"""逐词修改送给 TTS 的文字；讲稿和字幕始终保留原文。

处理顺序：读音规则（全组共用的规则 + 本次临时规则）→ 数字和单位按中文读（可选）。
规则按原词替换，替换结果不再被后面的步骤修改；数字读法只处理没被规则替换的部分。
“调”读 tiáo 现在是共用规则里的几条（见 rulebook.py）；--tiao 参数和旧任务的 tiao 字段仍然有效。
"""
import re

import number_reading as N
import script as S

MAX_RULES = 100
MAX_TEXT = 16000
DIAO_WORDS = ('强调', '调度', '对调')


def check_rule(source, target, where='读音规则'):
    """校验一条规则，返回去掉首尾空白的 (原词, 读法)。"""
    source, target = (source or '').strip(), (target or '').strip()
    if not source or not target:
        raise ValueError(f'{where}：原词和读法都不能为空')
    if len(source) > 120 or len(target) > 300:
        raise ValueError(f'{where}：原词最多 120 字，读法最多 300 字')
    if any(ord(c) < 32 for c in source + target):
        raise ValueError(f'{where}：不能包含制表符等控制字符')
    if any(sep in source + target for sep in ('=>', '→')):
        raise ValueError(f'{where}：原词和读法里不能包含 => 或 →')
    return source, target


def parse_rules(text, limit=MAX_RULES):
    """每行 原词 => 读法（也接受 →）。重复原词报错，避免规则含义不明确。"""
    if len(text) > MAX_TEXT * 2:
        raise ValueError(f'读音规则最多 {MAX_TEXT} 个字符')
    rules = {}
    for no, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        parts = re.split(r'=>|→', line)
        if len(parts) != 2 or not all(p.strip() for p in parts):
            raise ValueError(f'读音规则第 {no} 行：请填写“原词 => 读法”，两边都不能为空')
        source, target = check_rule(*parts, where=f'读音规则第 {no} 行')
        if source in rules:
            raise ValueError(f'读音规则第 {no} 行：原词“{source}”重复，请只保留一条')
        rules[source] = target
        if len(rules) > limit:
            raise ValueError(f'读音规则最多 {limit} 条')
    return rules


class Pronunciation:
    def __init__(self, text='', engine='cosyvoice', tiao=False, numbers=False, number_reader=None):
        # 任务的规则文本 = 共用规则 + 本次临时规则，各自最多 MAX_RULES 条
        self.rules = parse_rules(text, limit=MAX_RULES * 2)
        self.tiao = tiao
        # number_reader: 文字 → 读法，测试时可替换；默认用 wetext 规则，缺少规则文件时跳过
        self.numbers = numbers and (number_reader is not None or N.available())
        self._number_reader = number_reader
        replacements = dict(self.rules)
        if tiao:
            for word in DIAO_WORDS:
                replacements.setdefault(word, word)
            replacements.setdefault('调', '[t][iáo]' if engine == 'cosyvoice' else '条')
        self.replacements = replacements
        # 同位置优先最长原词；一次替换，替换结果不会被其他规则二次修改。
        self.pattern = re.compile('|'.join(re.escape(w) for w in sorted(replacements, key=len, reverse=True))) if replacements else None

    def apply(self, text):
        return ''.join(seg['text'] for seg in self.segments(text))

    def segments(self, text):
        """把配音文字切成片段，拼起来就是 apply 的结果。改动过的片段带 source（原文）和 kind：
        rule 自定义规则、builtin “调”读 tiáo、number 数字和单位读法。"""
        out = []
        def plain(part):
            if not part:
                return
            pieces = N.changes(part, self._number_reader) if self.numbers else [(part, None)]
            for before, after in pieces:
                out.append({'text': before} if after is None else {'text': after, 'source': before, 'kind': 'number'})
        pos = 0
        for m in (self.pattern.finditer(text) if self.pattern else ()):
            plain(text[pos:m.start()])
            spoken = self.replacements[m.group()]
            if spoken == m.group():
                # 强调、调度等受保护的词原样保留，不算改动
                out.append({'text': spoken})
            else:
                kind = 'rule' if m.group() in self.rules else 'builtin'
                seg = {'text': spoken, 'source': m.group(), 'kind': kind}
                if spoken.startswith('['):
                    # CosyVoice 的拼音标注 [t][iáo] 不好读，预览里显示成 调(tiáo)
                    reading = ''.join(re.findall(r'\[([^\]]+)\]', spoken))
                    seg['display'] = f'{m.group()}({reading})'
                out.append(seg)
            pos = m.end()
        plain(text[pos:])
        return out

    def preview(self, pages, start=0, end=0, limit=12):
        """所选页里读法有变化的句子（最多 limit 条示例），以及每条自定义规则用上了几次。"""
        examples, changed, builtin, number = [], 0, 0, 0
        hits = dict.fromkeys(self.rules, 0)
        for page in pages:
            if (start and page['no'] < start) or (end and page['no'] > end):
                continue
            for para in page['paras']:
                for original in S.sentences(para):
                    original = original.strip()
                    segments = self.segments(original)
                    edits = [seg for seg in segments if 'source' in seg]
                    if not edits:
                        continue
                    changed += 1
                    for seg in edits:
                        if seg['kind'] == 'rule':
                            hits[seg['source']] += 1
                        elif seg['kind'] == 'builtin':
                            builtin += 1
                        else:
                            number += 1
                    if len(examples) < limit:
                        examples.append({'page': page['no'], 'original': original,
                                         'spoken': ''.join(seg['text'] for seg in segments),
                                         'segments': segments})
        return {'rule_count': len(self.rules), 'changed_sentences': changed, 'examples': examples,
                'hits': hits, 'unused': [source for source, n in hits.items() if not n],
                'builtin_hits': builtin, 'number_hits': number}
