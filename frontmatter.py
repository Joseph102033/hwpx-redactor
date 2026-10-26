"""HWPX 앞부분 교체. 경계가 모호하거나 지원하지 않는 서식은 중단한다."""
from copy import deepcopy
from pathlib import Path
import re
import zipfile
import xml.etree.ElementTree as ET

HP = '{http://www.hancom.co.kr/hwpml/2011/paragraph}'
HH = '{http://www.hancom.co.kr/hwpml/2011/head}'


def text(node):
    return ''.join(''.join(e.itertext()) for e in node.iter(HP + 't'))


def compact(node):
    return re.sub(r'\s+', '', text(node))


def find_body(sections):
    candidates = []
    for name, root in sections.items():
        paragraphs = list(root)
        for i, paragraph in enumerate(paragraphs):
            if not re.fullmatch(r'(?:1[.．)]?|Ⅰ[.．]?)?재해발생사업장개요', compact(paragraph)):
                continue
            nearby = paragraphs[i + 1:i + 5]
            if not any('사업장명' in compact(p) and ('소재지' in compact(p) or '주소' in compact(p)) and p.find('.//' + HP + 'tbl') is not None for p in nearby):
                continue
            start = i
            for j in range(i - 1, max(-1, i - 5), -1):
                value = compact(paragraphs[j])
                if value in ('재해조사의견서', '재해원인조사의견서'):
                    start = j
                    break
                if value:
                    break
            candidates.append((name, start))
    if len(candidates) != 1:
        raise ValueError('본문 시작 위치가 없거나 여러 곳입니다. 표지 교체를 중단했습니다.')
    return candidates[0]


GROUPS = {'borderFills': 'borderFill', 'charProperties': 'charPr',
          'tabProperties': 'tabPr', 'numberings': 'numbering',
          'paraProperties': 'paraPr', 'styles': 'style', 'memoProperties': 'memoPr'}
REFS = {'borderFillIDRef': 'borderFills', 'charPrIDRef': 'charProperties',
        'tabPrIDRef': 'tabProperties', 'paraPrIDRef': 'paraProperties',
        'styleIDRef': 'styles', 'nextStyleIDRef': 'styles',
        'outlineShapeIDRef': 'numberings', 'memoShapeIDRef': 'memoProperties'}


def merge_header(target, template, template_section):
    dst = target.find(HH + 'refList')
    src = template.find(HH + 'refList')
    maps, additions, fonts = {}, [], {}
    for group in src:
        name = group.tag.split('}')[-1]
        existing = dst.find(group.tag)
        if existing is None:
            existing = ET.SubElement(dst, group.tag, dict(group.attrib))
        if name == 'fontfaces':
            for face in group:
                language = face.get('lang')
                dest_face = next((f for f in existing if f.get('lang') == language), None)
                if dest_face is None:
                    dest_face = ET.SubElement(existing, face.tag, dict(face.attrib))
                next_id = max([int(f.get('id')) for f in dest_face] + [-1]) + 1
                fonts[language.lower()] = {}
                for font in face:
                    copied = deepcopy(font)
                    fonts[language.lower()][font.get('id')] = str(next_id)
                    copied.set('id', str(next_id)); next_id += 1
                    dest_face.append(copied)
                dest_face.set('fontCnt', str(len(dest_face)))
            existing.set('itemCnt', str(len(existing)))
            continue
        if name not in GROUPS:
            raise ValueError('표준 서식에서 지원하지 않는 서식 목록: ' + name)
        next_id = max([int(e.get('id')) for e in existing] + [-1]) + 1
        maps[name] = {}
        for item in group:
            copied = deepcopy(item)
            maps[name][item.get('id')] = str(next_id)
            copied.set('id', str(next_id)); next_id += 1
            existing.append(copied)
            additions.append(copied)
        existing.set('itemCnt', str(len(existing)))
    for root in additions + [template_section]:
        for element in root.iter():
            if element.tag == HH + 'fontRef':
                for key, value in list(element.attrib.items()):
                    element.set(key, fonts[key][value])
            for attr, group in REFS.items():
                if attr in element.attrib:
                    old = element.get(attr)
                    if old in maps.get(group, {}):
                        element.set(attr, maps[group][old])
                    elif old not in ('0', '-1', '', '4294967295'):
                        raise ValueError('해결되지 않은 표준 서식 참조: ' + attr)
            if element.tag == HH + 'heading' and element.get('type') in ('OUTLINE', 'NUMBER'):
                old = element.get('idRef')
                if old in maps.get('numberings', {}):
                    element.set('idRef', maps['numberings'][old])


