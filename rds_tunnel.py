# -*- coding: utf-8 -*-
"""RDS 로 가는 SSM 포트포워딩 터널을 띄운다/내린다.

왜 터널인가
  `datatool` RDS 는 IP 허용이 아니라 **SSM 을 통해서만** 붙는다(찬님 확인).
  bastion EC2 를 거쳐 로컬 포트를 RDS 3306 에 이어 준다.
      [이 서버] 127.0.0.1:13306  ──SSM──▶ [bastion] ──▶ [RDS]:3306

쓰는 법
    python rds_tunnel.py up      # 터널 띄우고 붙을 때까지 기다린다
    python rds_tunnel.py status  # 지금 떠 있나
    python rds_tunnel.py down    # 내린다

★자격증명·엔드포인트는 전부 `config.json` 에서 읽는다(gitignore 됨).
  이 파일에는 값이 하나도 없다. 화면에도 안 찍는다.
★AWS CLI 는 **사용자 계정에만** 설치돼 있어(관리자 권한 없이 넣었다) PATH 에
  안 잡힐 수 있다 — 경로를 직접 찾아 쓴다.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).parent
CONFIG_FILE = BASE_DIR / "config.json"
PID_FILE = BASE_DIR / "logs" / "rds_tunnel.pid"

# 설치 위치 후보 — 사용자 계정 설치가 먼저다.
AWS_CANDIDATES = [
    Path(os.environ.get("LOCALAPPDATA", "")) / "Programs/Amazon/AWSCLIV2/aws.exe",
    Path(r"C:\Program Files\Amazon\AWSCLIV2\aws.exe"),
]
PLUGIN_DIR = Path(r"C:\Program Files\Amazon\SessionManagerPlugin\bin")


def log(m):
    print(m, flush=True)


def _cfg():
    c = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    rds, aws = c.get("rds") or {}, c.get("aws") or {}
    ssm = rds.get("ssm") or {}
    miss = [k for k in ("instance_id", "remote_host", "remote_port", "local_port")
            if not ssm.get(k)]
    if miss:
        log(f"★ config.json `rds.ssm` 에 빠진 값: {', '.join(miss)}")
        raise SystemExit(2)
    return rds, aws, ssm


def _aws_exe() -> str:
    for p in AWS_CANDIDATES:
        if p.exists():
            return str(p)
    log("★ aws.exe 를 못 찾았어요. AWS CLI v2 설치가 필요합니다.")
    raise SystemExit(2)


def is_up(port: int) -> bool:
    """로컬 포트가 실제로 받아 주는가. PID 파일보다 이게 진실이다."""
    with socket.socket() as s:
        s.settimeout(1.0)
        return s.connect_ex(("127.0.0.1", int(port))) == 0


def up():
    rds, aws, ssm = _cfg()
    lp = int(ssm["local_port"])
    if is_up(lp):
        log(f"이미 떠 있어요 — 127.0.0.1:{lp}")
        return 0

    env = dict(os.environ)
    # 자격증명은 환경변수로만 넘긴다 — ~/.aws 에 파일을 만들지 않는다
    # (자격증명이 두 곳에 흩어지면 어느 게 진짜인지 나중에 못 찾는다).
    if aws.get("access_key_id"):
        env["AWS_ACCESS_KEY_ID"] = aws["access_key_id"]
        env["AWS_SECRET_ACCESS_KEY"] = aws["secret_access_key"]
    env["AWS_DEFAULT_REGION"] = ssm.get("region") or aws.get("region") or "ap-northeast-2"
    env["PATH"] = str(PLUGIN_DIR) + os.pathsep + env.get("PATH", "")

    params = json.dumps({
        "host": [ssm["remote_host"]],
        "portNumber": [str(ssm["remote_port"])],
        "localPortNumber": [str(lp)],
    })
    cmd = [_aws_exe(), "ssm", "start-session",
           "--target", ssm["instance_id"],
           "--document-name", "AWS-StartPortForwardingSessionToRemoteHost",
           "--parameters", params]

    PID_FILE.parent.mkdir(exist_ok=True)
    out = PID_FILE.with_suffix(".log")
    log(f"터널 시작 — 127.0.0.1:{lp} → (bastion) → RDS:{ssm['remote_port']}")
    f = open(out, "w", encoding="utf-8", errors="replace")
    p = subprocess.Popen(cmd, stdout=f, stderr=subprocess.STDOUT, env=env,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    PID_FILE.write_text(str(p.pid), encoding="utf-8")

    for i in range(40):                       # 최대 20초
        if p.poll() is not None:
            log(f"★ 터널이 바로 죽었어요(종료 {p.returncode}). 로그: {out}")
            log(out.read_text(encoding="utf-8", errors="replace")[-800:])
            return 1
        if is_up(lp):
            log(f"열렸어요 (pid {p.pid} · {i * 0.5:.1f}s)")
            return 0
        time.sleep(0.5)
    log(f"★ 20초 안에 안 열렸어요. 로그: {out}")
    log(out.read_text(encoding="utf-8", errors="replace")[-800:])
    return 1


def status():
    _, _, ssm = _cfg()
    lp = int(ssm["local_port"])
    log(f"127.0.0.1:{lp} — {'열림' if is_up(lp) else '닫힘'}")
    return 0 if is_up(lp) else 1


def down():
    if not PID_FILE.exists():
        log("띄운 기록이 없어요.")
        return 0
    pid = PID_FILE.read_text(encoding="utf-8").strip()
    subprocess.run(["taskkill", "/F", "/T", "/PID", pid],
                   capture_output=True, text=True)
    PID_FILE.unlink(missing_ok=True)
    log(f"내렸어요 (pid {pid})")
    return 0


if __name__ == "__main__":
    a = (sys.argv[1] if len(sys.argv) > 1 else "up").lower()
    raise SystemExit({"up": up, "status": status, "down": down}.get(a, up)())
