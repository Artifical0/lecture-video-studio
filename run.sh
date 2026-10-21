#!/usr/bin/env bash
# 网页服务：./run.sh [start|restart|stop|status]
# 开机自启：crontab -e 加一行  @reboot /path/to/lecture-video-studio/run.sh start
set -e
cd "$(dirname "$(readlink -f "$0")")"
[ -f .env ] || { echo "缺少 .env：cp .env.example .env 并修改"; exit 1; }
set -a; . ./.env; set +a
PY=${LVS_PY_MAIN:-envs/main/bin/python}
DATA=${LVS_DATA:-data}; mkdir -p "$DATA"
PIDF=$DATA/server.pid
running() { [ -f "$PIDF" ] && grep -qs uvicorn "/proc/$(cat "$PIDF")/cmdline"; }
stop() { if running; then kill "$(cat "$PIDF")"; sleep 2; fi; rm -f "$PIDF"; }
case "${1:-start}" in
  stop) stop; echo "已停止" ;;
  status) running && echo "运行中 (pid $(cat "$PIDF"))，http://${LVS_HOST:-0.0.0.0}:${LVS_PORT:-8095}" || echo "未运行" ;;
  start|restart)
    [ "$1" = restart ] && stop
    if running; then echo "已在运行 (pid $(cat "$PIDF"))"; exit 0; fi
    export PATH="${LVS_FFMPEG_DIR:-$(dirname "$PY")}:$PATH"
    setsid nohup "$PY" -m uvicorn app:app --app-dir web --host "${LVS_HOST:-0.0.0.0}" --port "${LVS_PORT:-8095}" \
      --no-access-log >> "$DATA/server.log" 2>&1 < /dev/null &
    echo $! > "$PIDF"; sleep 3
    running && echo "已启动 (pid $!)：http://${LVS_HOST:-0.0.0.0}:${LVS_PORT:-8095}" || { echo "启动失败，看 $DATA/server.log"; tail -5 "$DATA/server.log"; exit 1; } ;;
  *) echo "用法: $0 [start|restart|stop|status]"; exit 1 ;;
esac
