"""全组共用的读音规则，保存在数据目录的 pronunciation-rules.json。

每条规则：{id, source, target, enabled, note}；可选 cosyvoice 字段是 CosyVoice 专用的读法
（它支持 [声母][韵母] 拼音标注，VoxCPM 不支持，只能用同音字），例如“调”读 tiáo。
任务提交时把当时启用的规则展开成“原词 => 读法”文本随任务保存，之后改规则不影响已提交的任务。
"""
import json
import os
import threading
import time
import uuid

from pronunciation import MAX_RULES, check_rule

# 第一次使用时放进规则库的内置规则：“调”读 tiáo，强调 / 调度 / 对调保持原读。默认不启用，
# 控制、电子类课程在网页上勾选即可。
DEFAULT_RULES = [
    {'source': '调', 'target': '条', 'cosyvoice': '[t][iáo]', 'note': '控制、电子类课程：调参、电调、超调读 tiáo'},
    {'source': '强调', 'target': '强调', 'note': '“调”读 tiáo 的例外，保持原读'},
    {'source': '调度', 'target': '调度', 'note': '“调”读 tiáo 的例外，保持原读'},
    {'source': '对调', 'target': '对调', 'note': '“调”读 tiáo 的例外，保持原读'},
]


class RuleBook:
    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()

    def _read(self):
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
            return data['rules']
        except FileNotFoundError:
            # 第一次使用：写入内置规则，之后的修改都基于同一批 ID
            now = time.time()
            rules = [{'id': uuid.uuid4().hex[:12], 'enabled': False, 'updated': now, **rule}
                     for rule in DEFAULT_RULES]
            self._write(rules)
            return rules

    def _write(self, rules):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(f'.{self.path.name}.tmp')
        tmp.write_text(json.dumps({'rules': rules}, ensure_ascii=False, indent=1), encoding='utf-8')
        os.replace(tmp, self.path)

    def list(self):
        with self.lock:
            return self._read()

    def add(self, source, target, note=''):
        source, target = check_rule(source, target)
        with self.lock:
            rules = self._read()
            if any(rule['source'] == source for rule in rules):
                raise ValueError(f'已经有“{source}”的规则，请直接修改那一条')
            if len(rules) >= MAX_RULES:
                raise ValueError(f'共用规则最多 {MAX_RULES} 条')
            rule = {'id': uuid.uuid4().hex[:12], 'source': source, 'target': target,
                    'enabled': True, 'note': note.strip()[:100], 'updated': time.time()}
            rules.append(rule)
            self._write(rules)
            return rule

    def update(self, rule_id, **changes):
        with self.lock:
            rules = self._read()
            rule = next((item for item in rules if item['id'] == rule_id), None)
            if rule is None:
                raise KeyError(rule_id)
            if 'source' in changes or 'target' in changes:
                source, target = check_rule(changes.get('source', rule['source']),
                                            changes.get('target', rule['target']))
                if source != rule['source'] and any(item['source'] == source for item in rules):
                    raise ValueError(f'已经有“{source}”的规则，请直接修改那一条')
                if target != rule['target']:
                    # 改了读法，CosyVoice 专用的拼音读法不再适用
                    rule.pop('cosyvoice', None)
                rule.update(source=source, target=target)
            if 'enabled' in changes:
                rule['enabled'] = bool(changes['enabled'])
            rule['updated'] = time.time()
            self._write(rules)
            return rule

    def delete(self, rule_id):
        with self.lock:
            rules = self._read()
            kept = [item for item in rules if item['id'] != rule_id]
            if len(kept) == len(rules):
                raise KeyError(rule_id)
            self._write(kept)

    def effective(self, engine):
        """当前启用的规则 {原词: 该模型的读法}。"""
        return {rule['source']: (rule.get('cosyvoice') if engine == 'cosyvoice' and rule.get('cosyvoice')
                                 else rule['target'])
                for rule in self.list() if rule['enabled']}


def combine(shared, extra_text):
    """共用规则 + 本次临时规则（同一个原词以临时规则为准），展开成规则文本。"""
    from pronunciation import parse_rules
    rules = dict(shared)
    rules.update(parse_rules(extra_text))
    return '\n'.join(f'{source} => {target}' for source, target in rules.items())
