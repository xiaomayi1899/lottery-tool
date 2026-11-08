# -*- coding: utf-8 -*-
"""竞彩数据更新：官方体彩 API(主) + 澳客网增强(胜负指数/百家欧赔凯利/升降赔)。仅标准库。

数据降级策略:
  official  —— webapi.sporttery.cn 竞彩足球 (赛程/胜平负/让球/排名)，稳定
  okooo_*   —— www.okooo.com 慢速抓取，任一失败不影响整体，字段留空并在 sources 标注
"""
import argparse
import datetime
import json
import os
import random
import re
import sys
import time
import urllib.request
from html.parser import HTMLParser

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, 'data')
UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')
WAF_MARK = ['aliyun_waf', '您的访问被阻断', '405']

# ---------------------------------------------------------------- HTTP


def http_get(url, headers=None, retries=2, timeout=45):
    hdr = {'User-Agent': UA, 'Accept-Language': 'zh-CN,zh;q=0.9'}
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


def is_waf(data):
    head = data[:4000].decode('utf-8', 'ignore')
    return any(m in head for m in WAF_MARK)


def polite_sleep(lo=18, hi=32):
    time.sleep(random.uniform(lo, hi))


# ---------------------------------------------------------------- 官方竞彩


def fetch_official():
    url = 'https://webapi.sporttery.cn/gateway/jc/football/getMatchCalculatorV1.qry?poolCode=hhad,had&channel=c'
    d = json.loads(http_get(url, {
        'Accept': 'application/json, text/javascript, */*; q=0.01',
        'Origin': 'https://www.sporttery.cn',
        'Referer': 'https://www.sporttery.cn/',
    }).decode('utf-8', 'ignore'))
    out = []
    for day in (d.get('value') or {}).get('matchInfoList') or []:
        for m in day.get('subMatchList') or []:
            if m.get('matchStatus') != 'Selling':
                continue
            out.append({
                'key': '%s_%s' % (m.get('matchNumDate'), m.get('matchNum')),
                'matchNumDate': m.get('matchNumDate'),
                'num': m.get('matchNumStr'),
                'date': m.get('matchDate'),
                'time': (m.get('matchTime') or '')[:5],
                'league': m.get('leagueAbbName'),
                'home': m.get('homeTeamAbbName'),
                'homeAll': m.get('homeTeamAllName'),
                'away': m.get('awayTeamAbbName'),
                'awayAll': m.get('awayTeamAllName'),
                'homeRank': parse_rank(m.get('homeRank')),
                'awayRank': parse_rank(m.get('awayRank')),
                'official': {'had': slim_odds(m.get('had')), 'hhad': slim_odds(m.get('hhad'))},
            })
    return out


def parse_rank(s):
    m = re.search(r'\d+', str(s or ''))
    return int(m.group(0)) if m else None


def slim_odds(o):
    if not o:
        return None
    def f(x):
        try:
            return float(x)
        except Exception:  # noqa: BLE001
            return None
    return {'h': f(o.get('h')), 'd': f(o.get('d')), 'a': f(o.get('a')),
            'goalLine': o.get('goalLine') or '', 'update': '%s %s' % (o.get('updateDate', ''), o.get('updateTime', ''))}


# ---------------------------------------------------------------- 澳客解析


class TableParser(HTMLParser):
    """把页面里的所有 <table> 解析成 rows[cells[{t,attrs}]]。"""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables = []
        self._stack_tbl = 0
        self._rows = None
        self._row = None
        self._cell = None

    def handle_starttag(self, tag, attrs):
        if tag == 'table':
            self._stack_tbl += 1
            if self._stack_tbl == 1:
                self._rows = []
        elif tag == 'tr' and self._stack_tbl:
            self._row = []
        elif tag in ('td', 'th') and self._stack_tbl:
            self._cell = {'t': [], 'attrs': dict(attrs)}

    def handle_endtag(self, tag):
        if tag == 'table':
            if self._stack_tbl == 1 and self._rows is not None:
                self.tables.append(self._rows)
                self._rows = None
            self._stack_tbl = max(0, self._stack_tbl - 1)
        elif tag == 'tr' and self._stack_tbl:
            if self._row is not None and self._rows is not None:
                self._rows.append(self._row)
            self._row = None
        elif tag in ('td', 'th') and self._stack_tbl:
            if self._cell is not None and self._row is not None:
                self._cell['txt'] = re.sub(r'\s+', '', ''.join(self._cell['t']))
                self._row.append(self._cell)
            self._cell = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell['t'].append(data)


