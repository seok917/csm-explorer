"""
가정 민감도: 계리 가정이 나빠지면 CSM(미래 이익)이 얼마나 줄어드는가
원문: raw/extra_tables.csv.gz 의 보험계약 민감도 표 (사망률 3.27%·장해질병 3.40%/2.62%·해지율 ±9.16%·사업비 2.62% 등
      감독기관 표준 충격이라 회사끼리 비교 가능)

표 모양이 회사마다 달라 '숫자 블록' 규칙으로 읽음
- 기준 블록 = [이행현금흐름(…), 보험계약마진(…)] — 재보험 전/후·원수/출재 쌍이면 4칸, 아니면 2칸. 보험계약마진 위치 k = 블록 길이 / 2
- 기준금액은 ① 별도 '기준금액' 행 또는 ② 충격 행 맨 앞(교보·농협·DB처럼)에 있음
- 충격 행의 나머지 숫자 r에서 r[k]가 기준 CSM에 가까우면 '변동 후 금액'(신한·라이나) → 변화 = r[k] − 기준, 아니면 '변동 금액'(교보·한화) → 변화 = r[k]
- 유배당·무배당 등 구분 행이 있으면 소계/합계 행을, 없으면 구분 행을 더함
- 기준 CSM이 그 회사의 기말 CSM(당기 또는 전기, ±15%: 재보험 전 금액·연결/별도 차이 허용)과 맞는 표만 채택 → 다른 회사·다른 표가 섞이지 않게
결과: data/sensitivity.csv (회사·기준일·충격별 CSM 변화 금액·비율·충격 크기)
"""
import os, re, sys
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from extract_schedule import num, UNITS

B = os.path.join(os.path.dirname(__file__), '..')
D = os.path.join(B, 'data')
SHOCKS = [('mort', '사망률 상승'), ('morb', '장해·질병(정액) 상승'), ('indem', '실손 상승'),
          ('lapse_up', '해지율 상승'), ('lapse_dn', '해지율 하락'), ('exp', '사업비 상승')]
SIZE = re.compile(r'(\d+(?:\.\d+)?)\s*(%p|%)\s*(증가|감소|상승|하락)?')


def shock_of(label, cur):
    """행 이름 → (충격 종류, 방향). 이름 없이 '3.27% 감소'만 있으면 앞 충격 종류를 이어받음"""
    t = label.replace(' ', '')
    kind = cur[0] if cur else None
    if '사망' in t:
        kind = 'mort'
    elif '실손' in t and '정액' not in t:
        kind = 'indem'
    elif re.search(r'장해|질병|위험률|이환', t):
        kind = 'morb'
    elif '해지' in t:
        kind = 'lapse'
    elif '인플레' in t and '수준' not in t:
        kind = 'infl'
    elif '사업비' in t:
        kind = 'exp'
    elif re.search(r'재물|할인|금리|대재해|기타', t):
        kind = 'skip'
    up = re.search(r'증가|상승', t)
    dn = re.search(r'감소|하락', t)
    d = 'up' if up and not dn else ('dn' if dn and not up else (cur[1] if cur and not (up or dn) else 'up'))
    m = SIZE.search(label)
    return kind, d, (float(m.group(1)) if m else None)


def rows_of(g):
    g = g.assign(row=g['row'].astype(int), col=g['col'].astype(int))
    v = g.pivot_table(index='row', columns='col', values='value', aggfunc='first').sort_index().sort_index(axis=1).fillna('')
    out = []
    for r in v.index:
        cells = [str(c).strip() for c in v.loc[r] if str(c).strip()]
        nums, label = [], []
        for c in cells:
            if SIZE.fullmatch(c.replace(' ', '')) or re.fullmatch(r'[\d.]+%p?(증가|감소|상승|하락)', c.replace(' ', '')):
                label.append(c); continue                     # '3.27% 증가' 같은 충격 크기는 숫자가 아니라 이름
            n = num(c)
            if n is None or c in ('_',):
                label.append(c)
            else:
                nums.append(n)
        out.append((' '.join(label), nums))
    return out, ' '.join(v.astype(str).values.ravel())


