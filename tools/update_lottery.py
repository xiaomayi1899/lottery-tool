# -*- coding: utf-8 -*-
"""数字彩开奖数据更新。多源链:
  双色球: 福彩官网(cwl.gov.cn) → 500彩票历史(datachart.500.com, 海外可达)
  大乐透: 体彩官网(webapi.sporttery.cn) → 500彩票历史
仅标准库。源全挂但本地有数据时 exit 0(保持旧数据), 本地无数据且全挂才 exit 2。"""
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


def http_get(url, headers=None, retries=2, timeout=45):
    hdr = dict(UA)
    if headers:
        hdr.update(headers)
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers=hdr)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(3 * (i + 1))
    raise last


def fetch_json(url, headers=None, retries=2):
    return json.loads(http_get(url, headers, retries).decode('utf-8', 'ignore'))


# ---------------------------------------------------- 福彩官网 (国内可达)


def fetch_ssq_cwl(count=30):
    url = 'https://www.cwl.gov.cn/cwl_admin/front/cwlkj/search/kjxx/findDrawNotice?name=ssq&issueCount=%d' % count
    d = fetch_json(url, {'Referer': 'https://www.cwl.gov.cn/ygkj/wqkjgg/ssq/'})
    out = []
    for r in d.get('result', []):
        reds = [int(x) for x in str(r.get('red', '')).split(',') if x.strip()]
        blue = int(r.get('blue', 0))
        if len(reds) == 6 and blue:
            out.append({'issue': str(r['code']), 'date': str(r['date']), 'nums': reds + [blue]})
    return out


def fetch_dlt_sporttery(pages=3, page_size=30):
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


# ---------------------------------------------------- 500彩票 (海外可达)


def _get_500(url):
    raw = http_get(url, {'Referer': 'https://datachart.500.com/'}, retries=3)
    try:
        return raw.decode('gb18030', 'ignore')
    except Exception:  # noqa: BLE001
        return raw.decode('utf-8', 'ignore')


def _parse_500(html, red_n, blue_n):
    """500 历史行: [序, 期号, 前1..前n, 后1..后m, ..., 日期(末列)]"""
    out = []
    for r in re.findall(r'<tr class="t_tr1">(.*?)</tr>', html, re.S):
        cells = [re.sub(r'<[^>]+>', '', c).strip() for c in re.findall(r'<t[dh][^>]*>(.*?)</t[dh]>', r, re.S)]
        if len(cells) < red_n + blue_n + 2:
            continue
        issue = cells[1]
        try:
            nums = [int(x) for x in cells[2:2 + red_n + blue_n]]
        except ValueError:  # noqa: PERF203
            continue
        date = cells[-1]
        if len(date) < 8 or not re.match(r'^\d{4}-\d{2}-\d{2}', date):
            continue
        if red_n == 6 and len(issue) == 5:  # 双色球500用5位期号, 官方为 20+5位
            issue = '20' + issue
        out.append({'issue': issue, 'date': date, 'nums': nums})
    return out


def fetch_ssq_500():
    return _parse_500(_get_500('https://datachart.500.com/ssq/history/newinc/history.php?limit=120'), 6, 1)


def fetch_dlt_500():
    html = _get_500('https://datachart.500.com/dlt/history/newinc/history.php?limit=120')
    try:
        return _parse_500(html, 5, 2)
    except Exception:  # noqa: BLE001
        return []


# ---------------------------------------------------- 合并


def merge(existing, fresh, cap=1200):
    idx = {str(d['issue']): d for d in existing}
    for d in fresh:
        idx[str(d['issue'])] = d
    arr = list(idx.values())
    arr.sort(key=lambda d: str(d['date'])[:10])
    return arr[-cap:]


def update_game(name, chains, path):
    existing = []
    if os.path.exists(path):
        try:
            existing = json.load(open(path, encoding='utf-8'))
        except Exception:  # noqa: BLE001
            existing = []
    used, fresh, errs = '-', [], []
    for src_name, fn in chains:
        try:
            fresh = fn()
            if fresh:
                used = src_name
                break
            errs.append(src_name + ':empty')
        except Exception as e:  # noqa: BLE001
            errs.append(src_name + ':' + str(e)[:90])
    if fresh:
        existing = merge(existing, fresh)
        tmp = path + '.tmp'
        json.dump(existing, open(tmp, 'w', encoding='utf-8'), ensure_ascii=False)
        os.replace(tmp, path)
    return {'status': 'ok' if fresh else 'stale', 'source': used, 'count': len(existing),
            'errors': errs, 'new': len(fresh) if fresh else 0}


def main():
    os.makedirs(DATA, exist_ok=True)
    status = {
        'ssq': update_game('ssq', [('cwl', fetch_ssq_cwl), ('500', fetch_ssq_500)], os.path.join(DATA, 'ssq.json')),
        'dlt': update_game('dlt', [('sporttery', fetch_dlt_sporttery), ('500', fetch_dlt_500)], os.path.join(DATA, 'dlt.json')),
    }
    print(json.dumps(status, ensure_ascii=False))
    ok_any = any(v.get('status') == 'ok' for v in status.values())
    has_local = all(v.get('count', 0) > 0 for v in status.values())
    if not ok_any:
        print('WARNING: 所有开奖源失败, 使用仓库内已有数据', file=sys.stderr)
        sys.exit(0 if has_local else 2)


if __name__ == '__main__':
    main()