def strip_tags(html):
    return re.sub(r'\s+', '', re.sub(r'<[^>]+>', '', html))


def clean_name(s):
    return re.sub(r'[!.#\s\u200b]', '', str(s or ''))


def fnum(s):
    m = re.search(r'\d+(?:\.\d+)?', str(s or ''))
    return float(m.group(0)) if m else None


def parse_peilv_page(html):
    """解析澳客凯利页(单页) → [{num, league, kick, homeRank, home, hand, away, awayRank, mid, books}]"""
    tp = TableParser()
    tp.feed(html)
    spans = [(m.start(), m.end()) for m in re.finditer(r'<table[\s\S]*?</table>', html)]
    matches = []
    prev_end = 0
    for (s, e), tb in zip(spans, tp.tables):
        if not tb or len(tb) < 6:
            prev_end = e
            continue
        title = parse_title(html[prev_end:s])
        mid = None
        for row in tb:
            for c in row:
                if 'data-matchid' in c.get('attrs', {}):
                    mid = c['attrs']['data-matchid']
                    break
            if mid:
                break
        books = []
        for row in tb:
            if len(row) < 14:
                continue
            name = clean_name(row[0].get('attrs', {}).get('data-name') or row[0].get('txt', ''))
            if not name or name in ('公司名',):
                continue
            try:
                init = [fnum(row[i]['txt']) for i in (1, 2, 3)]
                latest, arrows = [], []
                for i in (5, 6, 7):
                    t = row[i]['txt']
                    latest.append(fnum(t))
                    arrows.append(1 if '↑' in t or 'uarr' in t.lower() else (-1 if '↓' in t or 'darr' in t.lower() else 0))
                kelly = [fnum(row[i]['txt']) for i in (9, 10, 11)]
                if None in init or None in kelly:
                    continue
                books.append({'name': name, 'init': init, 'latest': latest, 'arrow': arrows, 'kelly': kelly})
            except Exception:  # noqa: BLE001
                continue
        if title and books:
            title['mid'] = int(mid) if mid else None
            title['books'] = books
            matches.append(title)
        prev_end = e
    return matches


def parse_title(seg_html):
    """从表格前的 HTML 片段解析比赛标题信息。
    例: 周日001 J联赛 11-08 13:00 [4] 町田泽维亚 (+1) VS 广岛三箭 [3]
    """
    txt = strip_tags(seg_html)
    m = re.search(r'(周[一二三四五六日]\d{3})', txt)
    if not m:
        return None
    tail = txt[m.start():]
    mt = re.search(r'(\d{1,2}-\d{1,2})(\d{1,2}:\d{2})', tail)
    ml = re.match(r'周[一二三四五六日]\d{3}(.+?)\d{1,2}-\d{1,2}\d{1,2}:\d{2}', tail)
    league = ml.group(1) if ml else ''
    rest = tail[mt.end():] if mt else tail
    parts = re.split(r'VS', rest, maxsplit=1)
    left = parts[0]
    right = parts[1] if len(parts) > 1 else ''
    rank_h = re.search(r'\[(\d+)\]', left)
    hand = re.search(r'\(([+-]?\d+)\)', left)
    home = left
    if rank_h:
        home = home[:rank_h.start()] + home[rank_h.end():]
    if hand:
        home = home[:hand.start()] + home[hand.end():]
    home = home.strip('[]|/: ')
    home = re.sub(r'[()（）\[\]]', '', home)
    home = re.sub(r'[+-]\d+$', '', home).strip()
    rank_a = re.search(r'\[(\d+)\]', right)
    away = right
    if rank_a:
        away = away[:rank_a.start()] + away[rank_a.end():]
    away = re.split(r'欧|亚|析|历史|/|伤|情报', away)[0].strip('[]|/: ')
    away = re.sub(r'[()（）\[\]]', '', away)
    away = re.sub(r'[+-]\d+$', '', away).strip()
    return {
        'num': m.group(1),
        'league': league,
        'kick': (mt.group(1) + ' ' + mt.group(2)) if mt else '',
        'homeRank': int(rank_h.group(1)) if rank_h else None,
        'home': home,
        'hand': hand.group(1) if hand else None,
        'away': away,
        'awayRank': int(rank_a.group(1)) if rank_a else None,
    }


