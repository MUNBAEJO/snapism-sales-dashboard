# -*- coding: utf-8 -*-
"""이 AWS 키가 **무엇을 할 수 있는지** 넓게 훑는다 (전부 읽기 전용).

왜
  받은 키(`seobuk-datatool-user`)는 STS 는 되는데 SSM·RDS 조회가 다 막혀 있다.
  그럼 이 키는 **무슨 용도로 만들어진 것인가**? 그걸 알아야
  "무엇을 더 달라" 고 정확히 요청할 수 있다. 찍어서 묻지 않는다.

무엇을 부르나 — **List/Get/Describe 계열만.** 만들거나 지우거나 바꾸는 호출 없음.
  자격증명이 Parameter Store / Secrets Manager 에 들어 있는 경우가 흔해서 거기부터 본다.

    python aws_perm_probe.py
"""
from __future__ import annotations

import json
from pathlib import Path

CONFIG_FILE = Path(__file__).parent / "config.json"


def log(m):
    print(m, flush=True)


def sess():
    import boto3
    a = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))["aws"]
    return boto3.session.Session(
        aws_access_key_id=a["access_key_id"],
        aws_secret_access_key=a["secret_access_key"],
        region_name=a.get("region") or "ap-northeast-2")


# (서비스, 호출, 인자, 결과에서 뽑을 것)
CHECKS = [
    ("ssm", "describe_parameters", {"MaxResults": 50},
     lambda r: [p["Name"] for p in r.get("Parameters", [])]),
    ("secretsmanager", "list_secrets", {"MaxResults": 50},
     lambda r: [s["Name"] for s in r.get("SecretList", [])]),
    ("rds", "describe_db_subnet_groups", {},
     lambda r: [g["DBSubnetGroupName"] for g in r.get("DBSubnetGroups", [])]),
    ("ec2", "describe_instances", {"MaxResults": 20},
     lambda r: [i["InstanceId"] for res in r.get("Reservations", [])
                for i in res.get("Instances", [])]),
    ("s3", "list_buckets", {}, lambda r: [b["Name"] for b in r.get("Buckets", [])]),
    ("athena", "list_work_groups", {},
     lambda r: [w["Name"] for w in r.get("WorkGroups", [])]),
    ("glue", "get_databases", {}, lambda r: [d["Name"] for d in r.get("DatabaseList", [])]),
    ("quicksight", "list_data_sources", {"AwsAccountId": "334132305479"},
     lambda r: [d.get("Name") for d in r.get("DataSources", [])]),
    ("iam", "get_user", {}, lambda r: [r["User"]["UserName"]]),
    ("iam", "list_attached_user_policies", {"UserName": "seobuk-datatool-user"},
     lambda r: [p["PolicyName"] for p in r.get("AttachedPolicies", [])]),
    ("iam", "list_user_policies", {"UserName": "seobuk-datatool-user"},
     lambda r: r.get("PolicyNames", [])),
    ("sts", "get_caller_identity", {}, lambda r: [r["Arn"]]),
]


def main():
    from botocore.exceptions import ClientError

    s = sess()
    log(f"{'서비스':<16}{'호출':<34}결과")
    log("-" * 92)
    ok = []
    for svc, op, kw, pick in CHECKS:
        try:
            c = s.client(svc)
            r = getattr(c, op)(**kw)
            vals = pick(r) or []
            ok.append((svc, op, vals))
            shown = ", ".join(str(v) for v in vals[:6]) or "(빈 목록)"
            log(f"  ✅ {svc:<13}{op:<34}{shown[:44]}")
            if len(vals) > 6:
                log(f"      … 외 {len(vals)-6}개")
        except ClientError as e:
            code = e.response["Error"]["Code"]
            log(f"  ✗  {svc:<13}{op:<34}{code}")
        except Exception as e:                               # noqa: BLE001
            log(f"  ✗  {svc:<13}{op:<34}{type(e).__name__}: {str(e)[:34]}")

    log("\n" + "=" * 92)
    log(f"쓸 수 있는 호출 {len(ok)}개")
    log("=" * 92)
    if not ok or all(o[0] == "sts" for o in ok):
        log("  STS 말고는 전부 막혔어요.")
        log("  → 이 키는 **RDS IAM 인증 전용**(rds-db:connect)일 가능성이 큽니다.")
        log("    그렇다면 비밀번호 대신 토큰을 만들어 붙는 방식이라,")
        log("    엔드포인트·DB 계정명·(필요시)인증서만 더 받으면 됩니다.")


if __name__ == "__main__":
    main()
