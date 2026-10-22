#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
HWPX 공개본 **완전 자동** 변환기 (YAML 불필요)
===============================================
재해사업장 개요표·재해자표·조사자표가 고정 포맷인 점을 이용해
이름/회사명/번호를 표에서 자동 추출 → 비식별합니다.

사용법:
    python redact_auto.py 원본.hwpx [원본2.hwpx ...]
    python redact_auto.py *.hwpx
결과:  원본_공개본.hwpx

처리: 재해자명→재해자A.. / 대표자→사업주A.. / 조사자→공란 /
      주민번호→출생년도 / 전화·팩스 삭제 / 소재지→시·구 /
      산재·사업개시번호 삭제 / 직종칼럼 비움 / 회사명→검색불가 이미지.

의존성: pip install pillow  (numpy·pyyaml 불필요) ; 같은 폴더에 redact_hwpx.py 필요
"""
import sys, os, re, glob, shutil, zipfile, tempfile

# 공유 헬퍼 재사용
from redact_hwpx import (find_korean_font, render_company_png, PicFactory,
                         JUMIN, jumin_to_birthyear, PHONE,
                         blank_jikjong_column, ADDR, truncate_address, narrow_address,
                         _inplace_update)

def norm(s): return re.sub(r'\s', '', s or '')

def clean_text(s):
    """hp:t 안에 박힌 인라인 마크업 제거: 줄바꿈은 붙이고(이름 줄바꿈), 탭/고정폭공백은 공백."""
    s = (s or '').replace('<hp:lineBreak/>', '')
    s = re.sub(r'<hp:tab\b[^>]*/>', ' ', s)
    s = s.replace('<hp:fwSpace/>', ' ')
    s = re.sub(r'<hp:[A-Za-z]+\b[^>]*/>', '', s)   # 기타 인라인 빈 태그
    return s


def accept_changes(x):
    """한컴 변경이력(추적) 수락: 삽입 마커 제거(텍스트 유지), 삭제구간은 '텍스트만' 제거하고
    구조 태그(p/tc/run/subList 등)는 보존 → XML 깨짐 없이 옛 내용(옛 회사명·주소 등) 제거."""
    x = re.sub(r'<hp:insertBegin\b[^>]*/>|<hp:insertEnd\b[^>]*/>', '', x)
    _txt = re.compile(r'(^|>)[^<>]+(<|$)')
    def _del(m):
        region = m.group(2)
        return _txt.sub(lambda mm: mm.group(1) + mm.group(2), region)  # 태그사이/양끝 텍스트 제거, 태그 유지
    # deleteBegin/End 는 같은 Id 끼리 짝지음(다른 Id 끼리 묶으면 정상 텍스트까지 지워짐)
    x = re.sub(r'<hp:deleteBegin\b[^>]*\bId="(\d+)"[^>]*/>(.*?)<hp:deleteEnd\b[^>]*\bId="\1"[^>]*/>',
               _del, x, flags=re.S)
    return re.sub(r'<hp:delete(?:Begin|End)\b[^>]*/>', '', x)        # 짝 없는 잔여 마커 제거


def is_overview(alln):
    """사업장 개요표 판별: (사업장명/회사명 또는 대표자) + (소재지/주소)."""
    return (('사업장명' in alln or '회사명' in alln or '대표자' in alln)
            and ('소재지' in alln or '주소' in alln))


def parse_table(block):
    """표 블록 -> [(col,row,text,htlist), ...]"""
    cells = []
    for tc in re.finditer(r'<hp:tc\b.*?</hp:tc>', block, re.S):
        b = tc.group(0)
        hts = re.findall(r'<hp:t>(.*?)</hp:t>', b, re.S)
        txt = clean_text(''.join(hts)).strip()
        ca = re.search(r'<hp:cellAddr colAddr="(\d+)" rowAddr="(\d+)"', b)
        if ca:
            cells.append((int(ca.group(1)), int(ca.group(2)), txt, hts))
    return cells

CORP_MARKER = re.compile(
    r'\(주\)|㈜|\(유\)|\(합\)|\(재\)|\(사\)|\(의\)|\(학\)|'
    r'주식회사|유한회사|유한책임회사|합자회사|합명회사|재단법인|사단법인|의료법인|학교법인')

def company_tokens(raw):
    """사업장명 셀값 -> 검색차단 토큰 집합. (주)·㈜·(유)·주식회사·유한회사 등 모든 법인마커 인식,
    마커 없는 '핵심명'까지 토큰화해 본문 맨이름도 매칭."""
    v = re.split(r'&lt;|<', raw)[0].strip()
    v = re.sub(r'\s+', ' ', v)
    v = re.sub(r'\s*\([^()]{3,}\)\s*$', '', v).strip()   # 끝 괄호(현장명 등) 제거. '(주)'(1자)는 보존
    toks = {v, v.replace('㈜', '(주)'), v.replace('(주)', '㈜')}
    mk = CORP_MARKER.search(v)
    if mk:
        marker = mk.group(0)
        if mk.start() == 0:                          # 접두형: (유)○○○ / (주)○○○
            core = v[mk.end():].strip()
            toks.add(marker + core)
            if marker in ('(주)', '㈜', '주식회사'):  # 주식회사계열은 ㈜/(주) 혼용까지
                for pre in ('(주)', '㈜', '주식회사'):
                    toks.add(pre + core)
        else:                                        # 접미형: ○○○㈜ [지점]
            core = v[:mk.start()].strip()
            toks.add(core + marker)
            if marker in ('(주)', '㈜', '주식회사'):
                for suf in ('(주)', '㈜'):
                    toks.add(core + suf)
        if core:
            toks.add(core)                           # ★ 마커 없는 핵심명(본문 다수 등장)
    else:
        core = v
    m = re.match(r'^[A-Za-z]{2,}', core)              # 선두 영문 약칭 (예: ABC)
    if m:
        toks.add(m.group(0))
    return {t for t in toks if t and len(CORP_MARKER.sub('', t).strip()) >= 2}


def is_person_name(s):
    """사람 이름인지 검증: 한글 2~6자 또는 외국인 영문명 인정.
    '-', 숫자, 빈칸/기호값은 배제."""
    s2 = (s or '').strip()
    if re.fullmatch(r'[가-힣]{2,6}', s2.replace(' ', '')):
        return True
    if re.fullmatch(r"[A-Za-z][A-Za-z .'-]{1,30}", s2):     # 외국인 영문명(FRANCISCO 등)
        return True
    return False


TITLE_WORDS = {'현장소장', '소장', '대표', '대표자', '대표이사', '사장', '부사장', '회장',
               '부장', '과장', '차장', '팀장', '반장', '조장', '공장장', '본부장', '이사',
               '상무', '전무', '감독', '감독자', '관리자', '관리감독자', '안전관리자',
               '담당', '담당자', '직원', '책임', '책임자', '주임', '대리', '사원', '소속', '본사', '현장'}

def extract_person_names(val):
    """대표자 등 값에서 사람 이름들을 추출. '홍길동(김철수)'→['홍길동','김철수'].
    괄호·슬래시·쉼표로 분리, 직함어는 제외, 표기형(띄어쓰기) 보존."""
    names = []
    for seg in re.split(r'[()\[\]/,·]', val or ''):
        cand = seg.strip()
        if is_person_name(cand) and cand.replace(' ', '') not in TITLE_WORDS and cand not in names:
            names.append(cand)
    return names


def _set_cell_text(tc_block, new_text):
    """tc 셀의 첫 hp:t = new_text, 나머지 hp:t = 빈칸 (쪼개진 노드 통합용)."""
    seen = [False]
    def rep(m):
        if not seen[0]:
            seen[0] = True
            return '<hp:t>' + new_text + '</hp:t>'
        return '<hp:t></hp:t>'
    return re.sub(r'<hp:t>.*?</hp:t>', rep, tc_block, flags=re.S)

BIZNUM = re.compile(r'\d{3}-\d{2}-\d{5,6}|\d{9,}')   # 사업자등록번호(3-2-5) 또는 9자리+ 순수숫자

def process_overview_cells(x):
    """사업장 개요표를 '셀 단위'로 처리: 줄바꿈태그 정규화(회사명 한덩어리화),
    풀주소→시·구(쪼개진 노드 통합), 산재/관리/사업자번호 셀→통째 삭제."""
    out = x
    for tbl in re.finditer(r'<hp:tbl\b.*?</hp:tbl>', x, re.S):
        block = tbl.group(0)
        raw_join = clean_text(''.join(re.findall(r'<hp:t>(.*?)</hp:t>', block, re.S)))
        alln = norm(raw_join)
        if not is_overview(alln):
            continue
        nb = block.replace('<hp:lineBreak/>', '')      # 셀 내 줄바꿈 제거(회사명 등 한덩어리화)
        cells = []
        for tc in re.finditer(r'<hp:tc\b.*?</hp:tc>', nb, re.S):
            tcb = tc.group(0)
            joined = clean_text(''.join(re.findall(r'<hp:t>(.*?)</hp:t>', tcb, re.S)))
            ca = re.search(r'<hp:cellAddr colAddr="(\d+)" rowAddr="(\d+)"', tcb)
            if ca:
                cells.append({'col': int(ca.group(1)), 'row': int(ca.group(2)),
                              'joined': joined, 'tc': tcb})
        newblock = nb
        for c in cells:                                 # (a) 풀주소 → 시·구
            j = c['joined'].strip()
            if j and truncate_address(j) != j:
                newblock = newblock.replace(c['tc'], _set_cell_text(c['tc'], truncate_address(j)), 1)
        bycr = {(c['col'], c['row']): c for c in cells}
        for c in cells:                                 # (b) 산재/관리/사업자번호 셀 삭제
            n = norm(c['joined'])
            if (n.startswith('산재관리번호') or n.startswith('관리번호') or n.startswith('사업장관리번호')
                    or '사업자등록번호' in n or '사업개시번호' in n):
                for cc in sorted(k for k in bycr if k[1] == c['row'] and k[0] > c['col']):
                    cell2 = bycr[cc]
                    if BIZNUM.search(cell2['joined']):
                        newblock = newblock.replace(cell2['tc'], _set_cell_text(cell2['tc'], ''), 1)
                        break
        out = out.replace(block, newblock, 1)
    return out

def process_jumin_cells(x):
    """재해자표 등의 '주민등록번호' 칼럼 값셀을 셀단위로 출생년도 변환(쪼개진 노드 통합)."""
    out = x
    for tbl in re.finditer(r'<hp:tbl\b.*?</hp:tbl>', x, re.S):
        block = tbl.group(0)
        cells = []
        for tc in re.finditer(r'<hp:tc\b.*?</hp:tc>', block, re.S):
            tcb = tc.group(0)
            joined = ''.join(re.findall(r'<hp:t>(.*?)</hp:t>', tcb, re.S))
            ca = re.search(r'<hp:cellAddr colAddr="(\d+)" rowAddr="(\d+)"', tcb)
            if ca:
                cells.append({'col': int(ca.group(1)), 'row': int(ca.group(2)),
                              'joined': joined, 'tc': tcb})
        hdr = next((c for c in cells if norm(c['joined']) in ('주민등록번호', '주민번호')), None)
        if not hdr:
            continue
        newblock = block
        for c in cells:
            if c['col'] == hdr['col'] and c['row'] > hdr['row'] and JUMIN.search(c['joined']):
                newblock = newblock.replace(c['tc'], _set_cell_text(c['tc'], jumin_to_birthyear(c['joined'])), 1)
        out = out.replace(block, newblock, 1)
    return out

def process(src):
    out = re.sub(r'\.hwpx$', '_공개본.hwpx', src, flags=re.I)
    font = find_korean_font()
    workdir = tempfile.mkdtemp(prefix="hwpxA_")
    with zipfile.ZipFile(src) as z:
        z.extractall(workdir)
    contents = os.path.join(workdir, "Contents")
    sec_paths = sorted(os.path.join(contents, f) for f in os.listdir(contents)
                       if re.fullmatch(r'section\d+\.xml', f))      # section0, section1, ...
    secs = {p: open(p, encoding="utf-8").read() for p in sec_paths}
    for p in list(secs):                   # 변경이력 수락 + 셀단위 선처리(모든 섹션)
        secs[p] = accept_changes(secs[p])
        secs[p] = process_jumin_cells(process_overview_cells(secs[p]))
    xdet = ''.join(secs.values())          # 표/회사명 탐지용(전 섹션 합본)
    header = open(os.path.join(workdir, "Contents", "header.xml"), encoding="utf-8").read()
    heights = {cid: int(h) for cid, h in re.findall(r'<hh:charPr id="(\d+)"[^>]*height="(\d+)"', header)}

    # ---------- 표 자동 분석 (라벨 동의어 + 값 오른쪽/아래 양방향 탐색) ----------
    LBL = {
        'company': lambda n: n.startswith('사업장명') or n in ('회사명', '업체명', '사업체명'),
        'addr':    lambda n: n == '소재지' or '주소' in n or n == '사업장소재지',
        'boss':    lambda n: n.startswith('대표자') or n in ('대표이사', '대표', '대표자성명', '대표자명'),
        'phone':   lambda n: n.startswith('전화번호') or '연락처' in n or n in ('전화', '휴대전화', '전화(fax)', 'tel'),
        'bizno':   lambda n: n.startswith('산재관리번호') or '사업자등록번호' in n or '사업개시번호' in n or '사업장관리번호' in n,
    }
    FIXED_LABELS = {'업종', '근로자수', '생산품', '소재지', '성명', '이름', '연번', '소속',
                    '고용형태', '직종', '담당업무', '상해정도', '주민등록번호', '주민번호',
                    '생년월일', '기관명', '직위', '비고'}
    def is_label(n):
        return n in FIXED_LABELS or any(f(n) for f in LBL.values())
    SUBLABELS = {'원청', '하청', '원수급', '하수급', '본사', '현장', '원도급', '하도급'}
    def value_of(bycr, bycr_h, c, r):
        # 라벨 오른쪽 값. '원청/하청/본사/현장' 같은 서브라벨이 끼면 한 칸 더 오른쪽 탐색
        for cc in (c + 1, c + 2, c + 3):
            if (cc, r) in bycr:
                v = bycr[(cc, r)]
                if not v:
                    continue
                nv = norm(v)
                if is_label(nv) or nv in SUBLABELS:
                    continue
                return v, bycr_h.get((cc, r), [])
        return None, []

    exact = {}; delete = set(); companies = set()
    jaehae_names = []; boss_names = []; inv_names = []
    diag = {'jaehae': 0, 'boss': 0, 'inv': 0, 'overview': 0, 'jaehae_tbl': 0}
    for tb in re.finditer(r'<hp:tbl\b.*?</hp:tbl>', xdet, re.S):
        cells = parse_table(tb.group(0))
        if not cells:
            continue
        bycr = {(c, r): t for c, r, t, _ in cells}
        bycr_h = {(c, r): h for c, r, _, h in cells}
        allnorm = norm(''.join(t for _, _, t, _ in cells))

        # (A) 사업장 개요표
        if is_overview(allnorm):
            diag['overview'] += 1
            for c, r, t, _ in cells:
                n = norm(t)
                if not n:
                    continue
                val, valh = value_of(bycr, bycr_h, c, r)
                if val is None:
                    continue
                if LBL['company'](n):
                    companies.add(val)
                elif n in SUBLABELS and n in ('원청', '하청', '원수급', '하수급', '원도급', '하도급'):
                    v2 = bycr.get((c + 1, r))   # 서브라벨 오른쪽이 실제 회사명
                    if v2 and v2.strip() not in ('', '-') and not is_label(norm(v2)) and norm(v2) not in SUBLABELS:
                        companies.add(v2.strip())
                elif LBL['boss'](n):
                    for _nm in extract_person_names(val):
                        boss_names.append(_nm)

        # (B) 재해자 인적사항표
        elif ('성명' in allnorm or '이름' in allnorm) and ('주민등록번호' in allnorm or '주민번호' in allnorm or '생년월일' in allnorm):
            diag['jaehae_tbl'] += 1
            hdr = next(((c, r) for c, r, t, _ in cells if norm(t) in ('성명', '이름')), None)
            if hdr:
                nc, nr = hdr
                for c, r, t, _ in sorted(cells, key=lambda k: (k[1], k[0])):
                    # 성명열은 위치로 이름이 보장 → 글자(한글/영문) 있으면 인정(외국인명 포함)
                    if c == nc and r > nr and t.strip() and re.search(r'[가-힣A-Za-z]', t):
                        jaehae_names.append(t.strip())
            for lab in ('주민등록번호', '주민번호'):
                exact[lab] = '출생년도'

        # (C) 조사자표
        elif ('성명' in allnorm or '이름' in allnorm) and ('직위' in allnorm or '기관명' in allnorm):
            hdr = next(((c, r) for c, r, t, _ in cells if norm(t) in ('성명', '이름')), None)
            if hdr:
                nc, nr = hdr
                for c, r, t, _ in cells:
                    if c == nc and r > nr and is_person_name(t):
                        inv_names.append(t.strip())

    # ---------- 인명 매핑: 재해자 A.. → 사업주(다음 글자).. / 조사자 공란 (본문 부분일치 치환) ----------
    jl = []
    for nm in jaehae_names:
        if nm not in jl: jl.append(nm)
    bl = []
    for nm in boss_names:
        if nm not in bl and nm not in jl: bl.append(nm)
    name_map = {}
    for i, nm in enumerate(jl):
        name_map[nm] = '재해자' + chr(65 + i)
    for i, nm in enumerate(bl):
        name_map[nm] = '사업주' + chr(65 + len(jl) + i)
    for nm in inv_names:
        name_map.setdefault(nm, '')          # 조사자 → 삭제
    name_keys = sorted([k for k in name_map if k], key=len, reverse=True)
    diag['jaehae'] = len(jl); diag['boss'] = len(bl); diag['inv'] = len(set(inv_names))

    # ---------- 회사명 오타(글자순서·띄어쓰기) 보강: 본문에서 ㈜/(주) 동반 후보 중
    #            알려진 회사명과 '글자 구성이 동일'(애너그램)하면 같은 회사로 간주 ----------
    known_cores = set()
    for c in companies:
        v = re.split(r'&lt;|<', c)[0].strip()
        mk = CORP_MARKER.search(v)
        core = (v[mk.end():] if (mk and mk.start() == 0) else (v[:mk.start()] if mk else v)).strip()
        core = re.sub(r'\s', '', core)
        if len(core) >= 4:
            known_cores.add(core)
    extra = set()
    for mm in re.finditer(r'(?:㈜|\(주\))\s*([가-힣A-Za-z]{2,25})', xdet):          # 접두형
        cand = re.sub(r'\s', '', mm.group(1))
        for kc in known_cores:
            L = len(kc)
            if len(cand) >= L and cand[:L] != kc and sorted(cand[:L]) == sorted(kc):
                extra.add(cand[:L])
    for mm in re.finditer(r'([가-힣A-Za-z]{2,25})\s*(?:㈜|\(주\))', xdet):           # 접미형
        cand = re.sub(r'\s', '', mm.group(1))
        for kc in known_cores:
            L = len(kc)
            if len(cand) >= L and cand[-L:] != kc and sorted(cand[-L:]) == sorted(kc):
                extra.add(cand[-L:])
    for e in extra:
        companies.add('㈜' + e)
    # 문서가 선언한 약칭: "(이하 'XXX'(이)라 함)" → 알려진 회사핵심명과 글자 겹치면 회사명으로 추가
    for mm in re.finditer(r"이하\s*[‘'“\"]([^’'”\"]{2,25})[’'”\"]", xdet):
        ab = re.sub(r'\s', '', mm.group(1))
        for kc in known_cores:
            if len(ab) >= 2 and (ab in kc or kc in ab):
                companies.add(ab); break

    # ---------- 회사명 이미지 준비 ----------
    tokens = set()
    for c in companies:
        tokens |= company_tokens(c)
    pic_tokens = []; tmap = {}; pf = None; tok_re = None
    if tokens:
        bindir = os.path.join(workdir, "BinData")
        imginfo, render_cache, i = {}, {}, 0
        for tok in sorted(tokens, key=len, reverse=True):
            rt = tok.replace('㈜', '(주)')
            if rt not in render_cache:
                i += 1; binref = f"img_c{i}"
                imginfo[binref] = render_company_png(rt, os.path.join(bindir, binref + ".png"), font)
                render_cache[rt] = binref
            tmap[tok] = render_cache[rt]
        pic_tokens = list(set(tmap.values()))
        pf = PicFactory(imginfo)
        parts = []
        for tok in sorted(tmap, key=len, reverse=True):
            if re.fullmatch(r'[A-Za-z]{2,}', tok):
                parts.append(r'(?<![A-Za-z])' + re.escape(tok) + r'(?![A-Za-z])')
            else:
                parts.append(re.escape(tok))
        tok_re = re.compile('|'.join(parts))
        # content.hpf 매니페스트 등록
        hpf_path = os.path.join(workdir, "Contents", "content.hpf")
        hpf = open(hpf_path, encoding="utf-8").read()
        add = ''.join(f'<opf:item id="{b}" href="BinData/{b}.png" media-type="image/png" isEmbeded="1"/>' for b in pic_tokens)
        a = re.search(r'<opf:item id="image\d+" ', hpf)
        hpf = (hpf[:a.start()] + add + hpf[a.start():]) if a else hpf.replace('</opf:manifest>', add + '</opf:manifest>')
        open(hpf_path, "w", encoding="utf-8").write(hpf)

    # ---------- 통합 텍스트 처리 (hp:t 노드 단위; run 구조 무관) ----------
    import bisect
    _cp = {'cps': [], 'starts': []}        # 섹션별로 교체됨
    def nearest_cp(pos):
        idx = bisect.bisect_right(_cp['starts'], pos) - 1
        return _cp['cps'][idx][1] if idx >= 0 else None

    def proc(m):
        pos = m.start(); t = m.group(1); k = t.strip()
        if k in exact:                                   # 풀셀 치환(주소·헤더)
            return '<hp:t>' + exact[k] + '</hp:t>'
        for nm in name_keys:                             # 인명 치환(본문 포함)
            if len(nm.replace(' ', '')) >= 3:
                if nm in t:
                    t = t.replace(nm, name_map[nm])
            elif t.strip() == nm:                        # 2글자 이름은 셀 전체일 때만
                t = name_map[nm]
        for d in delete:                                 # 번호 삭제
            if d and d in t:
                t = t.replace(d, '')
        t = jumin_to_birthyear(t)
        if PHONE.search(t):
            t = PHONE.sub('', t)
        t = narrow_address(t)                            # 본문 상세주소 → 시·구
        if tok_re is not None and tok_re.search(t):      # 회사명 → inline 이미지
            dh = heights.get(nearest_cp(pos), 1000)
            res = []; last = 0
            for mm in tok_re.finditer(t):
                a2, b2 = mm.span()
                res.append('<hp:t>' + t[last:a2] + '</hp:t>')
                res.append(pf.build(tmap[mm.group(0)], dh))
                res.append('<hp:t/>')
                last = b2
            res.append('<hp:t>' + t[last:] + '</hp:t>')
            return ''.join(res)
        return '<hp:t>' + t + '</hp:t>'

    # 모든 섹션에 텍스트 처리 + 직종 칼럼 비우기 적용
    for _p in list(secs):
        _x = secs[_p]
        _cp['cps'] = [(mm.start(), mm.group(1)) for mm in re.finditer(r'charPrIDRef="(\d+)"', _x)]
        _cp['starts'] = [pp[0] for pp in _cp['cps']]
        _x = re.sub(r'<hp:t>(.*?)</hp:t>', proc, _x, flags=re.S)
        for _jk in ("직종", "담당업무"):
            _x = blank_jikjong_column(_x, _jk)
        secs[_p] = _x
        open(_p, "w", encoding="utf-8").write(_x)

    # ---------- 미리보기 정리 ----------
    prv = os.path.join(workdir, "Preview", "PrvText.txt")
    if os.path.exists(prv):
        t = open(prv, encoding="utf-8").read()
        for k, v in exact.items():
            if k: t = t.replace(k, v)
        for d in delete:
            t = t.replace(d, '')
        for nm in name_keys:                             # 인명(본문과 동일 매핑)
            t = t.replace(nm, name_map[nm])
        for tok in sorted(tokens, key=len, reverse=True):
            t = t.replace(tok, '[사업장명]')
        t = PHONE.sub('', jumin_to_birthyear(t))
        t = BIZNUM.sub('', narrow_address(t))            # 상세주소·관리번호
        t = re.sub(r'(?<=<)[^<>]*[로길]\s*\d+[^<>]*(?=>)',  # 표 셀 속 풀주소 → 시·구
                   lambda m: truncate_address(m.group(0)), t)
        open(prv, "w", encoding="utf-8").write(t)
    pimg = os.path.join(workdir, "Preview", "PrvImage.png")
    if os.path.exists(pimg):
        from PIL import Image, ImageDraw
        im = Image.new("RGB", (724, 1024), "white")
        ImageDraw.Draw(im).text((40, 480), "공개본 (비식별 처리)", fill="black"); im.save(pimg)

    # ---------- 한컴 호환 in-place 재패키징 ----------
    shutil.copy(src, out); os.chmod(out, 0o644)
    changed = [os.path.relpath(p, workdir).replace(os.sep, '/') for p in sec_paths]   # 변경된 모든 섹션
    if tokens:
        changed.append("Contents/content.hpf")
    for _pv in ("Preview/PrvText.txt", "Preview/PrvImage.png"):
        if os.path.exists(os.path.join(workdir, _pv)):
            changed.append(_pv)
    changed += [f"BinData/{b}.png" for b in pic_tokens]
    _inplace_update(out, workdir, changed)

    # ---------- 검수 리포트 ----------
    names = [k for k in name_keys if len(k.replace(' ', '')) >= 3]   # 2글자는 일반어 오탐이 많아 제외
    residue = _audit_simple(out, names, tokens, delete)
    # ---------- 자동탐지 진단 / 경고 ----------
    warn = []
    if diag['overview'] == 0: warn.append("사업장 개요표 미검출(회사명·소재지·번호 누락 위험)")
    if diag['jaehae_tbl'] == 0: warn.append("재해자표 미검출(재해자 이름 남아있을 위험)")
    if not companies: warn.append("사업장명(회사명) 미검출")
    print(f"[완료] {out}")
    print(f"  · 검출: 재해자 {diag['jaehae']} / 사업주 {diag['boss']} / 조사자 {diag['inv']} / 개요표 {diag['overview']}개")
    print(f"  · 이름 비식별: {diag['jaehae']}명(재해자) {diag['boss']}명(사업주) {diag['inv']}건(조사자 공란)")
    print(f"  · 회사명 이미지화: {len(tokens)}토큰 / 이미지 {len(pic_tokens)}종")
    print(f"  · 검수 잔재: {residue if residue else '없음(0)'}")
    if warn:
        print("  ⚠️  자동탐지 점검 필요 → 이 문서는 결과 육안확인 또는 Cowork 권장:")
        for w in warn: print("     - " + w)
    shutil.rmtree(workdir, ignore_errors=True)
    return {'out': out, 'warn': warn, 'residue': residue, 'diag': diag, 'tokens': len(tokens), 'pics': len(pic_tokens)}

def _audit_simple(hwpx, names, tokens, delete):
    with zipfile.ZipFile(hwpx) as z:
        txt = ""
        for fn in z.namelist():
            if re.fullmatch(r'Contents/section\d+\.xml', fn):
                txt += "".join(re.findall(r'<hp:t>(.*?)</hp:t>', z.read(fn).decode("utf-8"), re.S))
        if "Preview/PrvText.txt" in z.namelist():
            txt += z.read("Preview/PrvText.txt").decode("utf-8")
    bad = [w for w in list(names) + list(tokens) + list(delete) if w and w in txt]
    if JUMIN.search(txt): bad.append("주민번호")
    if PHONE.search(txt): bad.append("전화번호")
    return sorted(set(bad))

def main(argv):
    files = []
    for a in argv:
        files += glob.glob(a)
    if not files:
        sys.exit("사용법: python redact_auto.py 원본.hwpx [...]")
    for f in files:
        if f.lower().endswith('.hwpx') and '_공개본' not in f:
            try:
                process(f)
            except Exception as e:
                print(f"[오류] {f}: {e}")

if __name__ == "__main__":
    main(sys.argv[1:])
