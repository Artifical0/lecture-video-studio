"""无需模型和显卡：python -m unittest discover -s tests -v。"""
import copy
import importlib.util
import os
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'lvs'))
import config
import script as S
from pronunciation import MAX_RULES, MAX_TEXT, Pronunciation, parse_rules
from rulebook import RuleBook, combine

SCRIPT = '''# 测试课程
## P01｜第一页
### 口播正文
我们开始调参。强调调度，对调参数。
### 讲解提示（不朗读）
我们不朗读这一行。
### 过渡
我们继续！
## P02｜第二页
### 口播正文
我们在2026年学习。
'''
RULES = '调参 => 条参\n我们 => 咱们\n2026年 => 二零二六年'


class PronunciationTests(unittest.TestCase):
    def test_empty_settings_preserve_text(self):
        self.assertEqual(Pronunciation().apply('我们强调调参。'), '我们强调调参。')

    def test_arrows_comments_and_crlf(self):
        self.assertEqual(parse_rules('# 注释\r\n\r\n 我们 → 咱们 \r\n调参=>条参'), {'我们': '咱们', '调参': '条参'})

    def test_longest_match_and_non_cascading(self):
        pron = Pronunciation('调 => 掉\n调参 => 条参\n条参 => 其他')
        self.assertEqual(pron.apply('调参、条参、调。'), '条参、其他、掉。')

    def test_literal_matching_and_case(self):
        pron = Pronunciation('A+B => 诶加比\n.* => 字面量')
        self.assertEqual(pron.apply('A+B a+b .* abc'), '诶加比 a+b 字面量 abc')

    def test_bad_rules(self):
        for text in ['没有分隔符', '原词 =>', '=> 读法', 'a=>b=>c', 'a=>b\na=>c', 'a\tb=>c', 'a' * 121 + '=>b', 'a=>' + 'b' * 301]:
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_rules(text)

    def test_limits(self):
        with self.assertRaises(ValueError):
            parse_rules('x' * (MAX_TEXT + 1))
        with self.assertRaises(ValueError):
            parse_rules('\n'.join(f'词{i}=>读法' for i in range(MAX_RULES + 1)))

    def test_tiao_compatibility_both_engines(self):
        for engine, reading in [('cosyvoice', '[t][iáo]'), ('voxcpm', '条')]:
            with self.subTest(engine=engine):
                self.assertEqual(Pronunciation(engine=engine, tiao=True).apply('强调调参，调度和对调。'), f'强调{reading}参，调度和对调。')

    def test_custom_override_and_protected_replacement(self):
        pron = Pronunciation('调参=>调整参数\n强调=>着重说明', tiao=True)
        self.assertEqual(pron.apply('强调调参和电调。'), '着重说明调整参数和电[t][iáo]。')

    def test_preview_scope_and_original_unchanged(self):
        _, pages = S.parse(SCRIPT)
        before = copy.deepcopy(pages)
        preview = Pronunciation(RULES).preview(pages, start=2, end=2)
        self.assertEqual(preview['changed_sentences'], 1)
        self.assertEqual(preview['examples'], [{
            'page': 2, 'original': '我们在2026年学习。', 'spoken': '咱们在二零二六年学习。',
            'segments': [{'text': '咱们', 'source': '我们', 'kind': 'rule'}, {'text': '在'},
                         {'text': '二零二六年', 'source': '2026年', 'kind': 'rule'}, {'text': '学习。'}],
        }])
        # 第 2 页没有“调参”：这条规则在所选页面没用上
        self.assertEqual(preview['hits'], {'调参': 0, '我们': 1, '2026年': 1})
        self.assertEqual(preview['unused'], ['调参'])
        self.assertEqual(pages, before)

    def test_segments_mark_only_real_changes(self):
        pron = Pronunciation('调参 => 条参', tiao=True, engine='voxcpm')
        segments = pron.segments('强调调参和电调。')
        self.assertEqual(segments, [{'text': '强调'}, {'text': '条参', 'source': '调参', 'kind': 'rule'},
                                    {'text': '和电'}, {'text': '条', 'source': '调', 'kind': 'builtin'},
                                    {'text': '。'}])
        self.assertEqual(''.join(seg['text'] for seg in segments), pron.apply('强调调参和电调。'))

    def test_cosyvoice_markup_is_shown_readably(self):
        [seg] = [s for s in Pronunciation(tiao=True).segments('电调。') if 'source' in s]
        self.assertEqual(seg, {'text': '[t][iáo]', 'source': '调', 'kind': 'builtin', 'display': '调(tiáo)'})

    def test_preview_counts_builtin_and_custom_hits_separately(self):
        _, pages = S.parse(SCRIPT)
        preview = Pronunciation('我们 => 咱们', engine='voxcpm', tiao=True).preview(pages)
        # 只有 P01 的“调参”换成“条”；强调、调度、对调受保护
        self.assertEqual(preview['builtin_hits'], 1)
        self.assertEqual(preview['hits'], {'我们': 3})
        self.assertEqual(preview['unused'], [])

    def test_preview_cap_counts_all_changes(self):
        pages = [{'no': 1, 'paras': ['我们继续。' * 20]}]
        preview = Pronunciation(RULES).preview(pages)
        self.assertEqual(preview['changed_sentences'], 20)
        self.assertEqual(len(preview['examples']), 12)

    def test_sentence_boundaries_match_generation(self):
        _, pages = S.parse(SCRIPT)
        pr = Pronunciation('调参。强调 => 不会跨句匹配').preview(pages)
        self.assertEqual(pr['changed_sentences'], 0)


