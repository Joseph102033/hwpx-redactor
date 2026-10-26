"""Windows 경량판 UI. 문서는 로컬 임시 폴더에서 처리합니다."""
from pathlib import Path
import queue
import tempfile
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import zipfile
from redact_lite import process, detect_companies, parse


def main():
    root = tk.Tk()
    root.title('사업장명 이미지 변환기 · 경량판')
    root.geometry('660x600')
    root.minsize(620, 560)
    frame = ttk.Frame(root, padding=20)
    frame.pack(fill='both', expand=True)
    ttk.Label(frame, text='사업장명 이미지 변환기', font=('맑은 고딕', 17, 'bold')).pack(anchor='w')
    ttk.Label(frame, text='전화번호 삭제 / 주민번호 → 출생연도 / 주소 시·군·구 간소화\n이름·직종·사업자번호 보존 / 결과는 새 HWPX 파일로 저장').pack(anchor='w', pady=10)
    source = tk.StringVar()
    status = tk.StringVar(value='원본 파일을 선택해 주세요. HWP는 한글 설치가 필요합니다.')
    events = queue.Queue()
    def choose():
        path = filedialog.askopenfilename(filetypes=[('한글 문서', '*.hwpx *.hwp')])
        if not path:
            return
        source.set(path)
        names.delete('1.0', 'end')
        if Path(path).suffix.lower() == '.hwpx':
            try:
                with zipfile.ZipFile(path) as archive:
                    import re
                    roots = [parse(archive.read(n)) for n in archive.namelist() if re.fullmatch(r'Contents/section\d+\.xml', n)]
                names.insert('1.0', '\n'.join(detect_companies(roots)))
                status.set('탐지한 사업장명을 확인하고 누락된 명칭·약칭을 추가해 주세요.')
            except Exception as exc:
                messagebox.showerror('파일 확인 실패', str(exc))
        else:
            status.set('HWP 파일의 사업장명을 아래에 직접 입력해 주세요.')
    choose_button = ttk.Button(frame, text='원본 파일 선택', command=choose)
    choose_button.pack(anchor='w')
    ttk.Label(frame, textvariable=source, wraplength=580).pack(anchor='w', pady=6)
    ttk.Label(frame, text='이미지로 바꿀 사업장명 (한 줄에 하나, 처리 전 확인·수정)').pack(anchor='w')
    names = tk.Text(frame, height=6, font=('맑은 고딕', 10))
    names.pack(fill='x', pady=5)
    ttk.Label(frame, text='주민번호는 출생연도만 남기고, 제목행도 출생연도로 변경합니다.').pack(anchor='w', pady=4)
    def start():
        src = Path(source.get())
        companies = [s.strip() for s in names.get('1.0', 'end').splitlines() if s.strip()]
        if not src.is_file() or not companies:
            messagebox.showinfo('입력 확인', '원본 파일과 사업장명을 입력해 주세요.')
            return
        dst = filedialog.asksaveasfilename(defaultextension='.hwpx', initialfile=src.stem + '_경량본.hwpx', filetypes=[('HWPX 문서', '*.hwpx')])
        if not dst:
            return
        if Path(dst).exists():
            messagebox.showerror('저장 위치 확인', '기존 파일은 덮어쓰지 않습니다. 새 이름을 지정해 주세요.')
            return
        run_button.config(state='disabled')
        choose_button.config(state='disabled')
        status.set('처리 중입니다…')
        def worker():
            try:
                with tempfile.TemporaryDirectory(prefix='hwpx_input_') as folder:
                    if src.suffix.lower() == '.hwp':
                        import pythoncom
                        import hwp_convert
                        pythoncom.CoInitialize()
                        try:
                            converted = hwp_convert.convert(str(src), str(Path(folder) / 'input.hwpx'))
                            result = process(converted, companies, dst)
                        finally:
                            hwp_convert.quit_hwp()
                            pythoncom.CoUninitialize()
                    else:
                        result = process(src, companies, dst)
                events.put((True, result))
            except Exception as exc:
                events.put((False, str(exc)))
        threading.Thread(target=worker, daemon=True).start()
    run_button = ttk.Button(frame, text='변환하여 새 파일로 저장', command=start)
    run_button.pack(anchor='w', pady=10)
    ttk.Label(frame, textvariable=status, wraplength=580).pack(anchor='w')
    def poll():
        try:
            ok, result = events.get_nowait()
        except queue.Empty:
            pass
        else:
            run_button.config(state='normal')
            choose_button.config(state='normal')
            if ok:
                status.set(f"완료: 사업장명 이미지 {result['pictures']}곳 / 전화 {result['phone']} / 주민번호 {result['jumin']} / 주소 {result['address']}")
                messagebox.showinfo('저장 완료', result['output'] + '\n\n한글에서 레이아웃과 처리 결과를 확인해 주세요.')
            else:
                status.set('처리 실패 — 아래 오류를 확인해 주세요.')
                messagebox.showerror('처리 실패', result)
        root.after(150, poll)
    def close():
        if str(run_button['state']) == 'disabled':
            messagebox.showinfo('처리 중', '파일 처리가 끝난 후 창을 닫아 주세요.')
        else:
            root.destroy()
    root.protocol('WM_DELETE_WINDOW', close)
    poll()
    root.mainloop()


if __name__ == '__main__':
    main()
