"""경량판: 번호 삭제, 주소 간소화, 사업장명 이미지화. 이름은 보존."""
from pathlib import Path
import io
import re
import tempfile
import zipfile
import xml.etree.ElementTree as ET
from PIL import Image
from redact_hwpx import PicFactory, render_company_png, truncate_address

HP = 'http://www.hancom.co.kr/hwpml/2011/paragraph'
HC = 'http://www.hancom.co.kr/hwpml/2011/core'
H = '{' + HP + '}'
# 긴 숫자의 일부를 잘못 지우지 않도록 경계 확인. 하이픈 없는 번호도 처리.
JUMIN = re.compile(
    r'(?<!\d)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])'
    r'(?:[ \t]*[-–][ \t]*[0-9*][\d*]{0,6}|[0-9*][\d*]{6})(?![\d*])')
PHONE = re.compile(r'(?<!\d)(?:\+82[ .-]?(?:10|2|[3-6][1-5]|70)|0(?:10|11|16|17|18|19|2|[3-6][1-5]|70|80|50[2-8]))[ .()-]*\d{3,4}[ .-]*\d{4}(?!\d)')
CORP = re.compile(r'\(주\)|㈜|주식회사|\(유\)|유한회사|유한책임회사')
LABELS = {'사업장명', '회사명', '업체명', '사업체명', '원청', '하청', '원도급', '하도급'}
# 원청/하청/원도급/하도급은 재해자 표의 '소속' 값으로도 그대로 쓰이므로, 주소 칸이 있는
# 표(=사업장 개요표) 안에서만 라벨로 인정한다. 그 외엔 값으로 취급해 오탐지를 막는다.
AMBIGUOUS_LABELS = {'원청', '하청', '원도급', '하도급'}
# 사업자등록번호는 유지하되(LITE_README 정책), 사업장관리번호·산재관리번호(사업개시번호)는 삭제한다.
BIZNO_LABEL = re.compile(r'관리번호|개시번호')


def accept_changes(data):
    """한컴 '변경 내용 추적'을 전부 승인한 것처럼 정리한다: 삽입 마커만 제거(텍스트는 유지),
    삭제 구간은 텍스트만 제거(구조 태그는 보존)하여 지금 화면에 보이는 상태 그대로 확정한다."""
    data = re.sub(rb'<hp:insertBegin\b[^>]*/>|<hp:insertEnd\b[^>]*/>', b'', data)

    def _del(m):
        region = m.group(2)
        return re.sub(rb'(^|>)[^<>]+(<|$)', lambda mm: mm.group(1) + mm.group(2), region)

    data = re.sub(rb'<hp:deleteBegin\b[^>]*\bId="(\d+)"[^>]*/>(.*?)<hp:deleteEnd\b[^>]*\bId="\1"[^>]*/>',
                   _del, data, flags=re.S)
    return re.sub(rb'<hp:delete(?:Begin|End)\b[^>]*/>', b'', data)


def birthyear(value):
    digits = re.sub(r'[ \t–-]', '', value)
    gender = digits[6]
    if gender == '*':
        return ''  # 세기를 판단할 수 없으면 추측하지 않음.
    # 1,2: 내국인 1900년대 / 3,4: 내국인 2000년대 / 5,6: 외국인 1900년대 / 7,8: 외국인 2000년대
    # 9,0: 2020년 외국인등록번호 뒷자리(5~8) 소진에 따른 연장번호(각각 1900년대/2000년대).
    century = 1900 if gender in '125679' else 2000
    return f'{century + int(digits[:2])}년'


def parse(data):
    for _, (prefix, uri) in ET.iterparse(io.BytesIO(data), events=['start-ns']):
        if not re.fullmatch(r'ns\d+', prefix):
            ET.register_namespace(prefix, uri)
    return ET.fromstring(data)


def texts(element):
    return list(element.iter(H + 't'))


def joined(element):
    return ''.join(''.join(t.itertext()) for t in texts(element))