def fetch_okooo_peilv(max_pages=2):
    """抓澳客凯利指数页(当前期竞彩)，返回 matches 列表。"""
    out = []
    for p in range(1, max_pages + 1):
        url = 'https://www.okooo.com/jingcai/shuju/peilv/dqjc/' + ('' if p == 1 else 'p%d/' % p)
        try:
            raw = http_get(url, {'Referer': 'https://www.okooo.com/jingcai/'})
            if is_waf(raw):
                raise RuntimeError('WAF challenge')
            html = raw.decode('gb18030', 'ignore')
            ms = parse_peilv_page(html)
            out.extend(ms)
            if not ms:
                break
            if p < max_pages:
                polite_sleep()
        except Exception as e:  # noqa: BLE001
            print('  [okooo peilv p%d] %s' % (p, str(e)[:120]), file=sys.stderr)
            break
    return out


def fetch_okooo_zhishu():
    """抓澳客胜负指数页 → {num: {init:[h,d,a], latest:[h,d,a], arrow:[...]}}"""
    url = 'https://www.okooo.com/jingcai/shuju/zhishu/dqjc/'
    try:
        raw = http_get(url, {'Referer': 'https://www.okooo.com/jingcai/'})
        if is_waf(raw):
            raise RuntimeError('WAF challenge')
        html = raw.decode('gb18030', 'ignore')
        tp = TableParser()
        tp.feed(html)
        for tb in tp.tables:
            if not tb or len(tb) < 3:
                continue
            out = {}
            for row in tb:
                if len(row) < 12:
                    continue
                txts = [c['txt'] for c in row]
                m = re.match(r'(周[一二三四五六日]\d{3})$', txts[0] or '')
                if not m:
                    continue
                def tri(idxs):
                    vals = []
                    arr = []
                    for i in idxs:
                        t = txts[i]
                        vals.append(fnum(t))
                        arr.append(1 if '↑' in t else (-1 if '↓' in t else 0))
                    return vals, arr
                init, _ = tri((6, 7, 8))
                latest, arrow = tri((9, 10, 11))
                if None not in init:
                    tip = []
                    for c in row[12:15]:
                        t = c.get('txt', '')
                        for k, name in ((0, '胜'), (1, '平'), (2, '负')):
                            if name in t and k not in tip:
                                tip.append(k)
                    out[m.group(1)] = {'init': init, 'latest': latest, 'arrow': arrow, 'tip': tip}
            if out:
                return out
    except Exception as e:  # noqa: BLE001
        print('  [okooo zhishu] %s' % str(e)[:120], file=sys.stderr)
    return {}


def fetch_okooo_custom(dates):
    """澳客即时指数 API → {mid: {h,d,a}}"""
    url = ('https://www.okooo.com/I/?method=lottery.match.custom&lotteryType=SportterySoccerMix'
           '&lotteryNo=%s&typeId=1&pid=24&typeid=1&format=json' % ','.join(dates))
    try:
        raw = http_get(url, {'Referer': 'https://www.okooo.com/jingcai/', 'X-Requested-With': 'XMLHttpRequest'})
        if is_waf(raw):
            raise RuntimeError('WAF challenge')
        d = json.loads(raw.decode('utf-8', 'ignore'))
        return d.get('match_custom_response') or {}
    except Exception as e:  # noqa: BLE001
        print('  [okooo custom] %s' % str(e)[:120], file=sys.stderr)
    return {}


