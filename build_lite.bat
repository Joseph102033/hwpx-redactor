@echo off
chcp 65001 >nul
cd /d "%~dp0"
py -3 -m venv .venv-lite
if errorlevel 1 goto fail
.venv-lite\Scripts\python -m pip install -r requirements-lite-build.txt
if errorlevel 1 goto fail
.venv-lite\Scripts\python -m PyInstaller --noconfirm --clean --onefile --windowed --name HWPX_Lite --distpath dist-lite --workpath build-lite --specpath build-lite --hidden-import win32com.client --hidden-import pythoncom --exclude-module numpy --exclude-module matplotlib --exclude-module yaml "%~dp0lite_app.py"
if errorlevel 1 goto fail
echo 완료: dist-lite\HWPX_Lite.exe
pause
exit /b 0
:fail
echo 빌드 실패. 위 오류를 확인하세요.
pause
exit /b 1