def detect_companies(roots):
    found = set()
    for root in roots:
        for table in root.iter(H + 'tbl'):
            cells = {}
            for row in table.findall(H + 'tr'):
                for cell in row.findall(H + 'tc'):
                    addr = cell.find(H + 'cellAddr')
                    if addr is not None:
                        cells[int(addr.get('colAddr')), int(addr.get('rowAddr'))] = joined(cell).strip()
            has_addr = any('주소' in v or '소재지' in v for v in cells.values())
            for (col, row), value in cells.items():
                norm = re.sub(r'\s', '', value)
                if norm not in LABELS:
                    continue
                if norm in AMBIGUOUS_LABELS and not has_addr:
                    continue
                # 바로 오른쪽 또는 바로 아래 값만 후보로 사용. 멀리 있는 임의 셀 추측 금지.
                for key in ((col + 1, row), (col, row + 1)):
                    candidate = cells.get(key, '')
                    if candidate and candidate not in LABELS and candidate not in {'-', '소재지', '주소', '대표자', '성명'}:
                        found.add(candidate)
                        break
    return sorted(found)


def company_pattern(companies):
    tokens = set()
    for company in companies:
        company = company.strip()
        if not company:
            continue
        tokens.add(company)
        core = CORP.sub('', company).strip()
        if len(core) >= 2:
            tokens.add(core)
        for marker in ('㈜', '(주)', '주식회사'):
            if any(x in company for x in ('㈜', '(주)', '주식회사')):
                tokens.add(re.sub(r'㈜|\(주\)|주식회사', lambda _: marker, company))
    parts = []
    for token in sorted(tokens, key=lambda t: (-len(t), t)):
        part = r'\s*'.join(re.escape(ch) for ch in re.sub(r'\s', '', token))
        if re.fullmatch('[A-Za-z]+', token):
            part = r'(?<![A-Za-z])' + part + r'(?![A-Za-z])'
        parts.append(part)
    return re.compile('|'.join(parts)) if parts else None


def replace_span(nodes, start, end, replacement='', picture=None, parents=None):
    """뒤쪽 범위부터 치환하여 run 경계를 넘는 텍스트의 주변 서식을 보존."""
    offset = 0
    first = True
    for node in nodes:
        value = node.text or ''
        stop = offset + len(value)
        if offset < end and stop > start:
            a, b = max(0, start - offset), min(len(value), end - offset)
            if first and picture is not None:
                parent = parents[node]
                suffix = ET.Element(H + 't')
                suffix.text = value[b:]
                node.text = value[:a]
                idx = list(parent).index(node)
                parent.insert(idx + 1, picture)
                parent.insert(idx + 2, suffix)
            else:
                node.text = value[:a] + (replacement if first else '') + value[b:]
            first = False
        offset = stop


def normalize_inline(root):
    """줄바꿈/탭 제어를 보존하면서 텍스트 조각을 각각의 hp:t로 분리."""
    for run in root.iter(H + 'run'):
        for node in list(run):
            if node.tag != H + 't' or not list(node):
                continue
            position = list(run).index(node)
            pieces = []
            first = ET.Element(H + 't', node.attrib)
            first.text = node.text
            pieces.append(first)
            for child in list(node):
                if child.tag not in {H + n for n in ('lineBreak', 'tab', 'fwSpace', 'nbSpace')} or list(child) or child.text:
                    raise ValueError('지원하지 않는 텍스트 내부 제어입니다. 원본 서식을 확인해 주세요.')
                tail = child.tail
                child.tail = None
                control = ET.Element(H + 't')
                control.append(child)
                pieces.append(control)
                following = ET.Element(H + 't')
                following.text = tail
                pieces.append(following)
            pieces[-1].tail = node.tail
            run.remove(node)
            for index, piece in enumerate(pieces):
                run.insert(position + index, piece)