def fake_tn(text):
    """模拟 wetext：数字改成汉字读法，同时把中文标点换成半角（真实规则也会这样）。"""
    for before, after in [('2026年', '二零二六年'), ('3.7V', '三点七V'), ('5%', '百分之五'), ('-10℃', '负十摄氏度'), ('4', '四')]:
        text = text.replace(before, after)
    return text.replace('，', ',').replace('。', '.')


class NumberReadingTests(unittest.TestCase):
    def test_only_number_spans_are_marked(self):
        pron = Pronunciation(numbers=True, number_reader=fake_tn)
        segments = pron.segments('在2026年，电压3.7V，误差5%。')
        self.assertEqual([s for s in segments if 'source' in s], [
            # 只标出真正变化的字：“年”“V”原样保留
            {'text': '二零二六', 'source': '2026', 'kind': 'number'},
            {'text': '三点七', 'source': '3.7', 'kind': 'number'},
            {'text': '百分之五', 'source': '5%', 'kind': 'number'},
        ])
        # 标点保持原样，不算读法变化
        self.assertEqual(pron.apply('在2026年，电压3.7V，误差5%。'), '在二零二六年，电压三点七V，误差百分之五。')

    def test_sentence_end_period_is_not_confused_with_decimal_point(self):
        # 真实规则把句尾“。”换成“.”；整句比对时它会和 12.5 的小数点错配
        def tn(text):
            return text.replace('12.5m/s', '每秒十二点五米').replace('。', '.')
        pron = Pronunciation(numbers=True, number_reader=tn)
        self.assertEqual(pron.apply('速度12.5m/s。'), '速度每秒十二点五米。')

    def test_adjacent_changes_are_one_span(self):
        segments = Pronunciation(numbers=True, number_reader=fake_tn).segments('温度-10℃。')
        self.assertEqual([s for s in segments if 'source' in s], [
            {'text': '负十摄氏度', 'source': '-10℃', 'kind': 'number'}])

    def test_rules_win_and_their_output_is_not_normalized(self):
        pron = Pronunciation('PX4 => P叉4\n5000mAh => 五千毫安时', numbers=True, number_reader=fake_tn)
        self.assertEqual(pron.apply('PX4飞控用4块5000mAh电池。'), 'P叉4飞控用四块五千毫安时电池。')

    def test_text_without_digits_skips_the_normalizer(self):
        calls = []
        pron = Pronunciation(numbers=True, number_reader=lambda text: calls.append(text) or text)
        self.assertEqual(pron.apply('没有数字。'), '没有数字。')
        self.assertEqual(calls, [])

    def test_switched_off_or_unavailable(self):
        self.assertEqual(Pronunciation(number_reader=fake_tn).apply('2026年。'), '2026年。')
        with patch('number_reading.available', return_value=False):
            self.assertFalse(Pronunciation(numbers=True).numbers)

    def test_preview_counts_number_spans(self):
        _, pages = S.parse(SCRIPT)
        preview = Pronunciation('我们 => 咱们', numbers=True, number_reader=fake_tn).preview(pages)
        self.assertEqual(preview['number_hits'], 1)
        self.assertEqual(preview['examples'][-1]['spoken'], '咱们在二零二六年学习。')


class RuleBookTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.book = RuleBook(Path(self.temp.name) / 'rules.json')

    def tearDown(self):
        self.temp.cleanup()

    def test_tiao_rules_start_disabled_and_use_engine_specific_reading(self):
        self.assertEqual([r['source'] for r in self.book.list()], ['调', '强调', '调度', '对调'])
        self.assertEqual(self.book.effective('cosyvoice'), {})
        for rule in self.book.list():
            self.book.update(rule['id'], enabled=True)
        self.assertEqual(self.book.effective('cosyvoice')['调'], '[t][iáo]')
        self.assertEqual(self.book.effective('voxcpm'), {'调': '条', '强调': '强调', '调度': '调度', '对调': '对调'})
        text = combine(self.book.effective('voxcpm'), '')
        self.assertEqual(Pronunciation(text).apply('强调调参，调度和对调。'), '强调条参，调度和对调。')

    def test_add_edit_toggle_delete(self):
        rule = self.book.add(' PX4 ', 'P叉4')
        self.assertEqual((rule['source'], rule['target'], rule['enabled']), ('PX4', 'P叉4', True))
        with self.assertRaises(ValueError):
            self.book.add('PX4', '别的读法')
        for bad in [('', 'x'), ('a=>b', 'c'), ('a', 'b→c'), ('a\tb', 'c')]:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.book.add(*bad)
        self.book.update(rule['id'], enabled=False)
        self.assertNotIn('PX4', self.book.effective('voxcpm'))
        tiao = self.book.list()[0]
        edited = self.book.update(tiao['id'], target='掉')
        # 改了读法后 CosyVoice 也用新读法
        self.assertNotIn('cosyvoice', edited)
        self.book.delete(rule['id'])
        self.assertNotIn('PX4', [r['source'] for r in self.book.list()])
        with self.assertRaises(KeyError):
            self.book.delete(rule['id'])
        # 规则保存在文件里，换个实例读出来一样
        self.assertEqual(RuleBook(self.book.path).list(), self.book.list())

    def test_temporary_rules_override_shared_ones(self):
        self.book.add('PX4', 'P叉4')
        text = combine(self.book.effective('voxcpm'), 'PX4 => 皮埃克斯四\n我们 => 咱们')
        self.assertEqual(parse_rules(text), {'PX4': '皮埃克斯四', '我们': '咱们'})


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient
        cls.temp = tempfile.TemporaryDirectory()
        cls.env_patch = patch.dict(os.environ, {'LVS_DATA': cls.temp.name, 'LVS_PASSWORD': 'test-only', 'LVS_SECRET': 'test-secret'})
        cls.env_patch.start()
        cls.data_patch = patch.object(config, 'DATA', Path(cls.temp.name))
        cls.data_patch.start()
        spec = importlib.util.spec_from_file_location('lvs_test_app', ROOT / 'web/app.py')
        cls.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.mod)
        # 不进入 lifespan，不启动 GPU 工作线程；通过真实登录中间件访问 API。
        cls.client = TestClient(cls.mod.app)
        cls.client.post('/login', data={'password': 'test-only'})

    @classmethod
    def tearDownClass(cls):
        cls.client.close()
        cls.data_patch.stop()
        cls.env_patch.stop()
        cls.temp.cleanup()

    def check(self, **data):
        return self.client.post('/api/check', files={'script': ('讲稿.md', SCRIPT.encode(), 'text/markdown')}, data=data)

    def add(self, rules=RULES, **data):
        with patch.object(self.mod, 'voice_files', return_value=('ref.wav', 'ref.txt')), patch.object(self.mod, 'list_voices', return_value=[{'id': 'test', 'name': '测试音色'}]):
            return self.client.post('/api/jobs', files={'script': ('讲稿.md', SCRIPT.encode(), 'text/markdown'), 'ppt': ('课件.pptx', b'test')},
                                    data={'voice': 'test', 'pronunciation': rules, **data})

    def test_auth_required(self):
        from fastapi.testclient import TestClient
        client = TestClient(self.mod.app)
        try:
            self.assertEqual(client.post('/api/check').status_code, 401)
        finally:
            client.close()

    def test_preview_and_page_range(self):
        r = self.check(pronunciation=RULES, start=2, end=2, engine='voxcpm')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()['pronunciation']['examples'][0]['spoken'], '咱们在二零二六年学习。')
        self.assertEqual(r.json()['errors'], [])

    def test_invalid_rule_in_check(self):
        r = self.check(pronunciation='我们=>')
        self.assertEqual(r.status_code, 400)
        self.assertIn('第 1 行', r.json()['detail'])

    def test_invalid_rule_in_job_creates_no_directory(self):
        before = set(self.mod.JOBS.iterdir())
        self.assertEqual(self.add('我们=>').status_code, 400)
        self.assertEqual(set(self.mod.JOBS.iterdir()), before)

    def test_new_job_snapshot_and_retry(self):
        r = self.add(engine='voxcpm', speed=0.9)
        self.assertEqual(r.status_code, 200, r.text)
        job = r.json()
        self.assertEqual(job['pronunciation'], RULES)
        self.assertEqual(job['pronunciation_preview']['rule_count'], 3)
        self.assertEqual((self.mod.JOBS / job['id'] / 'script.md').read_text(encoding='utf8'), SCRIPT)
        self.mod.update(job['id'], status='failed')
        with patch.object(self.mod, 'voice_files'):
            retry = self.client.post(f'/api/jobs/{job["id"]}/retry')
        self.assertEqual(retry.status_code, 200)
        self.assertEqual(retry.json()['pronunciation'], RULES)
        self.assertEqual(retry.json()['pronunciation_preview'], job['pronunciation_preview'])

    def test_legacy_job_without_new_field_still_retries(self):
        job = self.add('').json()
        del job['pronunciation'], job['pronunciation_preview']
        job['status'] = 'canceled'
        self.mod.save(job)
        with patch.object(self.mod, 'voice_files'):
            r = self.client.post(f'/api/jobs/{job["id"]}/retry')
        self.assertEqual(r.status_code, 200)
        self.assertNotIn('pronunciation', r.json())

    def test_number_reading_is_saved_with_the_job_and_forwarded(self):
        with patch('number_reading.available', return_value=True):
            job = self.add('', numbers='yes').json()
        self.assertTrue(job['numbers'])
        process = types.SimpleNamespace(stdout=iter([]), wait=lambda: 1)
        with patch.object(self.mod, 'prepare_images'), patch.object(self.mod, 'voice_files', return_value=('ref.wav', 'ref.txt')), patch.object(self.mod.subprocess, 'Popen', return_value=process) as popen:
            self.mod.run_job(job)
        self.assertIn('--numbers', popen.call_args.args[0])
        with patch('number_reading.available', return_value=False):
            self.assertFalse(self.add('', numbers='yes').json()['numbers'])

    def test_shared_rules_api_and_job_snapshot(self):
        rules = self.client.get('/api/rules').json()['rules']
        for rule in rules:
            self.client.patch(f"/api/rules/{rule['id']}", data={'enabled': 'yes'})
        added = self.client.post('/api/rules', data={'source': '继续', 'target': '接着'})
        self.assertEqual(added.status_code, 200, added.text)
        self.assertEqual(self.client.post('/api/rules', data={'source': '继续', 'target': 'x'}).status_code, 400)
        try:
            check = self.check(pronunciation='我们 => 咱们\n不存在 => 没有', engine='voxcpm').json()['pronunciation']
            # 共用规则只报命中次数，没用上的只提示临时规则
            self.assertEqual(check['unused'], ['不存在'])
            self.assertEqual(check['hits']['继续'], 1)
            self.assertEqual(check['hits']['调'], 1)
            job = self.add('我们 => 咱们', engine='voxcpm', speed=0.9).json()
            self.assertIn('继续 => 接着', job['pronunciation'])
            self.assertIn('调 => 条', job['pronunciation'])
            self.assertEqual(job['pronunciation_extra'], '我们 => 咱们')
            # 之后删掉共用规则，已提交任务的重试仍用提交时的规则
            self.client.delete(f"/api/rules/{added.json()['id']}")
            self.mod.update(job['id'], status='failed')
            with patch.object(self.mod, 'voice_files'):
                retry = self.client.post(f'/api/jobs/{job["id"]}/retry').json()
            self.assertIn('继续 => 接着', retry['pronunciation'])
            self.assertEqual(self.client.patch('/api/rules/nope', data={'enabled': 'yes'}).status_code, 404)
        finally:
            for rule in self.client.get('/api/rules').json()['rules']:
                if rule['source'] == '继续':
                    self.client.delete(f"/api/rules/{rule['id']}")
                else:
                    self.client.patch(f"/api/rules/{rule['id']}", data={'enabled': 'no'})

    def test_worker_writes_snapshot_and_forwards_cli_argument(self):
        for legacy in (False, True):
            with self.subTest(legacy=legacy):
                job = self.add().json()
                if legacy:
                    del job['pronunciation'], job['pronunciation_preview']
                process = types.SimpleNamespace(stdout=iter([]), wait=lambda: 1)
                with patch.object(self.mod, 'prepare_images'), patch.object(self.mod, 'voice_files', return_value=('ref.wav', 'ref.txt')), patch.object(self.mod.subprocess, 'Popen', return_value=process) as popen:
                    self.mod.run_job(job)
                cmd = popen.call_args.args[0]
                if legacy:
                    self.assertNotIn('--pronunciation', cmd)
                else:
                    file = Path(cmd[cmd.index('--pronunciation') + 1])
                    self.assertEqual(file.read_text(encoding='utf8'), RULES)