def fetch_okooo_list():
    """澳客竞彩赛程页 → {ordercn: {mid, hname, aname, had, hhad, kick}}"""
    url = 'https://www.okooo.com/jingcai/'
    try:
        raw = http_get(url, {'Referer': 'https://www.okooo.com/'})
        if is_waf(raw):
            raise RuntimeError('WAF challenge')
        html = raw.decode('gb18030', 'ignore')
        out = {}
        blocks = re.findall(r'<div class="touzhu_1".*?(?=<div class="touzhu_1"|<div id="pingce")', html, re.S)
        for b in blocks:
            mid = re.search(r'data-mid="(\d+)"', b)
            num = re.search(r'data-ordercn="([^"]+)"', b)
            if not mid or not num:
                continue
            kick = re.search(r'比赛时间[:：]([\d\-]+ \d{2}:\d{2})', b)
            sps = re.findall(r'data-sp="(\d+\.\d+)"', b)
            item = {'mid': int(mid.group(1)), 'num': num.group(1),
                    'kick': kick.group(1) if kick else ''}
            if len(sps) >= 6:
                item['had'] = [float(x) for x in sps[0:3]]
                item['hhad'] = [float(x) for x in sps[3:6]]
            elif len(sps) >= 3:
                item['had'] = [float(x) for x in sps[0:3]]
            out[num.group(1)] = item
        return out
    except Exception as e:  # noqa: BLE001
        print('  [okooo list] %s' % str(e)[:120], file=sys.stderr)
    return {}


# ---------------------------------------------------------------- 合成


KEY_BOOKS = ('威廉希尔', '立博', 'Bet365', '澳门彩票', 'Pinnacle', '皇冠', '香港马会', '竞彩官方', 'Interwetten', '易胜博')


def avg(vals):
    xs = [v for v in vals if isinstance(v, (int, float))]
    return round(sum(xs) / len(xs), 3) if xs else None


