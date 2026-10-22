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
        self.assertEqual(preview['examples'], [{'page': 2, 'original': '我们在2026年学习。', 'spoken': '咱们在二零二六年学习。'}])
        self.assertEqual(pages, before)

    def test_preview_cap_counts_all_changes(self):
        pages = [{'no': 1, 'paras': ['我们继续。' * 20]}]
        preview = Pronunciation(RULES).preview(pages)
        self.assertEqual(preview['changed_sentences'], 20)
        self.assertEqual(len(preview['examples']), 12)

    def test_sentence_boundaries_match_generation(self):
        _, pages = S.parse(SCRIPT)
        pr = Pronunciation('调参。强调 => 不会跨句匹配').preview(pages)
        self.assertEqual(pr['changed_sentences'], 0)


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
