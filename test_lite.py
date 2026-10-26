import hashlib
from pathlib import Path
import tempfile
import unittest
import zipfile
import xml.etree.ElementTree as ET
from redact_lite import process, parse, joined, H, HP, HC


def paragraph(*runs):
    return '<hp:p>' + ''.join(f'<hp:run charPrIDRef="0"><hp:t>{t}</hp:t></hp:run>' for t in runs) + '</hp:p>'


def cell(col, row, *runs):
    return f'<hp:tc><hp:subList>{paragraph(*runs)}</hp:subList><hp:cellAddr colAddr="{col}" rowAddr="{row}"/></hp:tc>'


def fixture(path):
    table = '<hp:tbl><hp:tr>' + cell(0, 0, '사업장명') + cell(1, 0, '가나', '산업(주)') + '</hp:tr><hp:tr>' + cell(0, 1, '소재지') + cell(1, 1, '서울특별시 중구 ', '세종대로 100') + '</hp:tr></hp:tbl>'
    section = f'<hs:sec xmlns:hs="urn:section" xmlns:hp="{HP}" xmlns:hc="{HC}">' + table + paragraph('홍길동 대표자 김대표 조사자 박조사 직종 용접공 123-45-67890 산재관리번호 12345678901') + paragraph('전화 010-', '1234-5678 주민 900101-', '1234567 끝') + paragraph('가나산업과 가나', '산업의 작업') + '</hs:sec>'
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('mimetype', 'application/hwp+zip')
        archive.writestr('Contents/section0.xml', section)
        archive.writestr('Contents/section2.xml', f'<hs:sec xmlns:hs="urn:section" xmlns:hp="{HP}">' + paragraph('가나산업 01012345678 9001011234567') + '</hs:sec>')
        archive.writestr('Contents/header.xml', '<hh:head xmlns:hh="urn:header"><hh:charPr id="0" height="1000"/></hh:head>')
        archive.writestr('Contents/content.hpf', '<opf:package xmlns:opf="urn:opf"><opf:manifest/></opf:package>')
        archive.writestr('Preview/PrvText.txt', '개인정보 원문')
        archive.writestr('BinData/original.png', b'preserved')


