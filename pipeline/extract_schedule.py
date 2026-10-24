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
import seg
B = seg.BASE
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
        if (re.search(r'합\s*계|총\s*계|계$', t) or '현재가치' in t) and not re.search(r'\d', t):
            out.append('total'); continue
        ns = [float(x) for x in re.findall(r'(\d+(?:\.\d+)?)\s*년', t)]
        if not ns:
            out.append(None); continue
        if len(ns) == 1 and INF_WORD.search(t) and not re.search(r'이내|이하|미만', t):
            out.append((ns[0], np.inf)); prev = np.inf; continue
        if len(ns) > 1:                                 # '1년초과~2년이내'는 (1, 2), '11년~15년'처럼 연차를 세는 표기는 앞 구간에 이어 (10, 15)
            lo, hi = min(ns), max(ns)
            if '초과' not in t and prev and lo == prev + 1:
                lo = prev
        else:
            lo, hi = prev, ns[0]
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


LABEL = re.compile(r'\d+\s*년|합\s*계|총\s*계|현재가치')
BAD_ROW = re.compile(r'위험보험료|예상보험금|보험금|사고|약정|유가증권|손실요소')


def fmt_of(vec):
    """예정표 형식: 30년 안팎까지 1년 단위가 이어지면 '연차별'(2024년 이후 표준), 아니면 '구간'"""
    return '연차별' if sum(1 for (lo, hi), _ in vec if hi - lo == 1) >= 8 else '구간'


def grid(g):
    """표 → 머리글의 값 열 이름(기간·합계)과 행별 (이름, 숫자들) — 병합 셀로 칸 위치가 어긋나도 숫자 순서로 읽음"""
    g = g.assign(row=g['row'].astype(int), col=g['col'].astype(int))
    v = g.pivot_table(index='row', columns='col', values='value', aggfunc='first').sort_index().sort_index(axis=1).fillna('')
    unit_txt = ' '.join(v.astype(str).values.ravel()) + ' ' + g.ctx.iloc[0]
    units = [u for u in UNITS if re.search(r'단위\s*[:：]?\s*' + u, unit_txt)] or list(UNITS)
    hdr = next((r for r in v.index if sum(isinstance(b, tuple) for b in buckets([str(x) for x in v.loc[r]])) >= 2), None)
    if hdr is None:
        return None
    labels = [str(c).strip() for c in v.loc[hdr] if LABEL.search(str(c)) and num(str(c)) is None]
    rows = []
    for r in v.index[v.index > hdr]:
        cells = [str(c).strip() for c in v.loc[r]]
        nums = [num(c) for c in cells if c and num(c) is not None]
        if nums:
            rows.append((' '.join(c for c in cells if c and num(c) is None), nums))
    lens = pd.Series([len(n) for _, n in rows])
    if len(lens) and lens.mode().iloc[0] == len(labels) + 1 and buckets(labels)[-1:] != ['total']:
        labels = labels + ['합계(머리글 없음)']                # 행마다 숫자가 하나 더 있으면 끝 열은 이름 없는 합계 열
    return dict(labels=labels, rows=rows, units=units, text=unit_txt)


def joined(prev, cur):
    """가로로 긴 예정표가 원문에서 두 표(1~8년 / 9년~)로 쪼개진 경우 이어 붙임
    조건: 뒤 표의 첫 기간이 1년보다 뒤에서 시작하고(예: '9년', '8년 초과 9년 이하'), 앞 표 마지막 기간과 이어지며, 행 수가 같음"""
    if not prev or not prev['labels'] or not cur['labels']:
        return None
    n_prev = [int(v) for v in re.findall(r'(\d+)\s*년', prev['labels'][-1])]
    n_cur = [int(v) for v in re.findall(r'(\d+)\s*년', cur['labels'][0])]
    if not n_prev or not n_cur or min(n_cur) <= 1 or min(n_cur) not in (max(n_prev), max(n_prev) + 1):
        return None
    if buckets(prev['labels'])[-1] == 'total':
        return None
    pr = [r for r in prev['rows'] if len(r[1]) == len(prev['labels'])]
    cr = [r for r in cur['rows'] if len(r[1]) == len(cur['labels'])]
    if len(pr) != len(cr) or not pr:
        return None
    return dict(labels=prev['labels'] + cur['labels'], rows=[(a[0], a[1] + b[1]) for a, b in zip(pr, cr)],
                units=prev['units'], text=prev['text'] + ' ' + cur['text'])


