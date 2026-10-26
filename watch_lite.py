#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
HWPX_Lite 폴더 감시 자동변환기 (무인 운영용)
===============================================
input 폴더에 한글(.hwpx 또는 .hwp)을 넣는 순간 자동으로:
  사업장명 이미지화 / 전화번호 삭제 / 주민번호 -> 출생연도 / 주소 간소화 /
  표준 표지·Disclaimer 교체(기존 표지·목차 삭제)
까지 처리해 output 폴더에 공개본을 만듭니다. 사람 이름은 치환하지 않고 그대로 둡니다.
(.hwp 는 PC에 설치된 한글로 먼저 .hwpx 변환 후 처리)

폴더 구조 (스크립트/exe 와 같은 위치에 자동 생성):
    input/   <- 변환할 hwp/hwpx 를 여기에 넣으세요
    output/  <- 공개본 결과물이 여기에 생성됩니다
    done/    <- 정상 처리된 원본이 보관됩니다
    review/  <- 자동탐지 실패·경고 문서 (사람 확인 필요)
    log.txt  <- 처리 기록

사용법:
    python watch_lite.py              폴더 감시 (Ctrl+C 로 종료)
    python watch_lite.py a.hwp b.hwpx 지정 파일만 즉시 처리 (exe 에 파일 끌어놓기와 동일)
의존성:  pip install pillow pywin32 ; 같은 폴더에 redact_lite.py, redact_hwpx.py,
         frontmatter.py, hwp_convert.py, templates/frontmatter.hwpx
"""
import os, sys, re, time, shutil, traceback, datetime
import redact_lite
import hwp_convert

BASE = os.path.dirname(sys.executable if getattr(sys, "frozen", False) else os.path.abspath(__file__))
IN   = os.path.join(BASE, "input")
OUT  = os.path.join(BASE, "output")
DONE = os.path.join(BASE, "done")
REV  = os.path.join(BASE, "review")
LOG  = os.path.join(BASE, "log.txt")
POLL = 5  # 감시 주기(초)

# PyInstaller 로 빌드하면 templates/frontmatter.hwpx 는 _MEIPASS 에 묻힙니다.
TEMPLATE = os.path.join(getattr(sys, "_MEIPASS", BASE), "templates", "frontmatter.hwpx")

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
    """HWP -> (같은 폴더에) HWPX 변환, 원본 HWP 는 done/ 보관. 변환된 HWPX 경로 반환."""
    fn = os.path.basename(src)
    log(f"HWP 변환 시작: {fn}")
    try:
        out = hwp_convert.convert(src)
        safe_move(src, DONE)
        log(f"HWP 변환 완료 -> {os.path.basename(out)}")
        return out
    except Exception as e:
        log(f"오류 -> review/ : {fn} | {e}")
        try:
            safe_move(src, REV)
        except Exception:
            pass
        return None


def handle(src):
    fn = os.path.basename(src)
    log(f"처리 시작: {fn}")
    dst = re.sub(r'\.hwpx$', '_공개본.hwpx', src, flags=re.I)
    template = TEMPLATE if os.path.isfile(TEMPLATE) else None
    if template is None:
        log(f"경고: 표준 표지 서식이 없어 표지 교체 없이 처리합니다 ({TEMPLATE})")
    try:
        result = redact_lite.process(src, companies=None, output=dst, front_template=template)
        final = safe_move(result["output"], OUT)
        safe_move(src, DONE)
        log(f"완료 -> output/{os.path.basename(final)} | "
            f"회사명이미지{result['pictures']}곳·전화{result['phone']}·주민번호{result['jumin']}·"
            f"주소{result['address']}·표지교체={'예' if result['frontmatter'] else '아니오'}")
    except Exception as e:
        log(f"오류 -> review/ : {fn} | {e}")
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
    log(f"처리 끝 - 결과는 {OUT} , 확인 필요 문서는 {REV}")


def watch():
    log("=" * 50)
    log(f"감시 폴더(input): {IN}")
    if not os.listdir(IN):
        log("input 폴더가 비어 있습니다. 여기에 .hwp / .hwpx 파일을 넣으세요.")
    log("감시 시작 - input/ 에 .hwp 또는 .hwpx 를 넣으세요. (종료: Ctrl+C 또는 창 닫기)")
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