def parse(g, closes):
    """표 → [(기준 CSM(원래 단위), 단위, 기준일, {충격: (변화, 크기)})] 후보"""
    rows, text = rows_of(g)
    if not re.search(r'해지', text) or not re.search(r'마진|CSM', text):
        return []
    units = [u for u in UNITS if re.search(r'단위\s*[:：]?\s*\(?\s*' + u, text + ' ' + g.ctx.iloc[0])] or list(UNITS)
    res = []
    for nb in (2, 4):
        k = nb // 2
        base_row = next((n for lb, n in rows if '기준' in lb and not re.search(r'사망|해지|사업비|장해', lb) and len(n) >= nb), None)
        cur, acc, seg_base = None, {}, {}
        L0 = max((len(n) for lb, n in rows if n and re.search(r'사망|해지|장해|사업비|유배당|무배당|변액|소\s*계|합\s*계', lb)), default=0)
        for lb, n in rows:
            if base_row is not None and n is base_row:
                continue
            if not n:
                cur = shock_of(lb, cur)[:2] if re.search(r'사망|해지|사업비|장해|질병|실손|재물|인플레|할인', lb) else cur
                continue
            kind, d, size = shock_of(lb, cur)
            cur = (kind, d)
            if kind in (None, 'skip', 'infl'):
                continue
            key = kind + ('_' + d if kind == 'lapse' else '')
            if kind != 'lapse' and d == 'dn':
                continue                                       # 사망률·사업비 하락 충격은 쓰지 않음(표준: 상승)
            seg = re.sub(r'[\d.,%p()]|증가|감소|상승|하락|사망률|해지율|사업비|장해질병|정액보상|실손보상|수준|[()]', '', lb).strip()
            total_row = bool(re.search(r'소\s*계|합\s*계', lb))
            if base_row is not None:
                base, rest = base_row[k], n
            elif len(n) == L0 and L0 >= nb + 2:                 # 기준금액이 들어 있는 가장 긴 행
                base, rest = n[k], n[nb:]
            elif len(n) == L0 - nb:                            # 기준금액 칸이 병합돼 빠진 행(교보 2행부터)
                base, rest = None, n
            else:
                continue
            if base is None:
                base = seg_base.get(seg, seg_base.get('__last__'))   # 같은 구분의 앞 행에서 기준을 이어받음
            if base is None or len(rest) <= k:
                continue
            seg_base[seg] = base; seg_base['__last__'] = base
            delta = rest[k] - base if abs(rest[k] - base) < 0.5 * abs(base) and abs(rest[k]) > 0.5 * abs(base) else rest[k]
            acc.setdefault(key, []).append((seg, total_row, base, delta, size))
        if not acc:
            continue
        shocks, bases = {}, set()
        for key, items in acc.items():
            tot = [i for i in items if i[1]]
            use = tot[-1:] if tot else items
            b = sum(i[2] for i in use); dlt = sum(i[3] for i in use)
            shocks[key] = (dlt, use[0][4]); bases.add(round(b, 0))
        if len(bases) != 1:
            b = max(bases, key=abs)                              # 구분 수가 충격마다 다르면 가장 큰 기준
        else:
            b = bases.pop()
 
        if sum(1 for v in shocks.values() if v[0] == 0) >= 2:
            continue                                           # 변화가 0인 충격이 여럿이면 열을 잘못 읽은 것
        if closes is None:                                     # 포트폴리오별로 나뉜 표를 합치기 위한 원자료
            res.append(dict(nb=nb, base=b, units=units, shocks=shocks)); continue
        cand = [(abs(b * UNITS[u] / c - 1), u, w, b * UNITS[u] / c) for u in units for w, c in closes.items() if c]
        cand = [c for c in cand if c[0] <= 0.15]
        if cand:                                               # 한 표는 기말 CSM에 가장 가까운 한 시점에만 대응
            _, u, w, r = min(cand)
            res.append(dict(nb=nb, base=b, unit=u, asof=w, ratio=r, shocks=shocks))
    return res


