"""讲稿格式解析（网页和命令行共用）。

    ## P01｜页面标题          ← 页码对应课件第几页，“｜”是全角竖线
    ### 口播正文              ← 朗读
    ### 讲解提示（不朗读）     ← 其他小节都不读
    ### 过渡                  ← 朗读
"""
import re

def sentences(para):
    """配音与读法预览使用同一套分句规则。"""
    return [s for s in re.split(r'(?<=[。？！])', para) if s.strip()]


def read_text(raw: bytes) -> str:
    for enc in ('utf-8-sig', 'gb18030'):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            pass
    raise ValueError('讲稿编码无法识别，请另存为 UTF-8')

def parse(src: str):
    """返回 (标题, 页列表)。每页 {'no','title','paras','chars','has_body'}；paras 是要朗读的段落。"""
    src = src.replace('\r\n', '\n')
    pages = []
    for mm in re.finditer(r'^## P(\d+)｜(.*?)\n(.*?)(?=^## |\Z)', src, re.S | re.M):
        blk = mm.group(3)
        body = re.search(r'### 口播正文\n(.*?)(?=\n### |\Z)', blk, re.S)
        tr = re.search(r'### 过渡\n(.*?)(?=\n### |\Z)', blk, re.S)
        paras = [p.strip() for x in (body, tr) if x for p in x.group(1).strip().split('\n') if p.strip()]
        pages.append({'no': int(mm.group(1)), 'title': mm.group(2).strip(), 'paras': paras,
                      'chars': len(re.findall(r'[一-鿿A-Za-z0-9]', ''.join(paras))), 'has_body': bool(body)})
    title = re.search(r'^# (.+)$', src, re.M)
    return (title.group(1).strip() if title else ''), pages
