# -*- coding: utf-8 -*-
"""汇总 data/*.json → data/status.json（页面数据状态栏用）。"""
import json
import datetime
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, 'data')


def last_of(path):
    if not os.path.exists(path):
        return None
    try:
        arr = json.load(open(path, encoding='utf-8'))
        return {'count': len(arr), 'last': str(arr[-1]['date'])[:10] if arr else ''}
    except Exception:  # noqa: BLE001
        return None


def main():
    st = {'updatedAt': datetime.datetime.now().astimezone().isoformat(timespec='seconds')}
    ssq = last_of(os.path.join(DATA, 'ssq.json'))
    dlt = last_of(os.path.join(DATA, 'dlt.json'))
    if ssq:
        st['ssq'] = ssq
    if dlt:
        st['dlt'] = dlt
    jcp = os.path.join(DATA, 'jingcai.json')
    if os.path.exists(jcp):
        try:
            jc = json.load(open(jcp, encoding='utf-8'))
            st['jingcai'] = {'count': len(jc.get('matches', [])), 'sources': jc.get('sources', {}),
                             'generatedAt': jc.get('generatedAt')}
        except Exception:  # noqa: BLE001
            pass
    tmp = os.path.join(DATA, 'status.json.tmp')
    json.dump(st, open(tmp, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    os.replace(tmp, os.path.join(DATA, 'status.json'))
    print(json.dumps(st, ensure_ascii=False)[:300])


if __name__ == '__main__':
    main()
