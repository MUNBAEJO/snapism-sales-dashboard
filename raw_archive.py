# -*- coding: utf-8 -*-
"""raw_photoism 원본을 월 단위로 묶고 되돌린다.

왜 이렇게 묶나
--------------
xlsx 는 그 자체가 zip 이라 **그냥 다시 압축하면 거의 안 준다**(실측 2.81GB → 2.75GB).
내부는 이미 deflate 로 10%까지 눌려 있어서, 겉을 아무리 눌러도 나올 게 없다.
그래서 **내부를 한 번 풀고 xz 로 다시 누른다** — deflate 가 10% 로 만든 XML 을
xz 는 4% 대로 만든다. 실측 2.81GB → 약 1.4GB.

담는 방식은 "압축 없는 xlsx(ZIP_STORED)를 tar 에 넣고 통째로 xz" 다.
  · tar 안의 항목 하나하나가 **그대로 열리는 xlsx** 라 형식이 깨질 여지가 없다
  · 한 달치를 한 덩어리로 누르므로 파일 사이 반복(같은 시트 틀·같은 문자열)까지 먹는다
  · 되돌릴 땐 다시 deflate 로 묶어 **원래 크기의 xlsx** 로 만든다(그냥 풀면 10배가 된다)

되돌린 파일은 원본과 **바이트가 같지는 않다**(압축 방식만 바뀐 것). 시트 내용은
같다 — 묶을 때 원본과 복원본의 **내부 항목 이름·내용 해시**를 하나하나 대조하고,
전부 맞을 때만 원본을 지운다.

    python raw_archive.py pack 2025            # 묶기(검증까지, 원본은 그대로 둠)
    python raw_archive.py pack 2025 --delete   # 검증 통과한 달만 원본 삭제
    python raw_archive.py restore 202503       # 한 달 되돌리기
    python raw_archive.py list                 # 묶어 둔 것 보기
"""
import hashlib
import io
import lzma
import sys
import tarfile
import time
import zipfile
from pathlib import Path

BASE = Path(__file__).parent
RAW = BASE / "raw_photoism"
ARC = RAW / "_archive"
PRESET = 3          # 6 은 13배 느린데 9%p 더 줄 뿐이다(실측 98.8s vs 7.5s / 20MB)


def log(msg):
    print(msg, flush=True)


def _members(path_or_bytes):
    """xlsx 안의 (항목이름 → 내용 sha256). 겉포장 말고 **내용**을 비교하려는 것."""
    src = io.BytesIO(path_or_bytes) if isinstance(path_or_bytes, bytes) else path_or_bytes
    out = {}
    with zipfile.ZipFile(src) as z:
        for n in z.namelist():
            out[n] = hashlib.sha256(z.read(n)).hexdigest()
    return out


def _repack(path, method, level=None):
    """항목 이름·내용은 그대로 두고 압축 방식만 바꾼다."""
    buf = io.BytesIO()
    kw = {"compresslevel": level} if level is not None else {}
    with zipfile.ZipFile(path) as src, \
            zipfile.ZipFile(buf, "w", method, **kw) as dst:
        for i in src.infolist():
            info = zipfile.ZipInfo(i.filename, date_time=i.date_time)
            info.external_attr = i.external_attr
            info.compress_type = method
            dst.writestr(info, src.read(i.filename))
    return buf.getvalue()


def months(year: str):
    got = {}
    for f in RAW.glob(f"*_{year}*.xlsx"):
        stem = f.stem.rsplit("_", 1)[-1]          # photoism_kr_20251225 → 20251225
        if len(stem) == 8 and stem.isdigit():
            got.setdefault(stem[:6], []).append(f)
    return {k: sorted(v) for k, v in sorted(got.items())}


