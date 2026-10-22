"""逐词修改送给 TTS 的文字；讲稿和字幕始终保留原文。只用标准库。"""
import re

import script as S

MAX_RULES = 100
MAX_TEXT = 16000
DIAO_WORDS = ('强调', '调度', '对调')


def parse_rules(text):
    """每行 原词 => 读法（也接受 →）。重复原词报错，避免规则含义不明确。"""
    if len(text) > MAX_TEXT:
        raise ValueError(f'读音规则最多 {MAX_TEXT} 个字符')
    rules = {}
    for no, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        parts = re.split(r'=>|→', line)
        if len(parts) != 2 or not all(p.strip() for p in parts):
            raise ValueError(f'读音规则第 {no} 行：请填写“原词 => 读法”，两边都不能为空')
        source, target = (p.strip() for p in parts)
        if len(source) > 120 or len(target) > 300:
            raise ValueError(f'读音规则第 {no} 行：原词最多 120 字，读法最多 300 字')
        if any(ord(c) < 32 for c in source + target):
            raise ValueError(f'读音规则第 {no} 行：不能包含制表符等控制字符')
        if source in rules:
            raise ValueError(f'读音规则第 {no} 行：原词“{source}”重复，请只保留一条')
        rules[source] = target
        if len(rules) > MAX_RULES:
            raise ValueError(f'读音规则最多 {MAX_RULES} 条')
    return rules


class Pronunciation:
    def __init__(self, text='', engine='cosyvoice', tiao=False):
        self.rules = parse_rules(text)
        replacements = dict(self.rules)
        if tiao:
            for word in DIAO_WORDS:
                replacements.setdefault(word, word)
            replacements.setdefault('调', '[t][iáo]' if engine == 'cosyvoice' else '条')
        self.replacements = replacements
        # 同位置优先最长原词；一次替换，替换结果不会被其他规则二次修改。
        self.pattern = re.compile('|'.join(re.escape(w) for w in sorted(replacements, key=len, reverse=True))) if replacements else None

    def apply(self, text):
        if not self.pattern:
            return text
        return self.pattern.sub(lambda m: self.replacements[m.group()], text)

    def preview(self, pages, start=0, end=0, limit=12):
        examples, changed = [], 0
        for page in pages:
            if (start and page['no'] < start) or (end and page['no'] > end):
                continue
            for para in page['paras']:
                for original in S.sentences(para):
                    spoken = self.apply(original)
                    if spoken != original:
                        changed += 1
                        if len(examples) < limit:
                            examples.append({'page': page['no'], 'original': original.strip(), 'spoken': spoken.strip()})
        return {'rule_count': len(self.rules), 'changed_sentences': changed, 'examples': examples}
