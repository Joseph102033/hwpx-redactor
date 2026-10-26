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


def birthyear(value):
    digits = re.sub(r'[ \t–-]', '', value)
    gender = digits[6]
    if gender == '*':
        return ''  # 세기를 판단할 수 없으면 추측하지 않음.
    century = 1800 if gender in '09' else 1900 if gender in '1256' else 2000
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
            for (col, row), value in cells.items():
                if re.sub(r'\s', '', value) not in LABELS:
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


def process(src, companies=None, output=None, front_template=None):
    src = Path(src)
    if src.suffix.lower() != '.hwpx':
        raise ValueError('HWPX 파일을 선택해 주세요.')
    output = Path(output) if output else src.with_name(src.stem + '_경량본.hwpx')
    if output.resolve() == src.resolve() or output.exists():
        raise ValueError('출력 파일이 이미 있습니다. 다른 저장 이름을 선택해 주세요.')
    with zipfile.ZipFile(src) as archive:
        infos = archive.infolist()
        data = {info.filename: archive.read(info) for info in infos}
    front_result = None
    if front_template is not None:
        if any(re.search(rb'<\w+:(?:insertBegin|deleteBegin)\b', value) for name, value in data.items() if name.endswith('.xml')):
            raise ValueError('변경 이력을 정리한 후 표지를 교체해 주세요.')
        from frontmatter import replace_frontmatter
        front_result = replace_frontmatter(data, front_template)
    sections = {name: parse(value) for name, value in data.items()
                if re.fullmatch(r'Contents/section\d+\.xml', name)}
    if not sections:
        raise ValueError('HWPX 본문을 찾지 못했습니다.')
    # 변경이력은 원본 보존 요구와 충돌할 수 있으므로 자동 수락/삭제하지 않음.
    if any(re.search(rb'<\w+:(?:insertBegin|deleteBegin)\b', data[n]) for n in sections):
        raise ValueError('변경 이력이 있습니다. 한글에서 변경 이력을 검토·정리한 후 다시 처리해 주세요.')
    companies = detect_companies(sections.values()) if companies is None else companies
    pattern = company_pattern(companies)
    if pattern is None:
        raise ValueError('사업장명을 찾지 못했습니다. 사업장명을 직접 입력해 주세요.')
    import os
    candidates = [str(Path(os.environ.get('WINDIR', r'C:\Windows')) / 'Fonts' / 'malgun.ttf'),
                  '/usr/share/fonts/truetype/nanum/NanumGothic.ttf',
                  '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc']
    font = next((p for p in candidates if Path(p).exists()), None)
    if not font:
        raise ValueError('사업장명 이미지 생성에 필요한 한글 폰트(맑은 고딕)가 없습니다.')
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
    count = {'phone': 0, 'jumin': 0, 'address': 0, 'pictures': 0}
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
    if not count['pictures']:
        raise ValueError('입력한 사업장명과 일치하는 텍스트가 없습니다.')
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
            value = ''.join(joined(run) for run in paragraph.findall(H + 'run'))
            if JUMIN.search(value) or PHONE.search(value) or pattern.search(value):
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
    return {'output': str(output), 'companies': companies, 'frontmatter': front_result, **count}
