#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
HWP → HWPX 변환 (한컴오피스 '한글' 자동화 COM 사용)
====================================================
순수 파이썬으로는 HWP 를 HWPX 로 '쓰기' 불가 → PC에 설치된 한글을 원격조종해 다른이름저장.

필수: Windows + 한컴오피스 한글 + pywin32 (exe 배포판에는 포함됨)

보안승인창: 한글은 자동화로 파일을 열 때마다 '접근 허용' 창을 띄움.
  같은 폴더(또는 exe 폴더)에 FilePathCheckerModule.dll 이 있으면
  HKCU\\Software\\HNC\\HwpAutomation\\Modules 에 자동 등록해 창을 없앰.
"""
import os, re, sys

MODULE_NAME = "FilePathCheckerModule"
_hwp = None


def _app_dir():
    return os.path.dirname(sys.executable if getattr(sys, "frozen", False) else os.path.abspath(__file__))


def _register_security_module():
    """FilePathCheckerModule.dll 이 레지스트리에 없으면 (현재 사용자 범위로) 등록."""
    try:
        import winreg
        key_path = r"Software\HNC\HwpAutomation\Modules"
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as k:
                cur, _ = winreg.QueryValueEx(k, MODULE_NAME)
                if cur and os.path.exists(cur):
                    return True
        except OSError:
            pass
        for base in (_app_dir(), getattr(sys, "_MEIPASS", "")):
            dll = os.path.join(base, MODULE_NAME + ".dll")
            if base and os.path.exists(dll):
                with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path) as k:
                    winreg.SetValueEx(k, MODULE_NAME, 0, winreg.REG_SZ, dll)
                return True
    except Exception:
        pass
    return False


def _get_hwp():
    global _hwp
    if _hwp is None:
        try:
            import win32com.client as win32
        except ImportError:
            raise RuntimeError("pywin32 가 없습니다 (pip install pywin32)")
        _register_security_module()
        try:
            _hwp = win32.Dispatch("HWPFrame.HwpObject")
        except Exception as e:
            raise RuntimeError(f"한컴 '한글'을 실행할 수 없습니다(설치 확인): {e}")
        try:
            _hwp.RegisterModule("FilePathCheckDLL", MODULE_NAME)
        except Exception:
            pass
        try:
            _hwp.XHwpWindows.Item(0).Visible = False
        except Exception:
            pass
    return _hwp


def hwpx_name(hwp_path):
    """'a.hwp' / 'a.hwp.hwp' → 'a.hwpx'"""
    return re.sub(r'(\.hwp)+$', '', hwp_path, flags=re.I) + ".hwpx"


def convert(hwp_path, out_path=None):
    """HWP 1개를 HWPX 로 저장하고 경로 반환. 실패 시 RuntimeError."""
    global _hwp
    hwp_path = os.path.abspath(hwp_path)
    out_path = os.path.abspath(out_path or hwpx_name(hwp_path))
    hwp = _get_hwp()
    try:
        if hwp.Open(hwp_path, "HWP", "forceopen:true") is False:
            raise RuntimeError("열기 실패(보안 승인창 거부 또는 파일 손상)")
        if hwp.SaveAs(out_path, "HWPX", "") is False:
            raise RuntimeError("HWPX 저장 실패")
    except Exception as e:
        try:
            hwp.Clear(1)
        except Exception:
            _hwp = None                     # 한글이 죽었으면 다음 호출 때 재실행
        raise RuntimeError(f"HWP→HWPX 변환 실패: {e}")
    hwp.Clear(1)                            # 문서 닫기(저장 안 함)
    if not os.path.exists(out_path):
        raise RuntimeError("HWPX 파일이 생성되지 않았습니다")
    return out_path


def quit_hwp():
    global _hwp
    if _hwp is not None:
        try:
            _hwp.Quit()
        except Exception:
            pass
        _hwp = None
