# -*- coding: utf-8 -*-
"""将《衣扫产品功能与实现逻辑分析》按顶级章节（## 一、~ ## 二十一、）切片。"""
import pathlib
import sys

SRC = pathlib.Path(sys.argv[1])
OUT = pathlib.Path(sys.argv[2])

lines = SRC.read_text(encoding='utf-8').splitlines(keepends=True)
starts = [i for i, ln in enumerate(lines) if ln.startswith('## ')]
OUT.mkdir(parents=True, exist_ok=True)

index_rows = []
title_block = ''.join(lines[:starts[0]]).strip()
if title_block:
    (OUT / '00-标题.md').write_text(title_block + '\n', encoding='utf-8')
    index_rows.append(f"- [00-标题.md](00-标题.md) — 原文第 1–{starts[0]} 行")

def sanitize(name):
    for ch in '\\/:*?"<>|':
        name = name.replace(ch, ' ')
    return name.strip()

for n, i in enumerate(starts):
    end = starts[n + 1] if n + 1 < len(starts) else len(lines)
    heading = lines[i].lstrip('#').strip()
    if '、' in heading:
        num, rest = heading.split('、', 1)
    else:
        num, rest = str(n + 1), heading
    fname = sanitize(f'{n + 1:02d}-{num}-{rest}') + '.md'
    body = ''.join(lines[i:end]).rstrip() + '\n'
    (OUT / fname).write_text(body, encoding='utf-8')
    index_rows.append(f"- [{fname}]({fname}) — 原文第 {i + 1}–{end} 行（{heading}）")

(OUT / 'README-切片索引.md').write_text(
    '# 衣扫产品分析文档切片索引\n\n'
    f'来源：`衣扫产品功能与实现逻辑分析.md`（共 {len(lines)} 行），'
    f'按 {len(starts)} 个顶级章节切分，内容与原文逐字一致。\n\n'
    + '\n'.join(index_rows) + '\n', encoding='utf-8')
print('done:', len(starts), 'sections')
