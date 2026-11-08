# -*- coding: utf-8 -*-
"""数字彩开奖数据更新：双色球(福彩官网) + 大乐透(体彩官网)。仅用标准库。"""
import json
import os
import re
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, 'data')
UA = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
      'Accept': 'application/json, text/javascript, */*; q=0.01'}


def fetch_json(url, headers=None, retries=3):
    hdr = dict(UA)
    if headers:
        hdr.update(headers)
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers=hdr)
            with urllib.request.urlopen(req, timeout=45) as r:
                return json.loads(r.read().decode('utf-8', 'ignore'))
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(4 * (i + 1))
    raise last


WD = ['一', '二', '三', '四', '五', '六', '日']


def week_cn(datestr):
    try:
        import datetime
        d = datetime.date.fromisoformat(datestr)
        return '周' + WD[d.weekday()]
    except Exception:  # noqa: BLE001
        return ''


def fetch_ssq(count=30):
    """福彩官网：双色球最新开奖，返回 [{issue,date,nums}]"""
    url = 'https://www.cwl.gov.cn/cwl_admin/front/cwlkj/search/kjxx/findDrawNotice?name=ssq&issueCount=%d' % count
    d = fetch_json(url, {'Referer': 'https://www.cwl.gov.cn/ygkj/wqkjgg/ssq/'})
    out = []
    for r in d.get('result', []):
        reds = [int(x) for x in str(r.get('red', '')).split(',') if x.strip()]
        blue = int(r.get('blue', 0))
        if len(reds) == 6 and blue:
            out.append({'issue': str(r['code']), 'date': str(r['date']), 'nums': reds + [blue]})
    return out


def fetch_dlt(pages=3, page_size=30):
    """体彩官网：大乐透最新开奖，返回 [{issue,date,nums}]"""
    out = []
    for p in range(1, pages + 1):
        url = ('https://webapi.sporttery.cn/gateway/lottery/getHistoryPageListV1.qry'
               '?gameNo=85&provinceId=0&pageSize=%d&isVerify=1&pageNo=%d' % (page_size, p))
        d = fetch_json(url, {'Origin': 'https://static.sporttery.cn', 'Referer': 'https://static.sporttery.cn/'})
        lst = (d.get('value') or {}).get('list') or []
        for r in lst:
            nums = [int(x) for x in str(r.get('lotteryDrawResult', '')).split() if x.strip()]
            if len(nums) == 7:
                out.append({'issue': str(r['lotteryDrawNum']), 'date': str(r['lotteryDrawTime']), 'nums': nums})
        if not lst:
            break
        time.sleep(2)
    return out


def merge(existing, fresh, cap=1200):
    """按 issue 合并去重，保持开奖时间升序。兼容旧格式日期带(周X)后缀。"""
    def key(d):
        return str(d['issue'])
    idx = {key(d): d for d in existing}
    for d in fresh:
        idx[key(d)] = d
    arr = list(idx.values())
    arr.sort(key=lambda d: str(d['date'])[:10])
    return arr[-cap:]


def update_game(name, fetcher, path):
    existing = []
    if os.path.exists(path):
        try:
            existing = json.load(open(path, encoding='utf-8'))
        except Exception:  # noqa: BLE001
            existing = []
    fresh = fetcher()
    if not fresh:
        return name, len(existing), 0, 'no-fresh'
    arr = merge(existing, fresh)
    tmp = path + '.tmp'
    json.dump(arr, open(tmp, 'w', encoding='utf-8'), ensure_ascii=False)
    os.replace(tmp, path)
    return name, len(existing), len(arr), 'ok'


def main():
    os.makedirs(DATA, exist_ok=True)
    status = {}
    for name, fn, path in [
        ('ssq', lambda: fetch_ssq(30), os.path.join(DATA, 'ssq.json')),
        ('dlt', lambda: fetch_dlt(3, 30), os.path.join(DATA, 'dlt.json')),
    ]:
        try:
            name, before, after, st = update_game(name, fn, path)
            status[name] = {'status': st, 'count': after, 'before': before}
        except Exception as e:  # noqa: BLE001
            status[name] = {'status': 'error', 'error': str(e)[:200],
                            'count': len(json.load(open(path, encoding='utf-8'))) if os.path.exists(path) else 0}
    print(json.dumps(status, ensure_ascii=False))
    # 非零退出仅在两个源全失败且本地无数据时
    ok_any = any(v.get('status') == 'ok' for v in status.values())
    if not ok_any:
        sys.exit(2)


if __name__ == '__main__':
    main()