class Tensor:
    """只模拟音频时长；不模拟模型输出或评价发音效果。"""
    def __init__(self, samples): self.shape = (1, samples)
    def abs(self): return self
    def max(self): return self
    def item(self): return 0.5
    def __truediv__(self, value): return self
    def float(self): return self
    def unsqueeze(self, dim): return self


class GenerationTests(unittest.TestCase):
    def test_both_engines_receive_modified_text_but_srt_keeps_original(self):
        import burn
        for engine in ('cosyvoice', 'voxcpm'):
            with self.subTest(engine=engine), tempfile.TemporaryDirectory() as td:
                d = Path(td)
                (d / 'script.md').write_text(SCRIPT, encoding='utf8')
                (d / 'rules.txt').write_text(RULES, encoding='utf8')
                (d / 'voice.txt').write_text('参考文字', encoding='utf8')
                for no in (1, 2): (d / f'p-{no:02d}.png').touch()
                spoken = []

                class Model:
                    sample_rate = 1000
                    tts_model = types.SimpleNamespace(sample_rate=1000)
                    def __init__(self, **kw): pass
                    def add_zero_shot_spk(self, *args): return True
                    def inference_zero_shot(self, text, *args, **kw):
                        spoken.append(text)
                        yield {'tts_speech': Tensor(len(text) * 100)}
                    @classmethod
                    def from_pretrained(cls, *args, **kw): return cls()
                    def generate(self, text, **kw):
                        spoken.append(text)
                        return Tensor(len(text) * 100)

                torch = types.SimpleNamespace(zeros=lambda *shape: Tensor(shape[-1]), cat=lambda ws, dim: Tensor(sum(w.shape[1] for w in ws)), from_numpy=lambda w: w)
                modules = {'torch': torch, 'torchaudio': types.SimpleNamespace(save=lambda *args: None),
                           'cosyvoice.cli.cosyvoice': types.SimpleNamespace(AutoModel=Model), 'voxcpm': types.SimpleNamespace(VoxCPM=Model)}
                args = ['make_video.py', '--script', str(d / 'script.md'), '--img-dir', str(d), '--voice-wav', 'ref.wav', '--voice-text', str(d / 'voice.txt'),
                        '--out-dir', str(d / 'out'), '--out', 'test', '--subs', 'none', '--speed', '1', '--engine', engine, '--pronunciation', str(d / 'rules.txt')]
                with patch.dict(sys.modules, modules), patch.object(sys, 'argv', args), patch.object(config, 'PY_VOXCPM', str(Path(sys.prefix) / 'bin/python')), patch.object(subprocess, 'run'), patch.dict(os.environ):
                    runpy.run_path(str(ROOT / 'lvs/make_video.py'), run_name='__main__')
                cues = burn.parse_srt((d / 'out/test.srt').read_text(encoding='utf8'))
                self.assertEqual([c[2] for c in cues], ['我们开始调参。', '强调调度，对调参数。', '我们继续！', '我们在2026年学习。'])
                self.assertEqual(spoken, ['咱们开始条参。', '强调调度，对条参数。', '咱们继续！', '咱们在二零二六年学习。'])
                self.assertEqual([round(e - b, 2) for b, e, _ in cues], [round(len(s) / 10, 2) for s in spoken])


if __name__ == '__main__':
    unittest.main()
