from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
import zipfile
import xml.etree.ElementTree as ET
from frontmatter import find_body, replace_frontmatter, HP, HH, REFS


def p(text):
    e = ET.Element(HP + 'p', {'paraPrIDRef': '0', 'styleIDRef': '0'})
    run = ET.SubElement(e, HP + 'run', {'charPrIDRef': '0'})
    ET.SubElement(run, HP + 't').text = text
    return e


def section(body=True):
    root = ET.Element('{http://www.hancom.co.kr/hwpml/2011/section}sec')
    root.append(p('기존 표지' if body else '표준 표지'))
    sec = ET.SubElement(root[0][0], HP + 'secPr', {'outlineShapeIDRef': '1'})
    ET.SubElement(sec, HP + 'startNum', {'page': '0'})
    if body:
        root.append(p('목차 1. 재해발생 사업장 개요'))
        root.append(p('재해조사 의견서'))
        root.append(p('1. 재해발생 사업장 개요'))
        tablep = p('')
        table = ET.SubElement(tablep[0], HP + 'tbl', {'id': '1', 'borderFillIDRef': '1'})
        table.append(p('사업장명 가상회사 소재지 서울특별시 중구'))
        root.append(tablep)
        root.append(p('보존할 본문'))
    else:
        root.append(p('Disclaimer'))
    return root


def header():
    root = ET.Element(HH + 'head', {'secCnt': '1'})
    refs = ET.SubElement(root, HH + 'refList')
    for group, tag, ident in [('borderFills','borderFill','1'), ('charProperties','charPr','0'),
                              ('paraProperties','paraPr','0'), ('styles','style','0'), ('numberings','numbering','1')]:
        g = ET.SubElement(refs, HH + group, {'itemCnt': '1'})
        ET.SubElement(g, HH + tag, {'id': ident})
    return root


def package(root):
    opf = '{urn:opf}'
    hpf = ET.Element(opf + 'package')
    manifest = ET.SubElement(hpf, opf + 'manifest')
    ET.SubElement(manifest, opf + 'item', {'id':'section0','href':'Contents/section0.xml'})
    spine = ET.SubElement(hpf, opf + 'spine')
    ET.SubElement(spine, opf + 'itemref', {'idref':'section0'})
    return {'Contents/section0.xml':ET.tostring(root),
            'Contents/header.xml':ET.tostring(header()), 'Contents/content.hpf':ET.tostring(hpf)}


class FrontTests(unittest.TestCase):
    def test_toc_not_mistaken_for_body(self):
        self.assertEqual(find_body({'section':section()}), ('section',2))
        with self.assertRaises(ValueError):
            find_body({'a':section(), 'b':section()})
        root=section(); root.remove(root[4])
        with self.assertRaises(ValueError):
            find_body({'a':root})

    def test_merge_preserves_body_and_separates_styles(self):
        source = section()
        original_body = [deepcopy(e) for e in list(source)[2:]]
        data = package(source)
        with tempfile.TemporaryDirectory() as folder:
            template = Path(folder)/'template.hwpx'
            with zipfile.ZipFile(template,'w') as z:
                for name,value in package(section(False)).items(): z.writestr(name,value)
            result = replace_frontmatter(data,template)
        self.assertEqual(result['sections'],2)
        front = ET.fromstring(data['Contents/section0.xml'])
        body = ET.fromstring(data['Contents/section1.xml'])
        self.assertNotEqual(front[0].get('paraPrIDRef'),body[0].get('paraPrIDRef'))
        self.assertIsNotNone(body.find('.//'+HP+'secPr'))
        # 첫 문단에 붙인 구역설정만 제거하면 원문 본문 구조가 동일.
        body[0].remove(body[0][0]); body[0].attrib.pop('pageBreak')
        self.assertEqual([ET.tostring(e) for e in body], [ET.tostring(e) for e in original_body])
        text=''.join(e.text or '' for e in front.iter(HP+'t'))
        self.assertIn('Disclaimer',text)
        self.assertNotIn('목차',text)
        h=ET.fromstring(data['Contents/header.xml'])
        self.assertEqual(h.get('secCnt'),'2')
        for group in h.find(HH+'refList'):
            ids=[e.get('id') for e in group]
            self.assertEqual(len(ids),len(set(ids)))
            self.assertEqual(int(group.get('itemCnt')),len(group))


if __name__=='__main__': unittest.main()