class LiteTests(unittest.TestCase):
    def test_scope_images_and_original(self):
        with tempfile.TemporaryDirectory() as folder:
            src = Path(folder) / 'input.hwpx'
            fixture(src)
            original = src.read_bytes()
            result = process(src)
            self.assertEqual(src.read_bytes(), original)
            self.assertEqual(result['pictures'], 4)
            self.assertEqual(result['phone'], 2)
            self.assertEqual(result['jumin'], 2)
            with zipfile.ZipFile(result['output']) as archive:
                self.assertIsNone(archive.testzip())
                root = parse(archive.read('Contents/section0.xml'))
                text = joined(root)
                for value in ('홍길동', '김대표', '박조사', '용접공', '123-45-67890', '12345678901', '서울특별시 중구', '끝', '과 ', '의 작업'):
                    self.assertIn(value, text)
                for value in ('가나산업', '900101', '1234-5678', '세종대로', '재해자A'):
                    self.assertNotIn(value, text)
                self.assertEqual(len(list(root.iter(H + 'pic'))), 3)
                self.assertEqual(archive.read('Preview/PrvText.txt'), b'')
                self.assertEqual(archive.read('BinData/original.png'), b'preserved')
                self.assertTrue(any(n.startswith('BinData/lite_company_') for n in archive.namelist()))
                for name in archive.namelist():
                    if name.endswith(('.xml', '.hpf')):
                        ET.fromstring(archive.read(name))
            output_before = Path(result['output']).read_bytes()
            with self.assertRaises(ValueError):
                process(src)
            self.assertEqual(Path(result['output']).read_bytes(), output_before)

    def test_no_company_no_output(self):
        with tempfile.TemporaryDirectory() as folder:
            src = Path(folder) / 'input.hwpx'
            fixture(src)
            with self.assertRaises(ValueError):
                process(src, [])
            with self.assertRaises(ValueError):
                process(src, ['없는회사'])
            self.assertFalse(src.with_name('input_경량본.hwpx').exists())

    def test_inline_controls(self):
        with tempfile.TemporaryDirectory() as folder:
            src = Path(folder) / 'input.hwpx'
            fixture(src)
            with zipfile.ZipFile(src) as archive:
                entries = {n: archive.read(n) for n in archive.namelist()}
            xml = entries['Contents/section0.xml'].decode()
            xml = xml.replace('가나산업과 가나', '가나<hp:lineBreak/>산업과 가나')
            xml = xml.replace('전화 010-', '전화 010-<hp:tab/>')
            entries['Contents/section0.xml'] = xml.encode()
            with zipfile.ZipFile(src, 'w') as archive:
                for name, value in entries.items():
                    archive.writestr(name, value)
            result = process(src)
            self.assertEqual(result['pictures'], 4)
            self.assertEqual(result['phone'], 2)
            with zipfile.ZipFile(result['output']) as archive:
                root = parse(archive.read('Contents/section0.xml'))
                self.assertEqual(len(list(root.iter(H + 'lineBreak'))), 1)
                self.assertEqual(len(list(root.iter(H + 'tab'))), 1)
                self.assertNotIn('가나산업', joined(root))

    def test_changed_document_stops_without_output(self):
        with tempfile.TemporaryDirectory() as folder:
            src = Path(folder) / 'input.hwpx'
            fixture(src)
            with zipfile.ZipFile(src) as archive:
                entries = {n: archive.read(n) for n in archive.namelist()}
            entries['Contents/section0.xml'] = entries['Contents/section0.xml'].replace(b'<hp:p>', b'<hp:p><hp:insertBegin Id="1"/>', 1)
            with zipfile.ZipFile(src, 'w') as archive:
                for name, value in entries.items():
                    archive.writestr(name, value)
            with self.assertRaisesRegex(ValueError, '변경 이력'):
                process(src)
            self.assertFalse(src.with_name('input_경량본.hwpx').exists())

    def test_default_birthyear_and_correct_header(self):
        from redact_lite import birthyear
        self.assertEqual(birthyear('900101-1'), '1990년')
        self.assertEqual(birthyear('050101-3******'), '2005년')
        self.assertEqual(birthyear('900101-*'), '')
        with tempfile.TemporaryDirectory() as folder:
            src = Path(folder) / 'input.hwpx'
            fixture(src)
            with zipfile.ZipFile(src) as archive:
                entries = {n: archive.read(n) for n in archive.namelist()}
            entries['Contents/section0.xml'] = entries['Contents/section0.xml'].replace(
                b'</hs:sec>', (paragraph('주민등록', '번호') + '</hs:sec>').encode())
            with zipfile.ZipFile(src, 'w') as archive:
                for name, value in entries.items(): archive.writestr(name, value)
            result = process(src)
            with zipfile.ZipFile(result['output']) as archive:
                value = joined(parse(archive.read('Contents/section0.xml')))
                self.assertIn('출생연도', value)
                self.assertIn('1990년', value)
                self.assertNotIn('출생년도', value)
                self.assertNotIn('주민등록번호', value)
                self.assertNotIn('900101', value)

    def test_partial_jumin_and_non_target_numbers(self):
        from redact_lite import JUMIN
        for value in ('900101-1', '900101 - 1******', '9001011234567'):
            self.assertEqual(JUMIN.sub('', value), '')
        for value in ('900101', '2023-01-05', '123-45-67890', '90010112345678'):
            self.assertEqual(JUMIN.sub('', value), value)

    def test_original_as_output_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            src = Path(folder) / 'input.hwpx'
            fixture(src)
            before = hashlib.sha256(src.read_bytes()).digest()
            with self.assertRaises(ValueError):
                process(src, output=src)
            self.assertEqual(before, hashlib.sha256(src.read_bytes()).digest())


if __name__ == '__main__':
    unittest.main()