def pack(year: str, delete: bool):
    ARC.mkdir(parents=True, exist_ok=True)
    by_month = months(year)
    if not by_month:
        log(f"{year} 년 파일이 없어요")
        return 0
    tot_raw = sum(f.stat().st_size for v in by_month.values() for f in v)
    log(f"{year} 년 {sum(len(v) for v in by_month.values()):,}개 · "
        f"{tot_raw / 1024 / 1024 / 1024:.2f} GB · {len(by_month)}개월")
    log(f"보관 위치: {ARC}")

    done_raw = done_arc = 0
    failed = []
    for ym, files in by_month.items():
        out = ARC / f"photoism_{ym}.tar.xz"
        raw = sum(f.stat().st_size for f in files)
        t = time.time()

        # ── 묶기 ──────────────────────────────────────────────────────
        want = {}
        with tarfile.open(out, "w:xz", preset=PRESET) as tar:
            for f in files:
                want[f.name] = _members(f)
                blob = _repack(f, zipfile.ZIP_STORED)
                ti = tarfile.TarInfo(f.name)
                ti.size = len(blob)
                ti.mtime = int(f.stat().st_mtime)
                tar.addfile(ti, io.BytesIO(blob))

        # ── 검증: 묶은 것을 도로 열어 내용 해시를 대조한다 ──────────────
        bad = []
        with tarfile.open(out, "r:xz") as tar:
            seen = set()
            for ti in tar:
                seen.add(ti.name)
                got = _members(tar.extractfile(ti).read())
                if got != want.get(ti.name):
                    bad.append(ti.name)
            missing = set(want) - seen
        if missing:
            bad += [f"{m} (빠짐)" for m in sorted(missing)]

        arc = out.stat().st_size
        mark = "OK " if not bad else "!! "
        log(f"  {mark}{ym}  {len(files):>4}개  "
            f"{raw / 1024 / 1024:>7.1f} → {arc / 1024 / 1024:>6.1f} MB "
            f"({arc / raw * 100:>4.1f}%)  {time.time() - t:>5.1f}s")
        if bad:
            failed.append(ym)
            log(f"      대조 실패 {len(bad)}건 — 원본은 그대로 둡니다: {bad[:3]}")
            continue

        done_raw += raw
        done_arc += arc
        if delete:
            for f in files:
                f.unlink()
            log(f"      원본 {len(files):,}개 삭제 · "
                f"{(raw - arc) / 1024 / 1024:,.0f} MB 확보")

    log(f"\n합계 {done_raw / 1024 / 1024 / 1024:.2f} → "
        f"{done_arc / 1024 / 1024 / 1024:.2f} GB "
        f"(확보 {(done_raw - done_arc) / 1024 / 1024 / 1024:.2f} GB)")
    if failed:
        log(f"★검증 실패한 달: {failed} — 원본을 지우지 않았어요")
        return 1
    if not delete:
        log("원본은 그대로예요. 지우려면 --delete 를 붙여 다시 돌리세요.")
    return 0


def restore(ym: str):
    """묶어 둔 달을 원래 크기의 xlsx 로 되돌린다."""
    src = ARC / f"photoism_{ym}.tar.xz"
    if not src.exists():
        log(f"없어요: {src}")
        return 1
    n = 0
    with tarfile.open(src, "r:xz") as tar:
        for ti in tar:
            blob = tar.extractfile(ti).read()
            # ★그냥 풀면 안 된다 — 안이 무압축이라 10배 크기로 떨어진다.
            buf = io.BytesIO(blob)
            (RAW / ti.name).write_bytes(
                _repack(buf, zipfile.ZIP_DEFLATED, 9))
            n += 1
    log(f"{ym} · {n:,}개 복원 → {RAW}")
    return 0


def show():
    if not ARC.exists():
        log("묶어 둔 게 없어요")
        return 0
    tot = 0
    for f in sorted(ARC.glob("*.tar.xz")):
        with tarfile.open(f, "r:xz") as tar:
            cnt = sum(1 for _ in tar)
        tot += f.stat().st_size
        log(f"  {f.name:<28} {cnt:>5,}개  {f.stat().st_size / 1024 / 1024:>7.1f} MB")
    log(f"  합계 {tot / 1024 / 1024 / 1024:.2f} GB")
    return 0


if __name__ == "__main__":
    a = sys.argv[1:]
    if not a or a[0] == "list":
        sys.exit(show())
    if a[0] == "pack":
        sys.exit(pack(a[1] if len(a) > 1 else "2025", "--delete" in a))
    if a[0] == "restore":
        sys.exit(restore(a[1]))
    log(__doc__)
    sys.exit(2)