def replace_frontmatter(data, template_path):
    # import는 순환 의존성을 피하고 기존 namespace 등록 함수를 재사용한다.
    from redact_lite import parse
    names = sorted((n for n in data if re.fullmatch(r'Contents/section\d+\.xml', n)),
                   key=lambda n: int(re.search(r'\d+', n).group()))
    sections = {n: parse(data[n]) for n in names}
    name, index = find_body(sections)
    chosen = sections[name]
    original = deepcopy(chosen)
    if index == 0 and name == names[0]:
        raise ValueError('삭제할 도입부가 없습니다. 중복 삽입을 막기 위해 중단했습니다.')
    with zipfile.ZipFile(template_path) as archive:
        template_names = [n for n in archive.namelist() if re.fullmatch(r'Contents/section\d+\.xml', n)]
        if template_names != ['Contents/section0.xml'] or any(n.startswith('BinData/') for n in archive.namelist()):
            raise ValueError('현재 표준 서식은 이미지 없는 단일 구역 HWPX만 지원합니다.')
        front = parse(archive.read(template_names[0]))
        th = parse(archive.read('Contents/header.xml'))
    for root in [front, th]:
        if any(e.tag.split('}')[-1] in ('header', 'footer', 'masterPage', 'trackChange') for e in root.iter()):
            raise ValueError('표준 서식에 지원하지 않는 머리말/꼬리말/변경 이력이 있습니다.')
    header = parse(data['Contents/header.xml'])
    merge_header(header, th, front)
    numeric = [int(e.get('id')) for root in sections.values() for e in root.iter()
               if (e.get('id') or '').isdigit()]
    object_id = max(numeric + [2100000000]) + 1
    for element in front.iter():
        if element.tag.split('}')[-1] in ('tbl', 'pic', 'rect', 'ellipse', 'line', 'container'):
            element.set('id', str(object_id)); object_id += 1
            if 'instid' in element.attrib:
                element.set('instid', str(object_id)); object_id += 1
    retained = list(chosen)[index:]
    for child in list(chosen):
        chosen.remove(child)
    chosen.extend(retained)
    if chosen.find('.//' + HP + 'secPr') is None:
        original_sec = original.find('.//' + HP + 'secPr')
        if original_sec is None:
            raise ValueError('본문의 구역 서식을 찾지 못했습니다.')
        run = ET.Element(HP + 'run', {'charPrIDRef': '0'})
        sec = deepcopy(original_sec)
        numbering = sec.find(HP + 'startNum')
        if numbering is not None:
            numbering.set('page', '1')
        run.append(sec)
        col = original.find('.//' + HP + 'colPr')
        if col is not None:
            ctrl = ET.SubElement(run, HP + 'ctrl'); ctrl.append(deepcopy(col))
        chosen[0].insert(0, run)
    chosen[0].set('pageBreak', '0')
    output_sections = [front] + [sections[n] for n in names[names.index(name):]]
    header.set('secCnt', str(len(output_sections)))
    hpf = parse(data['Contents/content.hpf'])
    manifest = next(e for e in hpf.iter() if e.tag.endswith('}manifest'))
    spine = next(e for e in hpf.iter() if e.tag.endswith('}spine'))
    ns = manifest.tag.split('}')[0] + '}'
    old_ids = {e.get('id') for e in manifest if e.get('href') in names}
    for element in list(manifest):
        if element.get('id') in old_ids:
            manifest.remove(element)
    positions = [i for i,e in enumerate(spine) if e.get('idref') in old_ids]
    insertion = min(positions) if positions else len(spine)
    for element in list(spine):
        if element.get('idref') in old_ids:
            spine.remove(element)
    for old in names:
        del data[old]
    used = {e.get('id') for e in manifest}
    for i, section in enumerate(output_sections):
        path = f'Contents/section{i}.xml'
        ref = f'lite_section_{i}'
        while ref in used:
            ref += '_'
        used.add(ref)
        ET.SubElement(manifest, ns + 'item', {'id': ref, 'href': path, 'media-type': 'application/xml'})
        spine.insert(insertion + i, ET.Element(ns + 'itemref', {'idref': ref, 'linear': 'yes'}))
        data[path] = ET.tostring(section, encoding='utf-8', xml_declaration=True)
    data['Contents/header.xml'] = ET.tostring(header, encoding='utf-8', xml_declaration=True)
    data['Contents/content.hpf'] = ET.tostring(hpf, encoding='utf-8', xml_declaration=True)
    return {'source_section': name, 'first_body_paragraph': index, 'sections': len(output_sections)}
