#!/usr/bin/env python3
"""Render a portable, editable WeChat OA article. No upstream runtime required."""
from __future__ import annotations

import argparse
import base64
from collections import Counter
from dataclasses import dataclass, field
import hashlib
import html
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import shutil
import sys
from urllib.parse import unquote, urlsplit
import webbrowser
import xml.etree.ElementTree as ET
import zipfile

try:
    import bleach
    import markdown
except ImportError as exc:
    raise SystemExit('Install dependencies first: python -m pip install -r requirements.txt') from exc

SKILL_DIR = Path(__file__).resolve().parents[1]
FONT = '-apple-system,"PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif'
WRAPPER = f'font-family:{FONT};font-size:16px;line-height:1.75;letter-spacing:0.5px;color:#1f2328;text-align:left;padding:0 8px;margin:0;'
PARAGRAPH = 'font-size:16px;line-height:1.75;letter-spacing:0.5px;color:#1f2328;text-align:left;margin:0 0 18px;padding:0;'
HIGHLIGHT = 'font-weight:600;color:#0550ae;background:linear-gradient(180deg,rgba(9,105,218,0) 0%,rgba(9,105,218,0) 58%,rgba(9,105,218,0.14) 58%,rgba(9,105,218,0.14) 100%);padding:0 2px;display:inline;'
CARD = 'background-color:#ffffff;border:none;border-radius:12px;box-shadow:0 6px 24px rgba(0,0,0,0.07);padding:20px 22px;margin:24px 8px;text-align:left;'
CARD_P = 'font-size:16px;font-weight:400;line-height:1.9;letter-spacing:0.3px;color:#555555;text-align:left;margin:0 0 10px;padding:0;'
HEADING = 'font-size:20px;font-weight:700;line-height:1.6;color:#1f2328;text-align:left;margin:32px 0 20px;padding:0 0 8px;border-bottom:2px solid #0969da;'
CTA = '如果这篇文章对你有启发，欢迎点赞、在看'
W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
R = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'
A = '{http://schemas.openxmlformats.org/drawingml/2006/main}'
VOID = {'br', 'hr', 'img'}
ALLOWED = {'p','h1','h2','h3','h4','h5','h6','strong','b','em','i','blockquote','ul','ol','li','pre','code','br','hr','img','a','table','thead','tbody','tr','th','td','del'}


@dataclass
class Node:
    tag: str
    attrs: dict[str, str] = field(default_factory=dict)
    children: list = field(default_factory=list)

    def text(self):
        return ''.join(c.text() if isinstance(c, Node) else c for c in self.children)


class TreeParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node('root')
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        node = Node(tag, dict(attrs))
        self.stack[-1].children.append(node)
        if tag not in VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for i in range(len(self.stack)-1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def walk(node):
    yield node
    for child in node.children:
        if isinstance(child, Node):
            yield from walk(child)


def serialize(node):
    if isinstance(node, str):
        return html.escape(node, quote=False)
    attrs = ''.join(f' {k}="{html.escape(str(v), quote=True)}"' for k,v in node.attrs.items())
    children = ''.join(serialize(c) for c in node.children)
    if node.tag == 'root':
        return children
    if node.tag in VOID:
        return f'<{node.tag}{attrs}>'
    return f'<{node.tag}{attrs}>{children}</{node.tag}>'


def plain_text(node):
    if isinstance(node, str):
        return node
    if node.tag == 'br':
        return '\n'
    result = ''.join(plain_text(c) for c in node.children)
    if node.tag in {'p','h2','h3','h4','pre','li','tr'}:
        result += '\n\n'
    return result


def docx_to_markdown(path, output):
    """Read only document content/relationships; embedded instructions stay text."""
    with zipfile.ZipFile(path) as package:
        rels = {}
        if 'word/_rels/document.xml.rels' in package.namelist():
            for rel in ET.fromstring(package.read('word/_rels/document.xml.rels')):
                rels[rel.attrib['Id']] = rel.attrib
        root = ET.fromstring(package.read('word/document.xml'))
        lines = []
        saved = {}

        def paragraph(p):
            pieces = []
            for child in p:
                if child.tag == W+'pPr':
                    continue
                if child.tag == W+'hyperlink':
                    label = ''.join(t.text or '' for t in child.iter(W+'t'))
                    rel = rels.get(child.attrib.get(R+'id'), {})
                    url = rel.get('Target', '')
                    pieces.append(f'[{label}]({url})' if urlsplit(url).scheme in {'https','http'} else label)
                    continue
                text = ''.join((n.text or '') if n.tag == W+'t' else '\n' if n.tag == W+'br' else '\t' if n.tag == W+'tab' else '' for n in child.iter())
                if text:
                    bold = child.find(f'{W}rPr/{W}b')
                    is_bold = bold is not None and bold.attrib.get(W+'val','1') not in {'0','false','off'}
                    pieces.append('**'+text+'**' if is_bold and text.strip() else text)
                for blip in child.iter(A+'blip'):
                    rel = rels.get(blip.attrib.get(R+'embed'), {})
                    target = rel.get('Target', '')
                    member = 'word/' + target.lstrip('/')
                    if rel.get('TargetMode') == 'External' or not target.startswith('media/') or member not in package.namelist():
                        continue
                    if member not in saved:
                        asset = output / 'assets' / Path(target).name
                        asset.parent.mkdir(exist_ok=True)
                        asset.write_bytes(package.read(member))
                        saved[member] = 'assets/' + asset.name
                    pieces.append('\n\n![图片]('+saved[member]+')\n\n')
            text = ''.join(pieces).strip()
            style = p.find(f'{W}pPr/{W}pStyle')
            style_name = style.attrib.get(W+'val','') if style is not None else ''
            if style_name.lower() in {'title','标题'}:
                text = '# ' + text.replace('**','')
            elif re.search(r'(heading|标题)\s*([1-6])', style_name, re.I):
                n = int(re.search(r'([1-6])', style_name)[1])
                text = '#' * n + ' ' + text.replace('**','')
            elif p.find(f'{W}pPr/{W}numPr') is not None:
                text = '- ' + text
            return text

        body = root.find(W+'body')
        for block in body:
            if block.tag == W+'p':
                text = paragraph(block)
                if text:
                    lines.append(text)
            elif block.tag == W+'tbl':
                rows = []
                for tr in block.findall(W+'tr'):
                    rows.append([' '.join(paragraph(p) for p in tc.findall(W+'p')).replace('|','\\|') for tc in tr.findall(W+'tc')])
                if rows:
                    width = max(map(len, rows))
                    rows = [row+['']*(width-len(row)) for row in rows]
                    lines.append('\n'.join(['| '+' | '.join(rows[0])+' |', '| '+' | '.join(['---']*width)+' |'] + ['| '+' | '.join(row)+' |' for row in rows[1:]]))
        return '\n\n'.join(lines) + '\n'


def prepare_markdown(text):
    """Protect hashtag lines and normalize the common full-width callout marker."""
    # Markdown requires whitespace after a heading marker. Make hashtags literal
    # even in parsers/extensions accepting permissive ATX headings.
    result = []
    fence = None
    for line in text.splitlines(keepends=True):
        match = re.match(r'^\s*(`{3,}|~{3,})', line)
        if match:
            marker = match[1]
            if fence is None:
                fence = marker
            elif marker[0] == fence[0] and len(marker) >= len(fence):
                fence = None
            result.append(line)
        elif fence is not None:
            result.append(line)
        else:
            result.append(re.sub(r'^(#{1,6})(?=[^#\s])', r'\\\1', line))
    return ''.join(result)


def remove_callout_marker(node):
    for i, child in enumerate(node.children):
        if isinstance(child, str):
            if not child.strip():
                continue
            node.children[i] = re.sub(r'^\s*\[!\w+\]\s*', '', child)
            return
        if isinstance(child, Node) and child.tag == 'p':
            remove_callout_marker(child)
            return


def clean_title(text, fallback):
    match = re.search(r'^#\s+(.+?)\s*#*\s*$', text, re.M)
    if match:
        label = re.sub(r'\*\*|__|`', '', match[1]).strip()
        text = text[:match.start()] + text[match.end():]
        return label, text
    return fallback, text


def safe_image(node, input_path, output, warnings):
    value = node.attrs.get('src','')
    scheme = urlsplit(value).scheme.lower()
    if scheme in {'http','https'}:
        warnings.append('外链图片未下载；公众号粘贴后可能需要在素材库重新上传。')
        return
    asset_prefix = output / value
    if not scheme and value.startswith('assets/') and asset_prefix.is_file():
        if asset_prefix.suffix.lower() in {'.png','.jpg','.jpeg','.gif','.webp'}:
            mime = {'.jpg':'jpeg','.jpeg':'jpeg'}.get(asset_prefix.suffix.lower(),asset_prefix.suffix.lower()[1:])
            node.attrs['src'] = 'data:image/'+mime+';base64,'+base64.b64encode(asset_prefix.read_bytes()).decode('ascii')
            warnings.append('本地图片已嵌入预览；粘贴公众号时仍可能需要在素材库上传。')
        else:
            node.tag='p';node.attrs={};node.children=['[图片格式需转换，请在公众号中上传]']
            warnings.append('图片不是常见位图格式，没有自动转换。')
        return
    if scheme or value.startswith(('/', '\\')):
        node.tag = 'p'
        node.attrs = {}
        node.children = ['[图片未导出：仅支持文章目录中的本地图片或 HTTPS 图片]']
        warnings.append('图片路径未导出，避免把绝对本地路径带入成品。')
        return
    candidate = (input_path.parent / unquote(value)).resolve()
    try:
        candidate.relative_to(input_path.parent.resolve())
    except ValueError:
        candidate = None
    if candidate is None or not candidate.is_file():
        node.tag = 'p'
        node.children = ['[图片：' + node.attrs.get('alt','图片') + '，请在公众号中上传]']
        node.attrs = {}
        warnings.append('有本地图片未找到，请在公众号中手动上传。')
        return
    suffix = candidate.suffix.lower()
    if suffix not in {'.png','.jpg','.jpeg','.gif','.webp'}:
        node.tag = 'p'
        node.attrs = {}
        node.children = ['[图片格式需转换，请在公众号中上传]']
        warnings.append('图片不是常见位图格式，没有自动转换。')
        return
    asset = output / 'assets' / (hashlib.sha256(candidate.read_bytes()).hexdigest()[:12]+suffix)
    asset.parent.mkdir(exist_ok=True)
    shutil.copyfile(candidate, asset)
    mime = {'.jpg':'jpeg','.jpeg':'jpeg'}.get(suffix,suffix[1:])
    node.attrs['src'] = 'data:image/'+mime+';base64,'+base64.b64encode(asset.read_bytes()).decode('ascii')
    warnings.append('本地图片已嵌入预览；粘贴公众号时仍可能需要在素材库上传。')


# Suggestions are a disclosed fallback, not a substitute for the skill agent's
# semantic reading. Existing author-selected emphasis is always preserved.
TERMS = '''核心需求 关键问题 目标用户 用户体验 使用习惯 工作流程 业务流程 数据权限 数据质量 组织关系 业务关系 协作关系 长期积累 用户规模 模型能力 办公入口 项目进度 执行计划 交付周期 供应链协同 生产管理 核心观点 行业经验 商业模式 用户增长 解决方案 投入产出 实验设计 评价指标 研究问题 关键结论 数据分析 技术方案 工作效率 成本控制 风险管理 产品定位 竞争优势 客户沟通 内部协作 权限管理 信息整理 任务分配 业务信息 使用场景 应用场景 真实需求 现有系统 持续迭代 用户反馈 产品能力 产业生态 技术能力 生态资源'''.split()


def suggest_highlights(root):
    count = sum(n.tag in {'strong','b'} for n in walk(root))
    characters = len(re.sub(r'\s+', '', root.text()))
    target = max(2, min(24, (characters+94)//95))
    if count >= target:
        return 0
    seen = Counter(n.text() for n in walk(root) if n.tag in {'strong','b'})
    headings = ''.join(n.text() for n in walk(root) if n.tag.startswith('h'))
    blocks = [n for n in walk(root) if n.tag in {'p','li'} and len(n.text().strip()) >= 16 and not any(isinstance(c,Node) and c.tag in {'p','li'} for c in n.children)]
    added = 0
    # Spread the fallback across paragraphs instead of filling the first block.
    for block in blocks:
        if count+added >= target:
            break
        if any(n.tag in {'strong','b','code','pre','a'} for n in walk(block) if n is not block):
            continue
        choices = []
        for i, child in enumerate(block.children):
            if not isinstance(child, str):
                continue
            for term in TERMS:
                start = child.find(term)
                if start >= 0 and not seen[term]:
                    score = len(term) + (4 if term in headings else 0)
                    choices.append((score,i,start,term))
            for m in re.finditer(r'\d+(?:\.\d+)?\s*(?:%|亿|万)(?:人|家|次)?', child):
                if not seen[m[0]]:
                    choices.append((3,i,m.start(),m[0]))
        if not choices:
            continue
        _, index, start, term = max(choices, key=lambda x:(x[0],-x[1],-x[2]))
        text = block.children[index]
        block.children[index:index+1] = [text[:start], Node('strong', {'data-wg-highlight':'1'}, [term]), text[start+len(term):]]
        seen[term] += 1
        added += 1
    return added


def style_tree(node, input_path, output, warnings, in_card=False):
    # Drop separators entirely: a heading's blue lower border is sufficient.
    node.children = [c for c in node.children if not (isinstance(c,Node) and c.tag == 'hr')]
    if node.tag == 'blockquote':
        node.tag = 'section'
        node.attrs = {'data-wg-card':'1', 'style':CARD}
        remove_callout_marker(node)
        in_card = True
    elif node.tag == 'p':
        node.attrs = {'style': CARD_P if in_card else PARAGRAPH}
    elif node.tag in {'strong','b'}:
        node.tag = 'strong'
        node.attrs = {'data-wg-highlight':'1', 'style':HIGHLIGHT}
    elif node.tag.startswith('h') and len(node.tag)==2 and node.tag[1].isdigit():
        if node.tag == 'h1':
            node.tag = 'h2'
        node.attrs = {'style':HEADING if node.tag == 'h2' else HEADING.replace('20px','18px').replace('border-bottom:2px solid #0969da;','')}
    elif node.tag == 'a':
        node.attrs = {k:v for k,v in node.attrs.items() if k in {'href','title'}} | {'style':'color:#0969da;text-decoration:underline;'}
    elif node.tag == 'img':
        safe_image(node,input_path,output,warnings)
        node.attrs['style'] = 'max-width:100%;height:auto;display:block;margin:16px auto;'
    elif node.tag in {'ul','ol'}:
        node.attrs = {'style':'margin:0 0 18px;padding:0 0 0 24px;text-align:left;'}
    elif node.tag == 'li':
        node.attrs = {'style':'font-size:16px;line-height:1.75;margin:0 0 8px;padding:0;text-align:left;'}
    elif node.tag == 'pre':
        node.attrs = {'style':'font-size:13px;line-height:1.6;white-space:pre-wrap;word-wrap:break-word;background-color:#f6f8fa;color:#333333;padding:16px;margin:18px 0;border-radius:6px;'}
    elif node.tag == 'code':
        node.attrs = {'style':'font-family:Consolas,monospace;font-size:13px;background-color:#f6f8fa;color:#333333;'}
    elif node.tag == 'table':
        node.attrs = {'style':'width:100%;border-collapse:collapse;font-size:14px;line-height:1.7;margin:18px 0;'}
    elif node.tag in {'th','td'}:
        node.attrs = {'style':'border:1px solid #d0d7de;padding:8px;text-align:left;word-wrap:break-word;'}
    elif node.tag not in {'root','section'}:
        node.attrs = {}
    for child in node.children:
        if isinstance(child,Node):
            style_tree(child,input_path,output,warnings,in_card)
    if in_card and node.tag == 'section':
        ps = [c for c in node.children if isinstance(c,Node) and c.tag=='p']
        if ps:
            ps[-1].attrs['style'] = CARD_P.replace('margin:0 0 10px;', 'margin:0;')


def end_marker(cta=CTA):
    line_style = 'color:#9ec0db;font-size:12px;font-weight:400;letter-spacing:0;'
    return Node('section', {'data-wg-end':'1','style':f'font-family:{FONT};text-align:center;margin:32px 0 16px;padding:12px 0 16px;'}, [
        Node('p', {'style':'text-align:center;margin:0;padding:0;font-size:16px;line-height:1.8;'}, [
            Node('span',{'style':line_style},['──────']),
            Node('span',{'style':'color:#176bb7;font-size:18px;font-weight:600;letter-spacing:0;'},['　E N D　']),
            Node('span',{'style':line_style},['──────'])
        ]),
        Node('p',{'style':'text-align:center;font-size:14px;font-weight:400;line-height:1.8;color:#999999;margin:20px 0 0;padding:0;'},[cta])
    ])


def render(input_path, output, *, title=None, highlights='suggest', include_end=True, cta=CTA):
    input_path = Path(input_path).resolve()
    output = Path(output).resolve()
    if not input_path.is_file():
        raise ValueError(f'Input file does not exist: {input_path.name}')
    if input_path.suffix.lower() not in {'.md','.markdown','.txt','.docx'}:
        raise ValueError('Input must be .md, .txt or .docx')
    output.mkdir(parents=True,exist_ok=True)
    if input_path.suffix.lower()=='.docx':
        source = docx_to_markdown(input_path,output)
    else:
        source = input_path.read_text(encoding='utf-8-sig')
    detected_title, body = clean_title(source,input_path.stem)
    title = title or detected_title
    prepared = prepare_markdown(body)
    raw = markdown.markdown(prepared, extensions=['tables','fenced_code','sane_lists'])
    cleaned = bleach.clean(raw, tags=ALLOWED, attributes={'a':['href','title'],'img':['src','alt','title']}, protocols={'http','https','mailto'}, strip=True)
    parser = TreeParser()
    parser.feed(cleaned)
    root = parser.root
    auto_added = suggest_highlights(root) if highlights=='suggest' else 0
    warnings = []
    style_tree(root,input_path,output,warnings)
    if include_end:
        root.children.append(end_marker(cta))
    root.tag = 'section'
    root.attrs = {'style':WRAPPER}
    article = serialize(root)
    stats = {'characters':len(re.sub(r'\s+','',root.text())), 'highlights':sum(n.tag=='strong' for n in walk(root)), 'cards':sum(n.attrs.get('data-wg-card')=='1' for n in walk(root)), 'headings':sum(n.tag=='h2' for n in walk(root)), 'auto_highlights':auto_added}
    metadata = {'title':title,'source_name':input_path.name,'stats':stats,'highlight_mode':highlights,'warnings':list(dict.fromkeys(warnings))}
    if auto_added:
        metadata['warnings'].append('已添加规则建议高亮；它只作为无 AI 的后备选择，请在预览中按文意检查和调整。')
    template = (SKILL_DIR/'assets/editor.html').read_text(encoding='utf-8')
    metadata_json = json.dumps(metadata,ensure_ascii=False).replace('<','\\u003c').replace('&','\\u0026')
    replacements = {
        '__EDITOR_CSS__':(SKILL_DIR/'assets/editor.css').read_text(encoding='utf-8'),
        '__EDITOR_JS__':(SKILL_DIR/'assets/editor.js').read_text(encoding='utf-8'),
        '__DOCUMENT_JSON__':metadata_json,
        '__ARTICLE_HTML__':article,
    }
    # A single template pass avoids replacing marker-looking text inside an article.
    preview = re.sub('|'.join(map(re.escape,replacements)), lambda m:replacements[m[0]], template)
    (output/'preview.html').write_text(preview,encoding='utf-8')
    # This is the initial snapshot. Edits in the preview are copied/exported there.
    export_root = TreeParser()
    export_root.feed(article)
    for n in walk(export_root.root):
        n.attrs = {k:v for k,v in n.attrs.items() if not k.startswith('data-')}
    (output/'article.html').write_text(serialize(export_root.root),encoding='utf-8')
    (output/'article.md').write_text('# '+title+'\n\n'+prepared.strip()+'\n',encoding='utf-8')
    (output/'article.txt').write_text(plain_text(root).strip()+'\n',encoding='utf-8')
    (output/'metadata.json').write_text(json.dumps(metadata,ensure_ascii=False,indent=2),encoding='utf-8')
    return metadata


def main():
    parser = argparse.ArgumentParser(description='WeChat OA: GitHub 风格公众号排版与即时高亮编辑')
    parser.add_argument('--input','-i',required=True,type=Path)
    parser.add_argument('--output','-o',type=Path,default=None)
    parser.add_argument('--title')
    parser.add_argument('--highlights',choices=['preserve','suggest'],default='suggest')
    parser.add_argument('--no-end',action='store_true')
    parser.add_argument('--cta',default=CTA)
    parser.add_argument('--open',action='store_true',help='生成后在默认浏览器打开预览')
    parser.add_argument('--no-open',action='store_true',help='兼容选项，默认不自动打开')
    args = parser.parse_args()
    output = args.output or Path.cwd()/'wechat-output'/args.input.stem
    try:
        result = render(args.input,output,title=args.title,highlights=args.highlights,include_end=not args.no_end,cta=args.cta)
    except (OSError,ValueError,zipfile.BadZipFile,ET.ParseError) as exc:
        parser.exit(1,f'Unable to render article: {exc}\n')
    print(json.dumps(result,ensure_ascii=False,indent=2))
    print('Preview:',(output.resolve()/'preview.html'))
    if args.open and not args.no_open:
        webbrowser.open((output.resolve()/'preview.html').as_uri())


if __name__=='__main__':
    main()
