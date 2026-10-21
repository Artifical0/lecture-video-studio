"""统一配置：读取仓库根目录的 .env（KEY=VALUE），环境变量优先。只用标准库，任何 Python 环境都能 import。"""
import os, re, pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent

def _load_env():
    env = {}
    f = pathlib.Path(os.environ.get('LVS_ENV_FILE', ROOT / '.env'))
    if f.exists():
        for line in f.read_text(encoding='utf-8').splitlines():
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                k, v = line.split('=', 1)
                v = re.split(r'\s+#', v, 1)[0]          # 行尾注释
                env[k.strip()] = os.path.expanduser(os.path.expandvars(v.strip().strip('"').strip("'")))
    return env

_ENV = _load_env()

def get(key, default=''):
    return os.environ.get(key) or _ENV.get(key) or default

def path(key, default):
    return pathlib.Path(os.path.expanduser(get(key, str(default))))

MODELS = path('LVS_MODELS', ROOT / 'models')
DATA = path('LVS_DATA', ROOT / 'data')
COSYVOICE_DIR = path('LVS_COSYVOICE_DIR', ROOT / 'third_party/CosyVoice')
COSYVOICE_MODEL = path('LVS_COSYVOICE_MODEL', MODELS / 'Fun-CosyVoice3-0.5B')
VOXCPM_MODEL = path('LVS_VOXCPM_MODEL', MODELS / 'VoxCPM2')
ASR_MODEL = path('LVS_ASR_MODEL', MODELS / 'Qwen3-ASR-1.7B')
ALIGNER_MODEL = path('LVS_ALIGNER_MODEL', MODELS / 'Qwen3-ForcedAligner-0.6B')
# 三个 Python 环境：CosyVoice 主环境（也跑网页）、VoxCPM2、Qwen3-ASR。后两个是建在主环境之上的 venv
PY_MAIN = get('LVS_PY_MAIN', str(ROOT / 'envs/main/bin/python'))
PY_VOXCPM = get('LVS_PY_VOXCPM', str(ROOT / 'envs/voxcpm/bin/python'))
PY_ASR = get('LVS_PY_ASR', str(ROOT / 'envs/asr/bin/python'))
FFMPEG_DIR = get('LVS_FFMPEG_DIR', str(pathlib.Path(PY_MAIN).parent))   # 需要带 libass 的 ffmpeg
# 内置音色（可选）：一段参考录音 + 对应文字
BUILTIN_VOICE_NAME = get('LVS_BUILTIN_VOICE_NAME', '')
BUILTIN_VOICE_WAV = get('LVS_BUILTIN_VOICE_WAV', '')
BUILTIN_VOICE_TXT = get('LVS_BUILTIN_VOICE_TXT', '')

def subprocess_env():
    """子进程环境：把 ffmpeg 所在目录放进 PATH。"""
    e = dict(os.environ)
    e['PATH'] = FFMPEG_DIR + os.pathsep + e.get('PATH', '')
    e.setdefault('TRANSFORMERS_VERBOSITY', 'error')
    return e
