#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
HWPX 공개본 폴더 감시 자동변환기 (무인 운영용)
===============================================
이 스크립트를 켜두면, input 폴더에 한글(.hwpx 또는 .hwp)을 넣는 순간
자동으로 비식별 처리해 output 폴더에 공개본을 만들어 줍니다.
(.hwp 는 PC에 설치된 한글로 먼저 .hwpx 변환 후 처리)

폴더 구조 (스크립트/exe 와 같은 위치에 자동 생성):
    input/   ← 변환할 hwp/hwpx 를 여기에 넣으세요
    output/  ← 공개본 결과물이 여기에 생성됩니다
    done/    ← 정상 처리된 원본이 보관됩니다
    review/  ← 자동탐지 경고/오류 난 문서 (사람 확인 권장)
    log.txt  ← 처리 기록

사용법:
    python watch_redact.py              폴더 감시 (Ctrl+C 로 종료)
    python watch_redact.py a.hwp b.hwpx 지정 파일만 즉시 처리 (exe 에 파일 끌어놓기와 동일)
의존성:  pip install pillow pywin32 ; 같은 폴더에 redact_auto.py, redact_hwpx.py, hwp_convert.py
"""
import os, sys, time, shutil, traceback, datetime
import redact_auto
import hwp_convert

BASE = os.path.dirname(sys.executable if getattr(sys, "frozen", False) else os.path.abspath(__file__))
IN   = os.path.join(BASE, "input")
OUT  = os.path.join(BASE, "output")
DONE = os.path.join(BASE, "done")
REV  = os.path.join(BASE, "review")
LOG  = os.path.join(BASE, "log.txt")
POLL = 5  # 감시 주기(초)

for d in (IN, OUT, DONE, REV):
    os.makedirs(d, exist_ok=True)

def log(msg):
    line = f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    print(line)
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass

def is_stable(path, need=2, gap=1.0, tries=40):
    """파일 크기가 안정될 때까지 대기(복사 중인 파일 처리 방지)."""
    last, ok = -1, 0
    for _ in range(tries):
        try:
            sz = os.path.getsize(path)
        except OSError:
            return False
        if sz > 0 and sz == last:
            ok += 1
            if ok >= need:
                return True
        else:
            ok = 0
        last = sz
        time.sleep(gap)
    return True

def safe_move(src, dst_dir):
    dst = os.path.join(dst_dir, os.path.basename(src))
    if os.path.exists(dst):                      # 동일 이름 충돌 시 타임스탬프 부여
        stem, ext = os.path.splitext(os.path.basename(src))
        dst = os.path.join(dst_dir, f"{stem}_{datetime.datetime.now():%H%M%S}{ext}")
    shutil.move(src, dst)
    return dst

def is_hwpx(fn):
    return fn.lower().endswith(".hwpx") and "_공개본" not in fn

def is_hwp(fn):
    return fn.lower().endswith(".hwp") and not fn.startswith("~")

def handle_hwp(src):
    """HWP → (같은 폴더에) HWPX 변환, 원본 HWP 는 done/ 보관. 변환된 HWPX 경로 반환."""
    fn = os.path.basename(src)
    log(f"HWP 변환 시작: {fn}")
    try:
        out = hwp_convert.convert(src)
        safe_move(src, DONE)
        log(f"HWP 변환 완료 → {os.path.basename(out)}")
        return out
    except Exception as e:
        log(f"오류 → review/ : {fn} | {e}")
        try:
            safe_move(src, REV)
        except Exception:
            pass
        return None

def handle(src):
    fn = os.path.basename(src)
    log(f"처리 시작: {fn}")
    try:
        res = redact_auto.process(src)           # 원본 옆에 _공개본.hwpx 생성 + 결과 dict
        final = safe_move(res["out"], OUT)        # 결과를 output/ 으로
        d = res["diag"]
        if res["warn"] or res["residue"]:
            safe_move(src, REV)
            log(f"⚠️ 점검필요 → review/ : {fn} | 경고={res['warn']} 잔재={res['residue']} "
                f"| 검출 재해자{d['jaehae']}/사업주{d['boss']}/조사자{d['inv']}/개요표{d['overview']}")
        else:
            safe_move(src, DONE)
            log(f"완료 → output/{os.path.basename(final)} | "
                f"재해자{d['jaehae']}·사업주{d['boss']}·조사자{d['inv']}·회사명이미지{res['pics']}종·잔재없음")
    except Exception as e:
        log(f"오류 → review/ : {fn} | {e}")
        log(traceback.format_exc())
        try:
            safe_move(src, REV)
        except Exception:
            pass

def run_files(paths):
    """끌어놓기/명령행 모드: 파일을 input/ 으로 복사해 1회 처리."""
    for p in paths:
        fn = os.path.basename(p)
        if not os.path.isfile(p) or not (is_hwp(fn) or is_hwpx(fn)):
            log(f"무시: {fn} (.hwp/.hwpx 아님)"); continue
        dst = os.path.join(IN, fn)
        if os.path.abspath(p) != os.path.abspath(dst):
            shutil.copy2(p, dst)
        if is_hwp(fn):
            dst = handle_hwp(dst)
            if not dst:
                continue
        handle(dst)
    hwp_convert.quit_hwp()
    log(f"처리 끝 — 결과는 {OUT} , 확인 필요 문서는 {REV}")

def watch():
    log("=" * 50)
    log(f"감시 폴더(input): {IN}")
    if not os.listdir(IN):
        log("input 폴더가 비어 있습니다. 여기에 .hwp / .hwpx 파일을 넣으세요.")
    log("감시 시작 — input/ 에 .hwp 또는 .hwpx 를 넣으세요. (종료: Ctrl+C 또는 창 닫기)")
    log("=" * 50)
    skipped = set()
    while True:
        try:
            for fn in sorted(os.listdir(IN)):
                path = os.path.join(IN, fn)
                if os.path.isdir(path):
                    continue
                if is_hwp(fn):
                    if is_stable(path):
                        handle_hwp(path)             # 변환된 .hwpx 는 다음 순회에서 처리
                elif is_hwpx(fn):
                    if is_stable(path):
                        handle(path)
                elif fn not in skipped:
                    skipped.add(fn)
                    log(f"무시(.hwp/.hwpx 아님): {fn}")
            time.sleep(POLL)
        except KeyboardInterrupt:
            log("감시 종료"); break
        except Exception as e:
            log(f"루프 오류(계속 진행): {e}"); time.sleep(POLL)
    hwp_convert.quit_hwp()

if __name__ == "__main__":
    if len(sys.argv) > 1:
        run_files(sys.argv[1:])
        if getattr(sys, "frozen", False):
            input("\n엔터를 누르면 창이 닫힙니다...")
    else:
        watch()
