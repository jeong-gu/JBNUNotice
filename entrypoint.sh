#!/bin/bash
set -euo pipefail

python3 - <<'PY'
import os, shlex, pathlib
path = pathlib.Path("/app/cron_env.sh")
with path.open("w", encoding="utf-8") as f:
    f.write("#!/bin/sh\n")
    for key, value in os.environ.items():
        if key in {"PWD", "OLDPWD", "SHLVL", "_"}:
            continue
        f.write(f"export {key}={shlex.quote(value)}\n")
path.chmod(0o755)
PY

# 1. crontab 설정 파일을 시스템 crontab에 등록합니다.
crontab /app/jbnunotice.cron

# 2. cron 데몬을 포그라운드로 실행합니다. 
# Docker 컨테이너는 CMD/ENTRYPOINT로 실행되는 메인 프로세스가 종료되면 컨테이너도 종료되므로,
# cron을 -f (포그라운드) 옵션으로 실행하여 컨테이너가 계속 유지되도록 합니다.
exec cron -f