def candidates(G):
    """[(행 이름, [(구간, 금액)], 합계 열 값, 단위 후보)]"""
    bk = buckets(G['labels'])
    spans = [b for b in bk if isinstance(b, tuple)]
    has_tot = bool(bk) and bk[-1] == 'total'
    need = len(spans) + (1 if has_tot else 0)
    if len(spans) < 4 or len(bk) != need:
        return []
    out = [(nm, list(zip(spans, nums[:len(spans)])), nums[-1] if has_tot else None)
           for nm, nums in G['rows'] if len(nums) == need and not BAD_ROW.search(nm)]
    body = [o for o in out if not re.search(r'합\s*계|총\s*계|소\s*계', o[0])]
    if len(body) > 1:
        out.append(('행 합', [(s_, sum(o[1][i][1] for o in body)) for i, s_ in enumerate(spans)],
                    sum(o[2] for o in body) if has_tot else None))
    return [(nm, vec, tot, G['units']) for nm, vec, tot in out]


def summarize(E, mv):
    """'믿을 만한 지표인가' 판단 근거 — 안정성·변별력·순위 예측력·금액 예측력·기준 변경"""
    import json
    E = E.rename(columns={'asof': 'at'})
    ALL = E
    E = E[E.main_fmt]                       # 회사의 주 형식끼리만 비교(형식이 다른 표는 다른 산출 기준)
    L = E[E.vintage == '당시']
    a, b = L[L['at'] == '2024.12'].set_index('company'), L[L['at'] == '2025.12'].set_index('company')
    both = a.index.intersection(b.index)
    d1 = (b.loc[both, 's1'] - a.loc[both, 's1']).abs()
    d10 = (b.loc[both, 's10p'] - a.loc[both, 's10p']).abs().dropna()
    x25 = b.dropna(subset=['s1'])
    mv = mv.assign(rr=-mv.release / (mv.close - mv.release))     # 화면의 상각률과 같은 식
    m = a.join(mv[mv.year == '2025'].set_index('company').rr, how='inner').dropna(subset=['s1', 'rr'])
    bt = L[L.bt_ratio.notna()].groupby('at').bt_ratio.median()
    R = ALL[ALL['at'] == '2023.12'].pivot_table(index='company', columns='report', values='s1')   # 기준 변경 탐지는 형식 구분 없이
    chg = [c for c in R.index if '2023.12' in R and '2024.12' in R and pd.notna(R.loc[c, '2023.12']) and pd.notna(R.loc[c, '2024.12'])
           and abs(R.loc[c, '2023.12'] - R.loc[c, '2024.12']) > 0.005]
    out = dict(n_company=int(E.company.nunique()), n_stable=int(len(both)),
               d_s1_median=round(float(d1.median()), 4), d_s10_median=round(float(d10.median()), 4),
               s1_range=[round(float(x25.s1.min()), 4), round(float(x25.s1.max()), 4)],
               s10_range=[round(float(x25.s10p.min()), 4), round(float(x25.s10p.max()), 4)], n_2025=int(len(x25)),
               s1_min_co=x25.s1.idxmin(), s1_max_co=x25.s1.idxmax(),
               rank_corr=round(float(m.s1.corr(m.rr, method='spearman')), 2), n_rank=int(len(m)),
               bt_median={k: round(float(v), 2) for k, v in bt.items()},
               bt_range={k: [round(float(g.min()), 2), round(float(g.max()), 2)] for k, g in L[L.bt_ratio.notna()].groupby('at').bt_ratio},
               restated_2023=sorted(chg))
    json.dump(out, open(os.path.join(D, 'schedule_summary.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    return out


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
    found, undisc, cands = {}, {}, {}
    x = x.assign(tn=x.table_no.astype(int)).sort_values(['co', 'period', 'rcept_no', 'doc', 'tn'])
    last = {}
    for (co, per, rc, doc, tno), g in x.groupby(['co', 'period', 'rcept_no', 'doc', 'tn'], sort=False):
        yr = int(per[:4])
        targets = {k[1]: v for k, v in asof.items() if k[0] == co and k[1] in (per, f'{yr - 1}.12') and v >= 10}   # 그 보고서의 당기·전기 시점만
        # (CSM 10억 원 미만 — 계약이전으로 0이 된 해, 장기보험이 거의 없는 디지털 손보 — 은 합계 허용오차(1억)보다 의미 있는 대응이 어려워 제외)
        G = grid(g)
        if G is None:
            continue
        pk = (co, per, rc, doc)
        prev = last.get(pk); last[pk] = dict(G, tn=tno)
        J = joined(prev, G) if prev and 0 < tno - prev['tn'] <= 3 else None     # 사이에 주석 표가 끼어 있을 수 있음
        if '마진' not in G['text'] and not (J and '마진' in J['text']):   # CSM 표가 아니면 제외(합계가 우연히 맞는 다른 표 방지)
            continue
        for nm, vec, tcol, units in candidates(G) + (candidates(J) if J else []):
            raw = sum(v for _, v in vec)
            if raw == 0 or max(abs(v) for _, v in vec) > 0.9 * abs(raw):          # 한 구간에 몰린 표는 예정표가 아님
                continue
            if re.search(r'합\s*계|총\s*계|행 합', nm) and re.search(r'인식|상각', G['text'] + (J['text'] if J else '')) and '재보험' not in g.ctx.iloc[0][-60:]:
                undisc.setdefault((co, per, rc, doc, tno, nm), (vec, units))     # 할인 전 예정표 후보(아래에서 대응)
            for u in units:
                for basis, tot in (('구간 합', raw * UNITS[u]), ('합계 열', None if tcol is None else tcol * UNITS[u])):
                    if tot is None:
                        continue
                    for when, close in targets.items():
                        err = abs(tot - close)
                        nom = raw * UNITS[u]           # 구간 합: 합계 열(현재가치)과 같거나, 할인 전 금액이라 조금 클 수 있음
                        ok_nom = abs(nom - close) <= max(2.0, abs(close) * 0.01) if basis == '구간 합' else 0.9 * close <= nom <= 1.6 * close
                        if err <= max(1.0, abs(close) * 0.002) and ok_nom:
                            k = (co, when, per)       # 같은 기준일이라도 보고서마다 따로 (나중 보고서가 다시 낸 값과 비교)
                            cands.setdefault(k, []).append(dict(company=co, asof=when, report=per, table=int(tno), row=nm, unit=u, err=round(err, 2),
                                                                basis=basis, total=round(close, 1), nominal=round(raw * UNITS[u], 1),
                                                                vec=[(s, v * UNITS[u]) for s, v in vec], fmt=fmt_of(vec)))
    # 한 보고서에 형식이 다른 예정표가 둘 있는 회사(교보: 5년까지 1년 단위+5~10+10년 초과 / 30년까지 연차별)가 있어
    # 회사마다 기준일을 가장 많이 덮는 형식 하나를 정해 그 형식을 우선 → 형식이 바뀌어 생기는 가짜 변동 방지
    main_fmt = {}
    for co in {k[0] for k in cands}:
        cover = {}
        for (c, when, per), L in cands.items():
            if c == co:
                for f in {x['fmt'] for x in L}:
                    cover.setdefault(f, set()).add(when)
        main_fmt[co] = max(cover, key=lambda f: (len(cover[f]), f == '연차별'))
    for k, L in cands.items():
        pref = [x for x in L if x['fmt'] == main_fmt[k[0]]] or L
        found[k] = min(pref, key=lambda x: x['err'])
    # 할인 전 예정표: 합계가 기말 CSM(현재가치)보다 조금 큰 표 — 보고서마다 아직 못 찾은 기준일에만, 비율 1.0~1.2배 중 가장 가까운 시점에 대응
    exact = {(k[0], k[2]) for k in found}
    used = set()
    for (co, per, rc, doc, tno, nm), (vec, units) in undisc.items():
        if (co, per) in exact or (co, per, rc, doc, tno) in used:   # 한 표는 한 시점에만                         # 그 보고서에 정확히 맞는 표가 있으면 할인 전 대응은 하지 않음
            continue
        raw = sum(v for _, v in vec)
        best = None
        yr = int(per[:4])
        allowed = {per}            # 할인 전 표는 합계가 기말과 정확히 맞지 않아 시점 근거가 약함 → 그 보고서의 당기 시점에만
        for u in units:
            for when, close in {k[1]: v for k, v in asof.items() if k[0] == co and k[1] in allowed and v >= 10}.items():
                r = raw * UNITS[u] / close if close else 0
                if 1.0 <= r <= 1.2 and (co, when, per) not in found and (best is None or r < best[0]):
                    best = (r, when, close, u)
        if best:
            used.add((co, per, rc, doc, tno))
            r, when, close, u = best
            found[(co, when, per)] = dict(company=co, asof=when, report=per, table=int(tno), row=nm, unit=u, err=round(raw * UNITS[u] - close, 1),
                                          basis='할인 전', total=round(close, 1), nominal=round(raw * UNITS[u], 1), vec=[(s_, v * UNITS[u]) for s_, v in vec],
                                          fmt=fmt_of([(s_, v) for s_, v in vec]))
    sched, ev = [], []
    for (co, when, per), f in sorted(found.items()):
        vec, tot = f['vec'], f['nominal']             # 비중은 구간 합 기준(합계 열이 현재가치라 구간 합과 다를 수 있음)
        std = to_std(vec)
        vint = '당시' if per == when else '재공시'          # 그 시점 보고서(예측 당시) / 다음 보고서의 전기 표
        mf = main_fmt.get(co, f['fmt'])
        sched.append(dict(company=co, asof=when, report=per, vintage=vint, fmt=f['fmt'], main_fmt=f['fmt'] == mf, total=f['total'], nominal=tot, basis=f['basis'], **{lb: (None if s is None else round(s, 1)) for lb, s in zip(STD_LB, std)},
                          buckets=' | '.join(f"{lo:g}~{'' if hi == np.inf else f'{hi:g}'}년 {v:,.0f}" for (lo, hi), v in vec),
                          source=f"{f['report']} 보고서 표 {f['table']} · {f['row'] or '합계'} · {f['unit']}", err=f['err']))
        hp = half_point(vec, tot)
        ev.append(dict(company=co, asof=when, report=per, vintage=vint, fmt=f['fmt'], main_fmt=f['fmt'] == mf, total=f['total'], nominal=tot, basis=f['basis'], s1=cum_share(vec, tot, 1), s5=cum_share(vec, tot, 5),
                       s10p=None if cum_share(vec, tot, 10) is None else 1 - cum_share(vec, tot, 10),
                       half=None if hp is None else (99.0 if hp == np.inf else round(hp, 2)),
                       tail_from=next((lo for (lo, hi), _ in vec if hi == np.inf), None),     # 마지막 열린 구간의 시작(예: '5년 초과'면 5) — 절반 시점이 그 뒤일 때 문장에 씀
                       pred1=sum(v for (lo, hi), v in vec if hi <= 1 + 1e-9) if cum_share(vec, tot, 1) is not None else None))
    S, E = pd.DataFrame(sched), pd.DataFrame(ev)
    # 백테스트: 연말 예정 1년 이내 vs 다음 해 실제 상각(연간)
    rel = {(r.company, r.year): -r.release for r in mv.itertuples()}
    E['actual_next'] = [rel.get((r.company, str(int(r.asof[:4]) + 1))) if r.asof.endswith('.12') else None for r in E.itertuples()]
    E['bt_ratio'] = E.actual_next / E.pred1
    summarize(E, mv)
    S.to_csv(os.path.join(D, 'schedule.csv'), index=False, encoding='utf-8-sig')
    E.round(4).to_csv(os.path.join(D, 'schedule_eval.csv'), index=False, encoding='utf-8-sig')
    print(f'예정표 {len(S)}건 (회사 {S.company.nunique() if len(S) else 0}곳) · 백테스트 {E.bt_ratio.notna().sum()}건')
    return S, E


if __name__ == '__main__':
    S, E = main()
    print(E.round(3).to_string())
