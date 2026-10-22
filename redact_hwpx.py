#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
중대재해조사보고서 HWPX 공개본 자동 변환기 (standalone)
=======================================================
Cowork(LLM) 없이 로컬에서 실행하는 비식별 스크립트.

사용법:
    python redact_hwpx.py config.yaml

config.yaml 에 문서별 이름/회사명만 적으면, 나머지(주민번호->출생년도,
전화/사업자번호 삭제, 직종칼럼 비우기, 소재지 시·구화, 회사명->이미지 대체,
미리보기 정리, 한컴 호환 in-place 재패키징)는 전부 자동 처리합니다.

의존성:  pip install pillow  (config 방식만 추가로 pyyaml)
        (한글 폰트는 Windows 기본 '맑은 고딕' 자동 사용)
"""
import sys, os, re, io, shutil, zipfile, tempfile, json

from PIL import Image, ImageDraw, ImageFont

# ----------------------------------------------------------------------------
# 0. 설정 로드
# ----------------------------------------------------------------------------
def load_cfg(path):
    try:
        import yaml
    except ImportError:
        sys.exit("config 방식은 PyYAML 필요:  pip install pyyaml")
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)

# ----------------------------------------------------------------------------
# 1. 회사명 이미지 생성 (검색 불가용)  — Pillow + 시스템 한글폰트
# ----------------------------------------------------------------------------
def find_korean_font():
    cands = [
        r"C:\Windows\Fonts\malgun.ttf",      # 맑은 고딕 (Windows)
        r"C:\Windows\Fonts\malgunbd.ttf",
        "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
        "/Library/Fonts/AppleGothic.ttf",
    ]
    for c in cands:
        if os.path.exists(c):
            return c
    # 폴백: koreanize-matplotlib 번들 NanumGothic
    try:
        import glob as _g, koreanize_matplotlib as _km
        f = _g.glob(os.path.join(os.path.dirname(_km.__file__), "fonts", "NanumGothic.ttf"))
        if f:
            return f[0]
    except Exception:
        pass
    return None

def render_company_png(text, out_path, font_path, px_h=64):
    """흰 배경·검정 글씨로 회사명을 PNG 렌더(타이트 크롭). 반환: (w,h) px"""
    font = ImageFont.truetype(font_path, px_h) if font_path else ImageFont.load_default()
    tmp = Image.new("RGB", (10, 10), "white")
    d = ImageDraw.Draw(tmp)
    bb = d.textbbox((0, 0), text, font=font)
    w, h = bb[2] - bb[0], bb[3] - bb[1]
    pad = 6
    im = Image.new("RGB", (w + pad * 2, h + pad * 2), "white")
    ImageDraw.Draw(im).text((pad - bb[0], pad - bb[1]), text, fill="black", font=font)
    # autocrop (Pillow 만 사용, numpy 불필요)
    from PIL import ImageChops
    bg = Image.new("RGB", im.size, "white")
    bbox = ImageChops.difference(im, bg).getbbox()
    if bbox:
        m = 3
        l, t, r, b = bbox
        im = im.crop((max(l - m, 0), max(t - m, 0),
                      min(r + m, im.size[0]), min(b + m, im.size[1])))
    im.save(out_path)
    return im.size

# ----------------------------------------------------------------------------
# 2. inline pic XML 빌더 (treatAsChar, 원본 run 구조 모방)
# ----------------------------------------------------------------------------
class PicFactory:
    def __init__(self, imginfo):
        self.imginfo = imginfo            # {binref: (pxw,pxh)}
        self.id = 2100000000
        self.inst = 1500000000
        self.z = 801
    def build(self, binref, dh):
        pxw, pxh = self.imginfo[binref]
        ow, oh = pxw * 75, pxh * 75       # px -> HWPUNIT @96dpi (7200/96=75)
        if dh <= 0: dh = 1000
        dw = round(dh * pxw / pxh)
        sx, sy = dw / ow, dh / oh
        self.id += 1; self.inst += 1; self.z += 1
        return (
        f'<hp:pic id="{self.id}" zOrder="{self.z}" numberingType="NONE" textWrap="SQUARE" '
        f'textFlow="BOTH_SIDES" lock="0" dropcapstyle="None" href="" groupLevel="0" '
        f'instid="{self.inst}" reverse="0">'
        f'<hp:offset x="0" y="0"/><hp:orgSz width="{ow}" height="{oh}"/><hp:curSz width="{dw}" height="{dh}"/>'
        f'<hp:flip horizontal="0" vertical="0"/><hp:rotationInfo angle="0" centerX="{dw//2}" centerY="{dh//2}" rotateimage="1"/>'
        f'<hp:renderingInfo><hc:transMatrix e1="1" e2="0" e3="0" e4="0" e5="1" e6="0"/>'
        f'<hc:scaMatrix e1="{sx:.6f}" e2="0" e3="0" e4="0" e5="{sy:.6f}" e6="0"/>'
        f'<hc:rotMatrix e1="1" e2="0" e3="0" e4="0" e5="1" e6="0"/></hp:renderingInfo>'
        f'<hc:img binaryItemIDRef="{binref}" bright="0" contrast="0" effect="REAL_PIC" alpha="0"/>'
        f'<hp:imgRect><hc:pt0 x="0" y="0"/><hc:pt1 x="{ow}" y="0"/><hc:pt2 x="{ow}" y="{oh}"/><hc:pt3 x="0" y="{oh}"/></hp:imgRect>'
        f'<hp:imgClip left="0" right="{ow}" top="0" bottom="{oh}"/>'
        f'<hp:inMargin left="0" right="0" top="0" bottom="0"/><hp:imgDim dimwidth="{ow}" dimheight="{oh}"/><hp:effects/>'
        f'<hp:sz width="{dw}" widthRelTo="ABSOLUTE" height="{dh}" heightRelTo="ABSOLUTE" protect="0"/>'
        f'<hp:pos treatAsChar="1" affectLSpacing="0" flowWithText="1" allowOverlap="0" holdAnchorAndSO="0" '
        f'vertRelTo="PARA" horzRelTo="PARA" vertAlign="CENTER" horzAlign="LEFT" vertOffset="0" horzOffset="0"/>'
        f'<hp:outMargin left="0" right="0" top="0" bottom="0"/></hp:pic>')

# ----------------------------------------------------------------------------
# 3. 유틸: 주민번호 -> 출생년도
# ----------------------------------------------------------------------------
JUMIN = re.compile(r'(\d{2})(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])\s*-\s*([1-8])[\d*]{0,7}')   # '900101 -1******' 공백형 포함
def jumin_to_birthyear(s):
    def rep(m):
        yy, _, _, g = m.groups()
        century = 1800 if g in '90' else (1900 if g in '1256' else 2000)  # 5~8=외국인
        return f"{century + int(yy)}년"
    return JUMIN.sub(rep, s)

PHONE = re.compile(r'\(?\s*0\d{1,2}\s*-\s*\d{3,4}\s*-\s*\d{4}\s*\)?')

# ----------------------------------------------------------------------------
# 4. 직종 칼럼 비우기 (헤더 '직종'을 가진 표의 동일 colAddr 셀 blank)
# ----------------------------------------------------------------------------
def blank_jikjong_column(section_xml, header_label="직종"):
    out = section_xml
    for tbl in re.finditer(r'<hp:tbl\b.*?</hp:tbl>', section_xml, re.S):
        block = tbl.group(0)
        # 헤더 '직종' 셀의 colAddr 찾기
        target_col = None
        for tc in re.finditer(r'<hp:tc\b.*?</hp:tc>', block, re.S):
            tcblk = tc.group(0)
            txt = ''.join(re.findall(r'<hp:t>(.*?)</hp:t>', tcblk, re.S))
            txt = re.sub(r'<[^>]+>', '', txt)        # 인라인 태그 제거
            txt = re.sub(r'\s', '', txt)             # 공백 제거('직 종'→'직종')
            ca = re.search(r'<hp:cellAddr colAddr="(\d+)"', tcblk)
            if txt == re.sub(r'\s', '', header_label) and ca:
                target_col = ca.group(1); break
        if target_col is None:
            continue
        # 해당 칼럼 셀 전부 blank (헤더 포함)
        def blank_tc(tc):
            tcblk = tc.group(0)
            ca = re.search(r'<hp:cellAddr colAddr="(\d+)"', tcblk)
            if ca and ca.group(1) == target_col:
                return re.sub(r'<hp:t>.*?</hp:t>', '<hp:t></hp:t>', tcblk, flags=re.S)
            return tcblk
        newblock = re.sub(r'<hp:tc\b.*?</hp:tc>', blank_tc, block, flags=re.S)
        out = out.replace(block, newblock, 1)
    return out

# ----------------------------------------------------------------------------
# 5. 소재지 시·구화 (라벨 '소재지' 다음 셀의 주소를 시·군·구까지만)
# ----------------------------------------------------------------------------
SIDO = (r'서울특별시|서울|부산광역시|부산|대구광역시|대구|인천광역시|인천|광주광역시|광주|'
        r'대전광역시|대전|울산광역시|울산|세종특별자치시|세종|경기도|경기|'
        r'강원특별자치도|강원도|강원|충청북도|충북|충청남도|충남|'
        r'전북특별자치도|전라북도|전북|전라남도|전남|경상북도|경북|경상남도|경남|'
        r'제주특별자치도|제주')
# 시·도(약칭 포함)는 선택, 시·군(+구) 또는 (시도+)구 까지만 — 우편번호 prefix 허용
#   예) '전주시 덕진구 두간7길'→'전주시 덕진구' / '충남 논산시 은진면'→'충남 논산시' / '대전 대덕구 대화로'→'대전 대덕구'
ADDR = re.compile(
    r'^(?:\(\d{5}\)\s*)?'
    r'(?P<full>'
    r'(?:(?:' + SIDO + r')\s+)?[가-힣]+?[시군](?:\s*[가-힣]+?구)?'   # (시도) 시/군 (+구, 붙여쓰기 허용)
    r'|(?:(?:' + SIDO + r')\s+)[가-힣]+?구'                          # 시도 + 구(중구·동구 등 1글자+구 포함)
    r')'
    r'\s*(?=[가-힣]*\d*[로길]|[가-힣]{1,4}[읍면동리]|산?\d|번지|,|$)'  # 뒤에 상세주소/끝(공백 없이 붙어도 OK)
)
def truncate_address(s):
    m = ADDR.match(s.strip())
    return m.group('full') if m else s

# 본문 줄글 속 상세주소(시·군·구 뒤 도로명+번지)만 시·구까지로 — 도로+숫자 있을 때만(오삭제 방지)
NARROW_ADDR = re.compile(
    r'((?:[가-힣]{2,}(?:특별시|광역시|특별자치시|특별자치도|도)\s+)?[가-힣]{2,}[시군구](?:\s+[가-힣]{2,}구)?)'
    r'(?=\s+(?:[가-힣]{2,4}[읍면동리]|[가-힣][\d가-힣]*[로길]\s*\d))'   # 뒤에 '장소명+읍면동리' 또는 '도로+번호'가 와야 절단(일반어 보호)
    r'((?:\s+[가-힣]{2,4}[읍면])?(?:\s+[가-힣]{2,4}[동리])*(?:\s+[가-힣]{1,5}마을)?'
    r'(?:\s+[가-힣][\d가-힣]*[로길]\s*\d+[\d\-]*(?:번[안길]*\s*\d+)?)?(?:\s+(?:일원|일대))?(?:[\s,]*\d+(?:동|호))*)'
)
def narrow_address(t):
    return NARROW_ADDR.sub(lambda m: m.group(1), t)

# ----------------------------------------------------------------------------
# 6. 메인 처리
# ----------------------------------------------------------------------------
def main(cfg_path):
    cfg = load_cfg(cfg_path)
    src = cfg["input"]
    out = cfg.get("output") or re.sub(r'\.hwpx$', '_공개본.hwpx', src)

    font = find_korean_font()
    if not font:
        print("[!] 한글 폰트를 못 찾음 — 회사명 이미지 품질이 낮을 수 있음")

    workdir = tempfile.mkdtemp(prefix="hwpx_")
    with zipfile.ZipFile(src) as z:
        z.extractall(workdir)
    cpath = os.path.join(workdir, "Contents", "section0.xml")
    x = open(cpath, encoding="utf-8").read()
    header = open(os.path.join(workdir, "Contents", "header.xml"), encoding="utf-8").read()
    heights = {cid: int(h) for cid, h in re.findall(r'<hh:charPr id="(\d+)"[^>]*height="(\d+)"', header)}

    # ---- (a) 이름 매핑 만들기 (재해자A.., 사업주A.., 조사자->공란) ----
    name_map = {}
    for i, n in enumerate(cfg.get("재해자", [])):
        name_map[n] = f"재해자{chr(ord('A')+i)}"
    for i, n in enumerate(cfg.get("사업주", [])):
        name_map[n] = f"사업주{chr(ord('A')+i)}"
    for n in cfg.get("조사자", []):
        name_map[n] = ""          # 공란
    for n, v in (cfg.get("기타치환") or {}).items():
        name_map[n] = v

    # ---- (b) hp:t 텍스트 노드 단위 자동 처리 ----
    def proc_text(m):
        t = m.group(1)
        raw = t
        # 1) 정확 이름 치환 (셀 전체가 그 이름일 때만 → 오치환 방지)
        if raw.strip() in name_map:
            return f'<hp:t>{name_map[raw.strip()]}</hp:t>'
        # 2) 주민번호 -> 출생년도
        t = jumin_to_birthyear(t)
        # 3) 전화/팩스 삭제
        if PHONE.search(t):
            t = PHONE.sub('', t)
        return f'<hp:t>{t}</hp:t>'
    x = re.sub(r'<hp:t>(.*?)</hp:t>', proc_text, x, flags=re.S)

    # ---- (c) 라벨 기반: 소재지 시·구화 + 전화/산재번호 라벨 다음셀 blank ----
    # 소재지: '소재지' 라벨 셀 다음에 오는 주소 셀을 시·구까지
    def addr_cells(m):
        return f'<hp:t>{truncate_address(m.group(1))}</hp:t>'
    # 주소 패턴을 가진 셀만 시·구화 (안전)
    def maybe_addr(m):
        t = m.group(1)
        if ADDR.match(t.strip()) and any(k in t for k in ['로','길','동','리','번지','번길']):
            return f'<hp:t>{truncate_address(t)}</hp:t>'
        return m.group(0)
    x = re.sub(r'<hp:t>(.*?)</hp:t>', maybe_addr, x, flags=re.S)

    # 산재관리번호/사업자등록번호/사업개시번호 값 삭제 (설정에 리스트로 받음)
    for s in cfg.get("삭제문자열", []) or []:
        x = x.replace(f'<hp:t>{s}</hp:t>', '<hp:t></hp:t>')

    # ---- (d) 직종 칼럼 비우기 ----
    if cfg.get("직종칼럼삭제", True):
        x = blank_jikjong_column(x, cfg.get("직종헤더", "직종"))

    # ---- (e) 회사명 -> inline 이미지 ----
    companies = cfg.get("회사명_이미지화", [])   # 예: ["가나산업(주)","가나산업","(주)다라건설","다라건설"]
    imginfo = {}
    bindir = os.path.join(workdir, "BinData")
    pic_tokens = []   # (token, binref)
    for i, c in enumerate(sorted(companies, key=len, reverse=True)):
        binref = f"img_c{i+1}"
        png = os.path.join(bindir, binref + ".png")
        render_text = c.replace("㈜", "(주)")
        imginfo[binref] = render_company_png(render_text, png, font)
        pic_tokens.append((c, binref))
    if pic_tokens:
        # 토큰 정규식: 긴 것부터 (부분매치 방지)
        pic_tokens.sort(key=lambda kv: len(kv[0]), reverse=True)
        tmap = {k: v for k, v in pic_tokens}
        # ㈜<->(주) 양형 모두 매칭되도록 변형 추가
        variants = {}
        for k, v in list(tmap.items()):
            variants[k] = v
            if "(주)" in k: variants[k.replace("(주)", "㈜")] = v
            if "㈜" in k: variants[k.replace("㈜", "(주)")] = v
        toks = sorted(variants.keys(), key=len, reverse=True)
        tok_re = re.compile("|".join(re.escape(t) for t in toks))
        pf = PicFactory(imginfo)
        runpat = re.compile(r'<hp:run charPrIDRef="(\d+)"><hp:t>(.*?)</hp:t></hp:run>', re.S)
        def repl_run(m):
            cpr, txt = m.group(1), m.group(2)
            if not tok_re.search(txt):
                return m.group(0)
            dh = heights.get(cpr, 1000)
            res, last = [], 0
            for mm in tok_re.finditer(txt):
                s, e = mm.span()
                if s > last:
                    res.append(f'<hp:run charPrIDRef="{cpr}"><hp:t>{txt[last:s]}</hp:t></hp:run>')
                res.append(f'<hp:run charPrIDRef="{cpr}">' + pf.build(variants[mm.group(0)], dh) + '<hp:t/></hp:run>')
                last = e
            if last < len(txt):
                res.append(f'<hp:run charPrIDRef="{cpr}"><hp:t>{txt[last:]}</hp:t></hp:run>')
            return ''.join(res)
        x = runpat.sub(repl_run, x)
        # content.hpf 매니페스트 등록
        hpf_path = os.path.join(workdir, "Contents", "content.hpf")
        hpf = open(hpf_path, encoding="utf-8").read()
        add = ''.join(f'<opf:item id="{b}" href="BinData/{b}.png" media-type="image/png" isEmbeded="1"/>'
                      for _, b in pic_tokens)
        anchor = re.search(r'<opf:item id="image\d+" ', hpf)
        if anchor:
            hpf = hpf[:anchor.start()] + add + hpf[anchor.start():]
        else:
            hpf = hpf.replace('</opf:manifest>', add + '</opf:manifest>')
        open(hpf_path, "w", encoding="utf-8").write(hpf)

    open(cpath, "w", encoding="utf-8").write(x)

    # ---- (f) 미리보기(PrvText) 동일 비식별 ----
    prv = os.path.join(workdir, "Preview", "PrvText.txt")
    if os.path.exists(prv):
        t = open(prv, encoding="utf-8").read()
        for n, v in name_map.items():
            if n: t = t.replace(n, v if v else "")
        for c, _ in pic_tokens:
            t = t.replace(c, "[사업장명]")
            t = t.replace(c.replace("(주)", "㈜"), "[사업장명]")
        t = jumin_to_birthyear(t)
        t = PHONE.sub('', t)
        for s in cfg.get("삭제문자열", []) or []:
            t = t.replace(s, '')
        # 주소 시·구화
        t = re.sub(r'(\S+(?:특별시|광역시|특별자치시|도)\s*\S+?[시군구])[^\s<>]*(로|길)\s?\d+[^<>]*',
                   lambda m: m.group(1), t)
        open(prv, "w", encoding="utf-8").write(t)
    # PrvImage 중립 placeholder
    pimg = os.path.join(workdir, "Preview", "PrvImage.png")
    if os.path.exists(pimg):
        im = Image.new("RGB", (724, 1024), "white")
        ImageDraw.Draw(im).text((40, 480), "공개본 (비식별 처리)", fill="black")
        im.save(pimg)

    # ---- (g) 한컴 호환 in-place 재패키징 ----
    shutil.copy(src, out)
    os.chmod(out, 0o644)
    changed = ["Contents/section0.xml", "Contents/content.hpf",
               "Preview/PrvText.txt", "Preview/PrvImage.png"]
    changed += [f"BinData/{b}.png" for _, b in pic_tokens]
    # zipfile 로 제자리 교체 (원본 엔트리/순서/ mimetype 보존)
    _inplace_update(out, workdir, changed)

    # ---- (h) 검수 ----
    residue = _audit(out, cfg)
    print(f"[완료] {out}")
    print(f"[검수] 잔재: {residue if residue else '없음(0)'}")
    print(f"[이미지] 회사명 {len(pic_tokens)}종 대체")
    shutil.rmtree(workdir, ignore_errors=True)

def _inplace_update(hwpx_path, workdir, changed):
    """원본 zip에서 changed 엔트리만 새 내용으로 교체. mimetype/순서/기타 보존."""
    tmp = hwpx_path + ".tmp"
    with zipfile.ZipFile(hwpx_path) as zin:
        infos = zin.infolist()
        with zipfile.ZipFile(tmp, "w") as zout:
            # mimetype 먼저 (stored)
            for info in infos:
                data = zin.read(info.filename)
                if info.filename in changed:
                    with open(os.path.join(workdir, info.filename), "rb") as f:
                        data = f.read()
                if info.filename == "mimetype":
                    zi = zipfile.ZipInfo("mimetype"); zi.compress_type = zipfile.ZIP_STORED
                    zout.writestr(zi, data)
                else:
                    zout.writestr(info, data, zipfile.ZIP_DEFLATED)
            # 새로 추가된 이미지 (원본에 없던 엔트리)
            existing = {i.filename for i in infos}
            for fn in changed:
                if fn not in existing:
                    with open(os.path.join(workdir, fn), "rb") as f:
                        zout.writestr(fn, f.read(), zipfile.ZIP_DEFLATED)
    os.replace(tmp, hwpx_path)

def _audit(hwpx_path, cfg):
    bad = []
    with zipfile.ZipFile(hwpx_path) as z:
        txt = ""
        for fn in ["Contents/section0.xml", "Contents/section1.xml"]:
            if fn in z.namelist():
                s = z.read(fn).decode("utf-8")
                txt += "".join(re.findall(r'<hp:t>(.*?)</hp:t>', s, re.S))
        if "Preview/PrvText.txt" in z.namelist():
            txt += z.read("Preview/PrvText.txt").decode("utf-8")
    watch = (cfg.get("재해자", []) + cfg.get("사업주", []) + cfg.get("조사자", []) +
             cfg.get("회사명_이미지화", []) + (cfg.get("삭제문자열") or []))
    for w in watch:
        if w and w in txt:
            bad.append(w)
    if JUMIN.search(txt): bad.append("주민번호패턴")
    if PHONE.search(txt): bad.append("전화번호패턴")
    return bad

if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("사용법: python redact_hwpx.py config.yaml")
    main(sys.argv[1])
