#!/usr/bin/env bash
# 一键安装：三个 Python 环境 + 四个模型（约 15GB）。Linux + NVIDIA 显卡（≥ 10GB 显存，驱动支持 CUDA 12.8）。
#   ./install.sh            # 默认用官方源
#   ./install.sh --cn       # 国内镜像（清华 conda / 阿里云 pip）；模型始终从 ModelScope 下载
#   ./install.sh --skip-models
# 需要先装好：conda（Miniconda 即可）、git，以及系统包 poppler-utils（pdftoppm）、fonts-noto-cjk（字幕字体）。
# 可选：libreoffice（直接上传 .pptx 时用）。
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"
ROOT=$PWD
CN=0; SKIP_MODELS=0
for a in "$@"; do case $a in --cn) CN=1 ;; --skip-models) SKIP_MODELS=1 ;; *) echo "未知参数 $a"; exit 1 ;; esac; done

say() { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }
need() { command -v "$1" >/dev/null || { echo "缺少 $1：$2"; exit 1; }; }
need conda "安装 Miniconda：https://docs.conda.io/en/latest/miniconda.html"
need git "sudo apt install git"
need nvidia-smi "需要 NVIDIA 驱动"
command -v pdftoppm >/dev/null || echo "提示：缺少 pdftoppm，请 sudo apt install poppler-utils"
fc-list :lang=zh | grep -qi "noto sans cjk" || echo "提示：缺少中文字体，字幕会显示方框，请 sudo apt install fonts-noto-cjk"
command -v soffice >/dev/null || echo "提示：没装 LibreOffice，只能上传 PDF（sudo apt install libreoffice-impress 可支持 .pptx）"

if [ $CN = 1 ]; then
  CONDA_MAIN=https://mirrors.tuna.tsinghua.edu.cn/anaconda/pkgs/main
  CONDA_FORGE=https://mirrors.tuna.tsinghua.edu.cn/anaconda/cloud/conda-forge
  PIP_INDEX=(-i https://mirrors.aliyun.com/pypi/simple/)
else
  CONDA_MAIN=defaults; CONDA_FORGE=conda-forge; PIP_INDEX=()
fi
source "$(conda info --base)/etc/profile.d/conda.sh"

say "1/5 主环境 envs/main（Python 3.10 + torch 2.7.1 cu128 + ffmpeg + CosyVoice3 + 网页）"
MAIN=$ROOT/envs/main
[ -x "$MAIN/bin/python" ] || conda create -y -p "$MAIN" --override-channels -c "$CONDA_MAIN" python=3.10
conda activate "$MAIN"
conda install -y --override-channels -c "$CONDA_FORGE" ffmpeg sox     # conda-forge 的 ffmpeg 带 libass，烧字幕要用
pip install torch==2.7.1 torchaudio==2.7.1 --index-url https://download.pytorch.org/whl/cu128
if [ ! -d third_party/CosyVoice ]; then
  git clone --recursive https://github.com/FunAudioLLM/CosyVoice.git third_party/CosyVoice
  git -C third_party/CosyVoice checkout 074ca6dc9e80a2f424f1f74b48bdd7d3fea531cc   # 测试过的版本
  git -C third_party/CosyVoice submodule update --init --recursive
fi
grep -v -E '^(--extra-index-url|torch|torchaudio|deepspeed|tensorrt|onnxruntime)' third_party/CosyVoice/requirements.txt > envs/cosyvoice-req.txt
pip install "${PIP_INDEX[@]}" -r envs/cosyvoice-req.txt onnxruntime-gpu==1.22.0 fastapi==0.115.6 uvicorn==0.30.0 python-multipart pillow
conda deactivate

PIN=$ROOT/envs/torch-pin.txt; printf 'torch==2.7.1\ntorchaudio==2.7.1\n' > "$PIN"
say "2/5 VoxCPM2 环境 envs/voxcpm（建在主环境之上，共用 torch）"
[ -x envs/voxcpm/bin/python ] || "$MAIN/bin/python" -m venv --system-site-packages envs/voxcpm
envs/voxcpm/bin/pip install "${PIP_INDEX[@]}" -c "$PIN" voxcpm "torchcodec==0.5.*"

say "3/5 语音识别环境 envs/asr（Qwen3-ASR）"
[ -x envs/asr/bin/python ] || "$MAIN/bin/python" -m venv --system-site-packages envs/asr
envs/asr/bin/pip install "${PIP_INDEX[@]}" -c "$PIN" qwen-asr

if [ $SKIP_MODELS = 0 ]; then
  say "4/5 下载模型（ModelScope，约 14GB）"
  for m in FunAudioLLM/Fun-CosyVoice3-0.5B-2512:Fun-CosyVoice3-0.5B OpenBMB/VoxCPM2:VoxCPM2 \
           Qwen/Qwen3-ASR-1.7B:Qwen3-ASR-1.7B Qwen/Qwen3-ForcedAligner-0.6B:Qwen3-ForcedAligner-0.6B; do
    "$MAIN/bin/modelscope" download --model "${m%%:*}" --local_dir "models/${m##*:}"
  done
fi

# 数字和单位按中文读的规则文件（WeTextProcessing 包里预编译的中文 TN FST，从 PyPI 取，几 MB）
if [ ! -f models/wetext-zh-tn/zh_tn_verbalizer.fst ]; then
  say "数字读法规则 models/wetext-zh-tn"
  tmp=$(mktemp -d)
  "$MAIN/bin/pip" download "${PIP_INDEX[@]}" --no-deps --only-binary :all: -d "$tmp" WeTextProcessing==1.2.0
  mkdir -p models/wetext-zh-tn
  "$MAIN/bin/python" - "$tmp" <<'PY'
import glob, sys, zipfile
with zipfile.ZipFile(glob.glob(sys.argv[1] + '/*.whl')[0]) as wheel:
    for name in ('zh_tn_tagger.fst', 'zh_tn_verbalizer.fst'):
        open(f'models/wetext-zh-tn/{name}', 'wb').write(wheel.read(f'tn/{name}'))
PY
  rm -rf "$tmp"
fi

say "5/5 配置"
if [ ! -f .env ]; then
  cp .env.example .env
  pw=$("$MAIN/bin/python" -c "import secrets;print(secrets.token_urlsafe(9))")
  sec=$("$MAIN/bin/python" -c "import secrets;print(secrets.token_hex(32))")
  sed -i "s|^LVS_PASSWORD=.*|LVS_PASSWORD=$pw|; s|^LVS_SECRET=.*|LVS_SECRET=$sec|" .env
  chmod 600 .env
  echo "已生成 .env，访问密码：$pw（可在 .env 里修改）"
fi
"$MAIN/bin/python" - <<'PY'
import torch; print('GPU:', torch.cuda.get_device_name(0), '| torch', torch.__version__, '| CUDA 可用:', torch.cuda.is_available())
PY
say "完成。启动：./run.sh start"
