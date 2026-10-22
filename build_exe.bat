@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem 코드 수정 후 배포판 exe 다시 만들기 (이 PC에만 필요: python, pip install pyinstaller pillow pywin32 pyhwpx)
for /f "delims=" %%i in ('python -c "import pyhwpx,os;print(os.path.join(os.path.dirname(pyhwpx.__file__),'FilePathCheckerModule.dll'))"') do set DLL=%%i
python -m PyInstaller --noconfirm --clean --onedir --console --name HWPX_Redactor ^
  --workpath build --specpath build --distpath 배포판 ^
  --add-binary "%DLL%;." --hidden-import win32com.client ^
  --exclude-module numpy --exclude-module matplotlib --exclude-module yaml --exclude-module tkinter ^
  watch_redact.py
echo.
echo 완료: 배포판\HWPX_Redactor  (사용법.txt 는 유지됨)
pause
