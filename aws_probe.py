# -*- coding: utf-8 -*-
"""받은 AWS 키로 **무엇이 열려 있는지** 조회한다 (읽기 전용).

왜 필요한가
  액세스키·시크릿키만 받았는데, 그건 **터널을 뚫는 열쇠**이지
  MySQL 로그인 정보가 아니다. 그래서 아직 모르는 게 이만큼 있다:
      · 어느 bastion(EC2)으로 터널을 뚫나
      · RDS 엔드포인트·포트가 무엇인가
      · DB 계정(id/pw)은 무엇인가
  앞의 둘은 **이 키로 직접 조회**할 수 있다. 물어볼 필요가 없다.

무엇을 부르나 (전부 조회 계열 · 만들거나 지우는 호출은 하나도 없다)
  sts:GetCallerIdentity · ssm:DescribeInstanceInformation
  rds:DescribeDBInstances / DescribeDBClusters · ec2:DescribeInstances

쓰는 법
    python aws_probe.py

★키는 `config.json` 의 `aws` 에만 둔다(gitignore 됨).
  이 스크립트는 **시크릿을 절대 찍지 않는다.** 액세스키도 앞 4자만 보여 준다.
"""
from __future__ import annotations

import json
from pathlib import Path

BASE_DIR = Path(__file__).parent
CONFIG_FILE = BASE_DIR / "config.json"
DEFAULT_REGION = "ap-northeast-2"        # 퀵사이트 계정과 같은 리전

SHAPE = """
  "aws": {
    "access_key_id": "AKIA…",
    "secret_access_key": "…",
    "region": "ap-northeast-2"
  }
"""


def log(m):
    print(m, flush=True)


def load_aws():
    if not CONFIG_FILE.exists():
        log("★ config.json 이 없어요.")
        raise SystemExit(2)
    cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    a = cfg.get("aws")
    if not a:
        log("★ config.json 에 `aws` 설정이 없어요. 아래 모양으로 넣어 주세요:")
        log(SHAPE)
        log("  ※ config.json 은 gitignore 되어 있어 커밋되지 않아요.")
        raise SystemExit(2)
    miss = [k for k in ("access_key_id", "secret_access_key")
            if not str(a.get(k, "")).strip()]
    if miss:
        log(f"★ `aws` 에 빠진 값: {', '.join(miss)}")
        log(SHAPE)
        raise SystemExit(2)
    return a


def main():
    import boto3
    from botocore.exceptions import ClientError, EndpointConnectionError

    a = load_aws()
    region = a.get("region") or DEFAULT_REGION
    ak = str(a["access_key_id"])
    log(f"키 {ak[:4]}…({len(ak)}자) · 리전 {region}\n")

    s = boto3.session.Session(
        aws_access_key_id=a["access_key_id"],
        aws_secret_access_key=a["secret_access_key"],
        region_name=region)

    def sect(t):
        log("\n" + "=" * 64)
        log(t)
        log("=" * 64)

    # ── 1. 나는 누구인가 ───────────────────────────────────────────
    sect("1) 이 키의 신원")
    try:
        me = s.client("sts").get_caller_identity()
        log(f"  계정  {me['Account']}")
        log(f"  ARN   {me['Arn']}")
    except Exception as e:                                   # noqa: BLE001
        log(f"  ★ 실패: {type(e).__name__}: {str(e)[:160]}")
        log("  → 키가 잘못됐거나 만료됐을 수 있어요.")
        raise SystemExit(1)

    # ── 2. SSM 으로 붙을 수 있는 서버 ──────────────────────────────
    sect("2) SSM 으로 붙을 수 있는 서버 (터널 후보)")
    try:
        inst = s.client("ssm").describe_instance_information(
            MaxResults=50).get("InstanceInformationList", [])
        if not inst:
            log("  (보이는 서버 없음 — ssm:DescribeInstanceInformation 권한이 없거나"
                " 등록된 서버가 없어요)")
        for i in inst:
            log(f"  {i.get('InstanceId')}  {i.get('PlatformName', '')}"
                f" {i.get('PlatformVersion', '')} · {i.get('PingStatus')}"
                f" · {i.get('IPAddress', '')}")
    except ClientError as e:
        log(f"  ★ 권한 없음/실패: {e.response['Error']['Code']}")
    except (EndpointConnectionError, Exception) as e:        # noqa: BLE001
        log(f"  ★ {type(e).__name__}: {str(e)[:140]}")

    # ── 3. RDS 엔드포인트 ─────────────────────────────────────────
    sect("3) RDS 엔드포인트")
    found = False
    try:
        for db in s.client("rds").describe_db_instances().get("DBInstances", []):
            found = True
            ep = db.get("Endpoint") or {}
            log(f"  {db.get('DBInstanceIdentifier')} · {db.get('Engine')}"
                f" {db.get('EngineVersion')}")
            log(f"     주소 {ep.get('Address')}:{ep.get('Port')}")
            log(f"     공개접근 {db.get('PubliclyAccessible')} · 상태 {db.get('DBInstanceStatus')}")
            log(f"     기본DB {db.get('DBName')} · 마스터계정 {db.get('MasterUsername')}")
    except ClientError as e:
        log(f"  ★ 권한 없음/실패: {e.response['Error']['Code']}")
    except Exception as e:                                   # noqa: BLE001
        log(f"  ★ {type(e).__name__}: {str(e)[:140]}")
    if not found:
        log("  (인스턴스가 안 보여요 — 클러스터일 수 있어 아래도 봅니다)")
        try:
            for c in s.client("rds").describe_db_clusters().get("DBClusters", []):
                log(f"  [클러스터] {c.get('DBClusterIdentifier')} · {c.get('Engine')}")
                log(f"     writer {c.get('Endpoint')}:{c.get('Port')}")
                log(f"     reader {c.get('ReaderEndpoint')}")
        except ClientError as e:
            log(f"  ★ 권한 없음/실패: {e.response['Error']['Code']}")
        except Exception as e:                               # noqa: BLE001
            log(f"  ★ {type(e).__name__}: {str(e)[:140]}")

    log("\n끝 — 조회만 했어요(만들거나 지운 것 없음).")
    log("다음 단계는 결과를 보고 정합니다: 직결이 되면 rds_probe.py 로 바로,"
        " 터널이 필요하면 AWS CLI + session-manager-plugin 설치부터.")


if __name__ == "__main__":
    main()
