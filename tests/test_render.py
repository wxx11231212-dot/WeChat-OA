import importlib.util
import json
from pathlib import Path
import re
import sys
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('wechat_render', ROOT / 'scripts/render.py')
render = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = render
spec.loader.exec_module(render)


class RendererTests(unittest.TestCase):
    def make(self, text, **options):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        base = Path(folder.name)
        source = base/'input.md'
        source.write_text(text,encoding='utf-8')
        output = base/'out'
        metadata = render.render(source,output,**options)
        return output,metadata

    def test_required_style_and_text_survive(self):
        output,meta = self.make('# 文章标题\n\n## 小标题\n\n原文**关键词**，数字 7.5 亿。\n\n---\n\n> [!important]\n> 这是**完整结论**。\n\n#AI #ai',highlights='preserve')
        article = (output/'article.html').read_text(encoding='utf-8')
        parser = render.TreeParser();parser.feed(article)
        self.assertNotIn('<hr',article)
        self.assertNotIn('border-top',article)
        self.assertNotIn('#8b5cf6',article)
        self.assertIn('border-bottom:2px solid #0969da',article)
        self.assertIn('border-radius:12px',article)
        self.assertIn('box-shadow:0 6px 24px',article)
        self.assertEqual(parser.root.text().count('──────'),2)
        self.assertIn('#AI #ai',parser.root.text())
        self.assertNotIn('文章标题',parser.root.text())
        self.assertNotIn('**',parser.root.text())
        self.assertNotIn('[!important]',parser.root.text())
        self.assertEqual(meta['stats']['highlights'],2)

    def test_code_not_rewritten_as_hashtag(self):
        output,_ = self.make('# T\n\n```c\n#include <stdio.h>\n```\n\n#tag',highlights='preserve')
        parser=render.TreeParser();parser.feed((output/'article.html').read_text(encoding='utf-8'))
        code = next(n for n in render.walk(parser.root) if n.tag=='code')
        self.assertIn('#include <stdio.h>',code.text())
        self.assertNotIn('\\#include',code.text())

    def test_untrusted_content_cannot_create_script_or_event_handler(self):
        output,_ = self.make('# </script><script>window.bad=1</script>\n\n<img src="x" onerror="window.bad=1">\n\n[链接](javascript:alert(1))',highlights='preserve')
        article=(output/'article.html').read_text(encoding='utf-8')
        self.assertNotRegex(article,r'<script|onerror=|javascript:')
        preview=(output/'preview.html').read_text(encoding='utf-8')
        self.assertNotIn('</script><script>window.bad=1</script>',preview)

    def test_no_end_option_and_no_open_is_default(self):
        output,_=self.make('# T\n\n正文。',include_end=False)
        self.assertNotIn('E N D',(output/'article.txt').read_text(encoding='utf-8'))

    def test_suggestions_preserve_words_and_protected_code(self):
        text = '# T\n\n目标用户需要改善工作流程，但也要明确数据权限。\n\n评价指标要反映完整结果，不应只看生成速度。\n\n`目标用户`\n'
        output,meta=self.make(text,highlights='suggest',include_end=False)
        parser=render.TreeParser();parser.feed((output/'article.html').read_text(encoding='utf-8'))
        self.assertGreater(meta['stats']['auto_highlights'],0)
        self.assertEqual(re.sub(r'\s+','',parser.root.text()),'目标用户需要改善工作流程，但也要明确数据权限。评价指标要反映完整结果，不应只看生成速度。目标用户')
        self.assertFalse(any(n.tag=='strong' for code in render.walk(parser.root) if code.tag=='code' for n in render.walk(code)))

    def test_docx_extracts_title_body_and_images_in_order(self):
        folder=tempfile.TemporaryDirectory();self.addCleanup(folder.cleanup)
        base=Path(folder.name);source=base/'input.docx';output=base/'out';output.mkdir()
        xml='''<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><w:body><w:p><w:pPr><w:pStyle w:val="Title"/></w:pPr><w:r><w:t>测试标题</w:t></w:r></w:p><w:p><w:r><w:t>第一段。</w:t></w:r></w:p><w:p><w:r><w:drawing><a:blip r:embed="rId1"/></w:drawing></w:r></w:p><w:p><w:r><w:t>最后一段。</w:t></w:r></w:p></w:body></w:document>'''
        rels='''<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Target="media/image1.png"/></Relationships>'''
        with zipfile.ZipFile(source,'w') as z:
            z.writestr('word/document.xml',xml);z.writestr('word/_rels/document.xml.rels',rels);z.writestr('word/media/image1.png',b'PNG-test')
        md=render.docx_to_markdown(source,output)
        self.assertTrue(md.startswith('# 测试标题'))
        self.assertLess(md.index('第一段'),md.index('assets/image1.png'))
        self.assertLess(md.index('assets/image1.png'),md.index('最后一段'))
        self.assertEqual((output/'assets/image1.png').read_bytes(),b'PNG-test')


if __name__=='__main__':
    unittest.main()
