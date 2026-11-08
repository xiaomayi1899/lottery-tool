# -*- coding: utf-8 -*-
"""把 data/*.json 快照注入 index.html 的 EMBED 区(幂等: 重复运行只刷新数据)。

用法: python tools/build_embed.py
页面上 fetch data/*.json 正常时永远用不到快照;快照只在首次部署/网络异常时兜底。
"""
import io
import json
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HTML = os.path.join(ROOT, 'index.html')


def load(path):
    p = os.path.join(ROOT, path)
    if not os.path.exists(p):
        return 'null'
    try:
        return json.dumps(json.load(open(p, encoding='utf-8')), ensure_ascii=False,
                          separators=(',', ':'))
    except Exception:  # noqa: BLE001
        return 'null'


def main():
    with io.open(HTML, encoding='utf-8') as f:
        html = f.read()
    pat = re.compile(r'const EMBED = \{[\s\S]*?\};\r?\n', re.M)
    new = ('const EMBED = {\r\n'
           '  ssq: /*__SSQ__*/' + load('data/ssq.json') + '/*__SSQ_END__*/,\r\n'
           '  dlt: /*__DLT__*/' + load('data/dlt.json') + '/*__DLT_END__*/,\r\n'
           '  jingcai: /*__JC__*/' + load('data/jingcai.json') + '/*__JC_END__*/\r\n'
           '};\r\n')
    if pat.search(html):
        html = pat.sub(lambda m: new, html, count=1)
        print('EMBED 区已刷新')
    else:
        raise SystemExit('未找到 EMBED 区, 请检查 index.html')
    with io.open(HTML, 'w', encoding='utf-8', newline='') as f:
        f.write(html)
    print('index.html size:', os.path.getsize(HTML))


if __name__ == '__main__':
    main()