def build(matches_official, peilv, zhishu, custom, oklist, prev):
    prev_map = {m['key']: m for m in (prev or {}).get('matches', [])}
    peilv_by_num = {}
    for pm in peilv:
        peilv_by_num.setdefault(pm['num'], pm)
    out = []
    now = datetime.datetime.now().astimezone().isoformat(timespec='seconds')
    for m in matches_official:
        num = m['num']
        pv = peilv_by_num.get(num) or {}
        zs = zhishu.get(num) or {}
        ok = oklist.get(num) or {}
        ci = custom.get(str(ok.get('mid') or pv.get('mid') or '')) or {}
        rec = {
            'key': m['key'], 'num': num, 'matchNumDate': m['matchNumDate'],
            'date': m['date'], 'time': m['time'], 'league': m['league'],
            'home': m['homeAll'] or m['home'], 'away': m['awayAll'] or m['away'],
            'homeRank': m['homeRank'], 'awayRank': m['awayRank'],
            'official': m['official'],
            'okooo': {
                'mid': ok.get('mid') or pv.get('mid'),
                'odds': {'had': ok.get('had'), 'hhad': ok.get('hhad')} if ok else None,
                'index': zs or None,
                'liveIndex': {k: fnum(ci.get(k)) for k in ('home', 'draw', 'away')} if ci else None,
                'books': pv.get('books'),
                'hand': pv.get('hand'),
            },
            'history': [],
        }
        # 聚合: 百家均值/升降统计
        books = pv.get('books') or []
        if books:
            rec['okooo']['avg'] = {
                'init': [avg([b['init'][i] for b in books]) for i in range(3)],
                'latest': [avg([b['latest'][i] for b in books if b.get('latest') and b['latest'][i] is not None]) for i in range(3)],
                'kelly': [avg([b['kelly'][i] for b in books]) for i in range(3)],
                'n': len(books),
            }
            # 只保留关键公司明细，控制 JSON 体积
            keep = [b for b in books if any(k in b['name'] for k in KEY_BOOKS)]
            if keep:
                rec['okooo']['books'] = keep[:10]
            ud = {}
            for b in books:
                for i, a in enumerate(b.get('arrow') or []):
                    k = ('h', 'd', 'a')[i] + ('Up' if a > 0 else 'Down')
                    ud[k] = ud.get(k, 0) + (1 if a else 0)
            rec['okooo']['upDown'] = ud
        # 官方赔率历史快照
        old = prev_map.get(m['key'])
        hist = (old or {}).get('history') or []
        had, hhad = m['official'].get('had'), m['official'].get('hhad')
        if had:
            last = hist[-1] if hist else None
            changed = (not last) or (last.get('had') or {}).get('h') != had.get('h') or \
                      (last.get('had') or {}).get('d') != had.get('d') or (last.get('had') or {}).get('a') != had.get('a')
            if changed:
                hist.append({'t': now, 'had': had, 'hhad': hhad})
                hist = hist[-24:]
        rec['history'] = hist
        out.append(rec)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--skip-okooo', action='store_true', help='只抓官方数据(调试用)')
    ap.add_argument('--no-sleep', action='store_true', help='调试: 缩短澳客请求间隔')
    args = ap.parse_args()
    if args.no_sleep:
        global polite_sleep
        polite_sleep = lambda lo=0.5, hi=1.2: time.sleep(random.uniform(lo, hi))  # noqa: E731

    os.makedirs(DATA, exist_ok=True)
    jpath = os.path.join(DATA, 'jingcai.json')
    prev = {}
    if os.path.exists(jpath):
        try:
            prev = json.load(open(jpath, encoding='utf-8'))
        except Exception:  # noqa: BLE001
            prev = {}

    sources = {'official': 'error', 'okooo_list': '-', 'okooo_peilv': '-', 'okooo_zhishu': '-', 'okooo_custom': '-'}

    matches_official = fetch_official()
    if matches_official:
        sources['official'] = 'ok'
    print('official matches: %d' % len(matches_official), file=sys.stderr)

    peilv, zhishu, custom, oklist = [], {}, {}, {}
    if matches_official and not args.skip_okooo:
        polite_sleep(random.uniform(3, 6), random.uniform(8, 12))
        oklist = fetch_okooo_list()
        sources['okooo_list'] = 'ok' if oklist else 'fail'
        polite_sleep()
        peilv = fetch_okooo_peilv(2)
        sources['okooo_peilv'] = 'ok(%d)' % len(peilv) if peilv else 'fail'
        polite_sleep()
        zhishu = fetch_okooo_zhishu()
        sources['okooo_zhishu'] = 'ok(%d)' % len(zhishu) if zhishu else 'fail'
        polite_sleep()
        dates = sorted({m['date'] for m in matches_official})
        custom = fetch_okooo_custom(dates)
        sources['okooo_custom'] = 'ok(%d)' % len(custom) if custom else 'fail'

    matches = build(matches_official, peilv, zhishu, custom, oklist, prev)
    doc = {
        'generatedAt': datetime.datetime.now().astimezone().isoformat(timespec='seconds'),
        'sources': sources,
        'matches': matches,
    }
    tmp = jpath + '.tmp'
    json.dump(doc, open(tmp, 'w', encoding='utf-8'), ensure_ascii=False)
    os.replace(tmp, jpath)
    print(json.dumps({'generatedAt': doc['generatedAt'], 'sources': sources,
                      'matches': len(matches), 'withBooks': sum(1 for m in matches if (m['okooo'] or {}).get('books'))},
                     ensure_ascii=False))


if __name__ == '__main__':
    main()
