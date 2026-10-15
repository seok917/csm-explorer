"""
CSM 상각(인식) 예정표 추출·평가
원문: raw/extra_tables.csv.gz 의 schedule 표 ('보험계약마진이 미래에 당기손익으로 인식될 것으로 기대되는 금액')

1) 표 해석: 기간 구간 머리글(1년 이내, 1~2년, …, 10년 초과)을 (시작, 끝) 연수로 바꾸고
   각 행(보험계약자산·부채·합계 등)과 '행 합'을 후보로 둔 뒤, 단위(백만원·천원·원·억원)를 바꿔 가며
   합계가 그 회사의 어느 시점 기말 CSM(변동표)과 맞는지 찾음 → 맞는 시점이 그 표의 기준일
   (같은 보고서에 당기·전기 표가 함께 있어도 합계로 구분됨. 재보험 표는 맞지 않아 자연히 빠짐)
2) 지표: 1년 내·5년 내·10년 초과 비중, 절반이 인식되는 시점(구간 안 선형 보간)
3) 백테스트: 연말 예정표의 '1년 이내' 금액 vs 다음 해 실제 상각액
   (실제 상각에는 다음 해 신계약분·이자부리 효과가 섞이므로 비율이 1보다 큰 게 정상 → 수준보다 안정성을 봄)
결과: data/schedule.csv (회사·기준일·구간별 금액), data/schedule_eval.csv (지표·백테스트)
"""
import os, re, sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
B = os.path.join(os.path.dirname(__file__), '..')
D = os.path.join(B, 'data')
SRC = os.path.join(B, 'raw', 'extra_tables.csv.gz')
STD = [(0, 1), (1, 2), (2, 5), (5, 10), (10, np.inf)]
STD_LB = ['1년 이내', '1~2년', '2~5년', '5~10년', '10년 초과']
UNITS = {'백만원': 0.01, '천원': 1e-5, '억원': 1.0, '원': 1e-8}      # → 억 원
INF_WORD = re.compile(r'초과|이상|이후|후')


def num(s):
    s = str(s).replace(',', '').replace(' ', '')
    if s in ('', '-', '–', '−'):
        return 0.0
    neg = s.startswith('(') and s.endswith(')') or s.startswith('△') or s.startswith('-') or s.startswith('−')
    s = re.sub(r'[()△\-−]', '', s)
    try:
        v = float(s)
    except ValueError:
        return None
    return -v if neg else v


def buckets(labels):
    """머리글 → [(시작, 끝)] / 기간이 아닌 칸은 None. '합계'는 'total'"""
    out, prev = [], 0
    for lb in labels:
        t = lb.replace(' ', '')
        if re.search(r'합\s*계|총\s*계|계$', t) and not re.search(r'\d', t):
            out.append('total'); continue
        ns = [float(x) for x in re.findall(r'(\d+(?:\.\d+)?)\s*년', t)]
        if not ns:
            out.append(None); continue
        if len(ns) == 1 and INF_WORD.search(t) and not re.search(r'이내|이하|미만', t):
            out.append((ns[0], np.inf)); prev = np.inf; continue
        lo, hi = (min(ns), max(ns)) if len(ns) > 1 else (prev, ns[0])
        out.append((lo, hi)); prev = hi
    return out


def to_std(vec):
    """[(구간, 금액)] → 표준 5구간 금액 (구간이 표준 경계를 가로지르면 그 표준 구간은 None)"""
    res = [0.0] * len(STD)
    ok = [True] * len(STD)
    for (lo, hi), v in vec:
        hit = [i for i, (a, b) in enumerate(STD) if lo >= a and hi <= b]
        if hit:
            res[hit[0]] += v
        else:                                           # 예: 3~10년 → 2~5, 5~10 둘 다 불완전
            for i, (a, b) in enumerate(STD):
                if lo < b and hi > a:
                    ok[i] = False
    return [r if o else None for r, o in zip(res, ok)]


def cum_share(vec, total, h):
    """h년 안에 인식될 비중 — 경계가 정확히 있을 때만"""
    if not any(abs(hi - h) < 1e-9 for (lo, hi), _ in vec):
        return None
    return sum(v for (lo, hi), v in vec if hi <= h + 1e-9) / total


def half_point(vec, total):
    run = 0.0
    for (lo, hi), v in sorted(vec):
        if run + v >= total / 2:
            if hi == np.inf:
                return np.inf
            return lo + (hi - lo) * (total / 2 - run) / v if v else lo
        run += v
    return None


