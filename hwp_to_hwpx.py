#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
HWP → HWPX 일괄 변환기  (같은 폴더의 .hwp 전부를 .hwpx 로)
==========================================================
한컴오피스 '한글'의 자동화(COM) 기능을 이용합니다.

사용법:
    python hwp_to_hwpx.py            # 이 스크립트가 있는 폴더의 *.hwp 변환
    python hwp_to_hwpx.py "D:\\대상폴더"   # 특정 폴더 지정

필수 조건:
    - Windows
    - 한컴오피스 '한글' 설치 (한글 2014 이상 권장)
    - pip install pywin32

결과:
    원본옆에 같은이름.hwpx 생성.  (이미 .hwpx 가 있으면 건너뜀)

※ 보안 경고창이 매 파일마다 뜨면(자동화 차단), 아래 'FilePathCheckerModule'
  보안모듈 등록이 필요합니다. README 참고.
"""
import os, sys, glob


def main():
    # 대상 폴더 결정
    folder = sys.argv[1] if len(sys.argv) > 1 else os.path.dirname(os.path.abspath(__file__))
    folder = os.path.abspath(folder)

    hwps = sorted(p for p in glob.glob(os.path.join(folder, "*.hwp"))
                  if p.lower().endswith(".hwp"))     # .hwpx 는 제외됨
    if not hwps:
        print(f"[알림] '{folder}' 에 변환할 .hwp 파일이 없습니다.")
        return

    try:
        import win32com.client as win32
    except ImportError:
        sys.exit("pywin32 가 필요합니다:  pip install pywin32")

    # 한글 실행(자동화 객체)
    try:
        hwp = win32.gencache.EnsureDispatch("HWPFrame.HwpObject")
    except Exception as e:
        sys.exit(f"한컴 '한글'을 찾을 수 없습니다(설치 확인). 상세: {e}")

    # 보안 승인창 억제(보안모듈이 등록돼 있을 때만 효과)
    try:
        hwp.RegisterModule("FilePathCheckDLL", "FilePathCheckerModule")
    except Exception:
        pass
    # 창 숨김(있으면)
    try:
        hwp.XHwpWindows.Item(0).Visible = False
    except Exception:
        pass

    ok = skip = fail = 0
    for path in hwps:
        out = os.path.splitext(path)[0] + ".hwpx"
        name = os.path.basename(path)
        if os.path.exists(out):
            print(f"[건너뜀] {name}  (이미 .hwpx 있음)")
            skip += 1
            continue
        try:
            opened = hwp.Open(path, "HWP", "forceopen:true")
            if opened is False:                      # 보안창 거부/열기 실패
                raise RuntimeError("열기 실패(보안 승인창 또는 손상 가능)")
            hwp.SaveAs(out, "HWPX", "")              # HWPX 포맷으로 저장
            hwp.Clear(1)                             # 현재 문서 닫기(저장 안 함)
            print(f"[OK]     {name}  ->  {os.path.basename(out)}")
            ok += 1
        except Exception as e:
            print(f"[실패]   {name} : {e}")
            try:
                hwp.Clear(1)
            except Exception:
                pass
            fail += 1

    try:
        hwp.Quit()
    except Exception:
        pass
    print(f"\n완료 — 성공 {ok} / 건너뜀 {skip} / 실패 {fail}")


if __name__ == "__main__":
    main()