def main():
    x = pd.read_csv(os.path.join(B, 'raw', 'extra_tables.csv.gz'), dtype=str).fillna('')
    x = x[x.kind.isin(['csm', 'bucket', 'schedule'])]
    mv = pd.read_csv(os.path.join(D, 'movement.csv'), dtype={'year': str})
    close = {}
    for r in mv.itertuples():
        close[(r.company, r.year[:4] + ('.06' if '상반기' in r.year else '.12'))] = r.close
        if r.year == '2023':
            close.setdefault((r.company, '2022.12'), r.open)
    txt = x.groupby(['co', 'period', 'rcept_no', 'doc', 'table_no']).value.apply(' '.join)
    keep = txt[txt.str.contains('해지') & txt.str.contains(r'사망|장해') & txt.str.contains(r'증가|상승|감소|하락')].index
    best = {}
    for key in keep:
        co, per, rc, doc, tno = key
        g = x[(x.co == co) & (x.rcept_no == rc) & (x.doc == doc) & (x.table_no == tno)]
        yr = int(per[:4])
        allowed = {per, f'{yr - 1}.12'}
        closes = {k[1]: v for k, v in close.items() if k[0] == co and k[1] in allowed}
        for c in parse(g, closes):
            s = c['shocks']
            if 'lapse_up' not in s or 'mort' not in s:
                continue
            scale = UNITS[c['unit']]
            pct = {k: v[0] / c['base'] for k, v in s.items()}
            if any(abs(p) > 0.6 for p in pct.values()):
                continue                                       # 기준 대비 60% 넘는 변화는 열을 잘못 읽은 것
            if 'lapse_dn' in pct and pct['lapse_up'] * pct['lapse_dn'] > 0:
                continue                                       # 해지율 상승·하락이 같은 방향이면 잘못 읽은 것
            k2 = (co, c['asof'], per)
            score = abs(c['ratio'] - 1) + (0 if c['asof'] == per else 0.001)      # 당기 표 우선, 기말 CSM에 가까운 표 우선
            if k2 not in best or score < best[k2][0]:
                best[k2] = (score, dict(company=co, asof=c['asof'], report=per, vintage='당시' if c['asof'] == per else '재공시',
                                        base=round(c['base'] * scale, 1), close=round(close[(co, c['asof'])], 1),
                                        source=f'{per} 보고서 표 {tno} · {c["unit"]}',
                                        **{f'd_{k}': round(v[0] * scale, 1) for k, v in s.items()},
                                        **{f'p_{k}': round(v[0] / c['base'], 5) for k, v in s.items()},
                                        **{f'sz_{k}': v[1] for k, v in s.items()}))
    # 포트폴리오별로 나뉜 표(신한라이프 2025 등): 같은 문서의 표 2~4개 기준 CSM 합이 기말 CSM과 0.2% 안에서 맞으면 합침
    from itertools import combinations
    for (co, per, rc, doc), grp in pd.Series(list(keep)).map(lambda k: k).groupby(lambda i: keep[i][:4]):
        yr = int(per[:4]); when = per
        if (co, when, per) in best or (co, when) not in close:
            continue
        raws = []
        for key in grp:
            g = x[(x.co == co) & (x.rcept_no == rc) & (x.doc == doc) & (x.table_no == key[4])]
            for c in parse(g, None):
                if c['nb'] == 2 and 'lapse_up' in c['shocks'] and 'mort' in c['shocks']:
                    raws.append((key[4], c))
        raws = raws[:10]
        found = None
        for n in (2, 3, 4):
            for combo in combinations(raws, n):
                if len({t for t, _ in combo}) < n:
                    continue
                for u in combo[0][1]['units']:
                    tot = sum(c['base'] for _, c in combo) * UNITS[u]
                    if abs(tot / close[(co, when)] - 1) <= 0.002:
                        found = (combo, u); break
                if found: break
            if found: break
        if not found:
            continue
        combo, u = found
        keys = set.intersection(*[set(c['shocks']) for _, c in combo])
        b = sum(c['base'] for _, c in combo); scale = UNITS[u]
        s_ = {k: (sum(c['shocks'][k][0] for _, c in combo), combo[0][1]['shocks'][k][1]) for k in keys}
        best[(co, when, per)] = (0, dict(company=co, asof=when, report=per, vintage='당시', base=round(b * scale, 1), close=round(close[(co, when)], 1),
                                         source=f"{per} 보고서 표 {'+'.join(t for t, _ in combo)} · {u} (포트폴리오 합산)",
                                         **{f'd_{k}': round(v[0] * scale, 1) for k, v in s_.items()},
                                         **{f'p_{k}': round(v[0] / b, 5) for k, v in s_.items()},
                                         **{f'sz_{k}': v[1] for k, v in s_.items()}))
    out = pd.DataFrame([v[1] for v in best.values()]).sort_values(['company', 'asof', 'report'])
    out.to_csv(os.path.join(D, 'sensitivity.csv'), index=False, encoding='utf-8-sig')
    print(f'민감도 {len(out)}건 · 회사 {out.company.nunique()}곳')
    return out


if __name__ == '__main__':
    o = main()
    pd.set_option('display.width', 250)
    cols = ['company', 'asof', 'report', 'base', 'close'] + [c for c in o.columns if c.startswith('p_')]
    print(o[o.vintage == '당시'][cols].round(4).to_string())
