@echo off
chcp 65001 >nul
cd /d "%~dp0"
py -3 -m venv .venv-watch-lite
if errorlevel 1 goto fail
.venv-watch-lite\Scripts\python -m pip install -r requirements-lite-build.txt
if errorlevel 1 goto fail
.venv-watch-lite\Scripts\python -m PyInstaller --noconfirm --clean --onedir --console --name HWPX_Lite_Watcher --distpath dist-watch-lite --workpath build-watch-lite --specpath build-watch-lite --add-data "%~dp0templates\frontmatter.hwpx;templates" --hidden-import win32com.client --hidden-import pythoncom --exclude-module numpy --exclude-module matplotlib --exclude-module yaml --exclude-module tkinter "%~dp0watch_lite.py"
if errorlevel 1 goto fail
echo 완료: dist-watch-lite\HWPX_Lite_Watcher\HWPX_Lite_Watcher.exe
echo 더블클릭하면 폴더 감시가 시작됩니다(같은 폴더에 input/output/done/review 자동 생성).
pause
exit /b 0
:fail
echo 빌드 실패. 위 오류를 확인하세요.
pause
exit /b 1