def parse_table(g):
    v = g.pivot_table(index='row', columns='col', values='value', aggfunc='first').fillna('')
    unit_txt = ' '.join(v.astype(str).values.ravel()) + ' ' + g.ctx.iloc[0]
    units = [u for u in UNITS if re.search(r'단위\s*[:：]?\s*' + u, unit_txt)] or list(UNITS)
    hdr = None
    for r in v.index:
        bk = buckets([str(x) for x in v.loc[r]])
        if sum(isinstance(b, tuple) for b in bk) >= 2:
            hdr = r; break
    if hdr is None:
        return []
    bk = buckets([str(x) for x in v.loc[hdr]])
    cols = [c for c, b in zip(v.columns, bk) if isinstance(b, tuple)]
    tcol = next((c for c, b in zip(v.columns, bk) if b == 'total'), None)
    rows = []
    for r in v.index[v.index > hdr]:
        vals = [num(v.loc[r, c]) for c in cols]
        if any(x is None for x in vals):
            continue
        rows.append((str(v.loc[r, v.columns[0]]), vals, num(v.loc[r, tcol]) if tcol else None))
    cands = [(nm, vals) for nm, vals, _ in rows]
    body = [(nm, vals) for nm, vals, _ in rows if not re.search(r'합\s*계|총\s*계', nm)]
    if len(body) > 1:
        cands.append(('행 합', [sum(x) for x in zip(*[b[1] for b in body])]))
    spans = [b for b in bk if isinstance(b, tuple)]
    return [(nm, list(zip(spans, vals)), units) for nm, vals in cands]


def main():
    x = pd.read_csv(SRC, dtype=str).fillna('')
    x = x[x.kind.isin(['schedule', 'bucket'])]
    mv = pd.read_csv(os.path.join(D, 'movement.csv'), dtype={'year': str})
    asof = {}
    for r in mv.itertuples():                    # 기준일 → 기말 CSM (2023 기초 = 2022.12 기말)
        end = r.year[:4] + ('.06' if '상반기' in r.year else '.12')
        asof[(r.company, end)] = r.close
        if r.year == '2023':
            asof.setdefault((r.company, '2022.12'), r.open)
    found = {}
    for (co, per, rc, doc, tno), g in x.groupby(['co', 'period', 'rcept_no', 'doc', 'table_no'], sort=False):
        targets = {k[1]: v for k, v in asof.items() if k[0] == co}
        for nm, vec, units in parse_table(g):
            raw = sum(v for _, v in vec)
            for u in units:
                tot = raw * UNITS[u]
                for when, close in targets.items():
                    err = abs(tot - close)
                    if err <= max(1.0, abs(close) * 0.002):
                        k = (co, when)
                        if k not in found or err < found[k]['err']:
                            found[k] = dict(company=co, asof=when, report=per, table=int(tno), row=nm, unit=u, err=round(err, 2),
                                            total=round(tot, 1), vec=[(s, v * UNITS[u]) for s, v in vec])
    sched, ev = [], []
    for (co, when), f in sorted(found.items()):
        vec, tot = f['vec'], f['total']
        std = to_std(vec)
        sched.append(dict(company=co, asof=when, total=tot, **{lb: (None if s is None else round(s, 1)) for lb, s in zip(STD_LB, std)},
                          buckets=' | '.join(f"{lo:g}~{'' if hi == np.inf else f'{hi:g}'}년 {v:,.0f}" for (lo, hi), v in vec),
                          source=f"{f['report']} 보고서 표 {f['table']} · {f['row']} · {f['unit']}", err=f['err']))
        hp = half_point(vec, tot)
        ev.append(dict(company=co, asof=when, total=tot, s1=cum_share(vec, tot, 1), s5=cum_share(vec, tot, 5),
                       s10p=None if cum_share(vec, tot, 10) is None else 1 - cum_share(vec, tot, 10),
                       half=None if hp is None else (99.0 if hp == np.inf else round(hp, 2)),
                       pred1=sum(v for (lo, hi), v in vec if hi <= 1 + 1e-9) if cum_share(vec, tot, 1) is not None else None))
    S, E = pd.DataFrame(sched), pd.DataFrame(ev)
    # 백테스트: 연말 예정 1년 이내 vs 다음 해 실제 상각(연간)
    rel = {(r.company, r.year): -r.release for r in mv.itertuples()}
    E['actual_next'] = [rel.get((r.company, str(int(r.asof[:4]) + 1))) if r.asof.endswith('.12') else None for r in E.itertuples()]
    E['bt_ratio'] = E.actual_next / E.pred1
    S.to_csv(os.path.join(D, 'schedule.csv'), index=False, encoding='utf-8-sig')
    E.round(4).to_csv(os.path.join(D, 'schedule_eval.csv'), index=False, encoding='utf-8-sig')
    print(f'예정표 {len(S)}건 (회사 {S.company.nunique() if len(S) else 0}곳) · 백테스트 {E.bt_ratio.notna().sum()}건')
    return S, E


if __name__ == '__main__':
    S, E = main()
    print(E.round(3).to_string())