def process(src, companies=None, output=None):
    src = Path(src)
    if src.suffix.lower() != '.hwpx':
        raise ValueError('HWPX 파일을 선택해 주세요.')
    output = Path(output) if output else src.with_name(src.stem + '_경량본.hwpx')
    if output.resolve() == src.resolve() or output.exists():
        raise ValueError('출력 파일이 이미 있습니다. 다른 저장 이름을 선택해 주세요.')
    with zipfile.ZipFile(src) as archive:
        infos = archive.infolist()
        data = {info.filename: archive.read(info) for info in infos}
    section_names = [name for name in data if re.fullmatch(r'Contents/section\d+\.xml', name)]
    if not section_names:
        raise ValueError('HWPX 본문을 찾지 못했습니다.')
    # 변경 내용 추적이 켜져 있으면 전부 승인한 것처럼 정리하고(지금 보이는 상태를 확정) 계속 진행한다.
    for n in section_names:
        if re.search(rb'<\w+:(?:insertBegin|deleteBegin)\b', data[n]):
            data[n] = accept_changes(data[n])
    sections = {name: parse(data[name]) for name in section_names}
    # 사업장명을 못 찾거나 이미지화를 못 해도, 전화/주민번호/주소 처리는 끝까지 진행한다.
    companies = detect_companies(sections.values()) if companies is None else companies
    pattern = company_pattern(companies)
    font = None
    if pattern is not None:
        import os
        candidates = [str(Path(os.environ.get('WINDIR', r'C:\Windows')) / 'Fonts' / 'malgun.ttf'),
                      '/usr/share/fonts/truetype/nanum/NanumGothic.ttf',
                      '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc']
        font = next((p for p in candidates if Path(p).exists()), None)
        if not font:
            pattern = None  # 폰트가 없으면 사업장명 이미지화만 건너뛴다.
    header = parse(data['Contents/header.xml'])
    heights = {e.get('id'): int(e.get('height', '1000')) for e in header.iter() if e.tag.endswith('}charPr')}
    manifest_root = parse(data['Contents/content.hpf'])
    manifest = next((e for e in manifest_root.iter() if e.tag.endswith('}manifest')), None)
    if manifest is None:
        raise ValueError('HWPX 이미지 목록(manifest)이 없습니다.')
    ns = manifest.tag.split('}')[0] + '}'
    used_ids = {e.get('id') for e in manifest}
    pf = PicFactory({})
    existing_ids = {e.get('id') for root in sections.values() for e in root.iter()}
    numeric_ids = [int(i) for i in existing_ids if i and i.isdigit()]
    pf.id = max([pf.id] + numeric_ids)
    cache = {}
    count = {'phone': 0, 'jumin': 0, 'address': 0, 'pictures': 0, 'bizno': 0}
    with tempfile.TemporaryDirectory(prefix='hwpx_lite_') as folder:
        for name, root in sections.items():
            normalize_inline(root)
            parents = {child: parent for parent in root.iter() for child in parent}
            # 표 셀 전체 주소: 여러 run/문단으로 나뉜 상세주소도 간소화.
            for cell in root.iter(H + 'tc'):
                nodes = [t for t in texts(cell) if not list(t)]
                value = ''.join(t.text or '' for t in nodes)
                short = truncate_address(value)
                if short != value:
                    replace_span(nodes, 0, len(value), short)
                    count['address'] += 1
            # 사업장관리번호 · 산재관리번호(사업개시번호) 값은 라벨을 남기고 값만 완전히 삭제.
            for table in root.iter(H + 'tbl'):
                cell_els = {}
                for trow in table.findall(H + 'tr'):
                    for cell in trow.findall(H + 'tc'):
                        addr = cell.find(H + 'cellAddr')
                        if addr is not None:
                            cell_els[int(addr.get('colAddr')), int(addr.get('rowAddr'))] = cell
                for (col, row), cell in cell_els.items():
                    if not BIZNO_LABEL.search(re.sub(r'\s', '', joined(cell))):
                        continue
                    for key in ((col + 1, row), (col, row + 1)):
                        target = cell_els.get(key)
                        if target is None:
                            continue
                        nodes = [t for t in texts(target) if not list(t)]
                        value = ''.join(t.text or '' for t in nodes)
                        if value and not BIZNO_LABEL.search(re.sub(r'\s', '', value)):
                            replace_span(nodes, 0, len(value), '')
                            count['bizno'] += 1
                            break
            for paragraph in root.iter(H + 'p'):
                # 중첩 표의 문단은 해당 문단 처리 때만 포함.
                nodes = [t for run in paragraph.findall(H + 'run') for t in run.findall(H + 't') if not list(t)]
                value = ''.join(t.text or '' for t in nodes)
                if re.sub(r'\s', '', value) in ('주민등록번호', '주민번호', '출생년도'):
                    replace_span(nodes, 0, len(value), '출생연도')
                for regex, key in ((JUMIN, 'jumin'), (PHONE, 'phone')):
                    value = ''.join(t.text or '' for t in nodes)
                    matches = list(regex.finditer(value))
                    for match in reversed(matches):
                        replacement = birthyear(match.group()) if key == 'jumin' else ''
                        replace_span(nodes, *match.span(), replacement)
                    count[key] += len(matches)
                # 본문 주소는 기존의 도로명/번지 패턴을 재사용.
                from redact_hwpx import NARROW_ADDR
                value = ''.join(t.text or '' for t in nodes)
                for match in reversed(list(NARROW_ADDR.finditer(value))):
                    replace_span(nodes, *match.span(), match.group(1))
                    count['address'] += 1
                if pattern is None:
                    continue
                value = ''.join(t.text or '' for t in nodes)
                for match in reversed(list(pattern.finditer(value))):
                    label = match.group()
                    if label not in cache:
                        index = len(cache) + 1
                        ref = f'lite_company_{index}'
                        while ref in used_ids or f'BinData/{ref}.png' in data:
                            index += 1
                            ref = f'lite_company_{index}'
                        used_ids.add(ref)
                        path = Path(folder) / (ref + '.png')
                        pf.imginfo[ref] = render_company_png(label, str(path), font)
                        data[f'BinData/{ref}.png'] = path.read_bytes()
                        ET.SubElement(manifest, ns + 'item', {'id': ref, 'href': f'BinData/{ref}.png', 'media-type': 'image/png', 'isEmbeded': '1'})
                        cache[label] = ref
                    offset = 0
                    cp = '0'
                    for node in nodes:
                        if offset + len(node.text or '') > match.start():
                            cp = parents[node].get('charPrIDRef', '0')
                            break
                        offset += len(node.text or '')
                    picture = ET.fromstring(f'<wrapper xmlns:hp="{HP}" xmlns:hc="{HC}">' + pf.build(cache[label], heights.get(cp, 1000)) + '</wrapper>')[0]
                    replace_span(nodes, *match.span(), picture=picture, parents=parents)
                    count['pictures'] += 1
            data[name] = ET.tostring(root, encoding='utf-8', xml_declaration=True)
    # 사업장명을 못 찾아도(그림 0곳) 중단하지 않는다 — 전화/주민번호/주소는 이미 처리됐다.
    data['Contents/content.hpf'] = ET.tostring(manifest_root, encoding='utf-8', xml_declaration=True)
    # 원문이 포함된 미리보기 캐시를 비움.
    if 'Preview/PrvText.txt' in data:
        data['Preview/PrvText.txt'] = b''
    if 'Preview/PrvImage.png' in data:
        stream = io.BytesIO()
        Image.new('RGB', (1, 1), 'white').save(stream, format='PNG')
        data['Preview/PrvImage.png'] = stream.getvalue()
    for name in sections:
        root = parse(data[name])
        for paragraph in root.iter(H + 'p'):
            # joined(run)은 run 안에 중첩된 하위 표까지 재귀로 긁어와 경계 없이 이어붙이므로
            # 서로 다른 셀 텍스트가 붙어 가짜로 매치될 수 있다. 처리 루프와 같은 방식(직계 자식만)으로 검사.
            nodes = [t for run in paragraph.findall(H + 'run') for t in run.findall(H + 't') if not list(t)]
            value = ''.join(t.text or '' for t in nodes)
            if JUMIN.search(value) or PHONE.search(value) or (pattern is not None and pattern.search(value)):
                raise ValueError('처리 대상 텍스트가 남아 있어 저장을 중단했습니다.')
    # 같은 디렉터리의 임시 파일에서 완성한 후, 기존 파일을 덮어쓰지 않고 생성.
    with tempfile.TemporaryFile() as tmp:
        with zipfile.ZipFile(tmp, 'w') as archive:
            for info in infos:
                if info.filename in data:
                    archive.writestr(info, data.pop(info.filename))
            for name, value in data.items():
                archive.writestr(name, value, compress_type=zipfile.ZIP_DEFLATED)
        tmp.seek(0)
        import shutil
        with output.open('xb') as target:
            shutil.copyfileobj(tmp, target)
    return {'output': str(output), 'companies': companies, **count}
