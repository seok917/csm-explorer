"""
보험금 예실차비율 자동 추출 (생보·손보 같은 코드)
- 원자료: raw/extra_tables.csv.gz 의 claims 표(collect_extra.py) — 2024 사업보고서부터 감독당국 공시 확대 지침으로 새로 생긴 주석
  예상손해율(A) = 당기 예상보험금 ÷ 당기 실제위험보험료, 실제손해율(B) = (당기 발생보험금 + 발생사고요소조정) ÷ 당기 실제위험보험료,
  보험금 예실차비율(C) = A − B  (양수 = 보험금이 예상보다 적게 나감)
- 표 형식이 회사마다 다름 → 머리글이 가로인 표(예상|실제|예실차 열), 세로인 표(행 이름 + 값 열), 당기·전기 열이 함께 있는 표,
  XBRL 비율(0.919 = 91.9%), 괄호·(-)·△ 음수, 연결·별도 중복 표
- 당기·전기 구분: 표 머리글의 당기/전기 → 바로 앞 제목 표의 '당기'/'전기' → 표 앞 문장의 마지막 당기/전기 표시 → 같은 절 안 표 순서(당기 → 전기)
- 값 선택: 그해 보고서의 당기 값(별도 우선, 같은 값이 여러 표에 있으면 소수 자리가 가장 많은 표) → 없으면 다음 해 보고서의 전기 값
결과: data/ae_auto.csv(최종), data/claims_candidates.csv(읽은 표 전부 — 검증·감사 추적용),
      data/ae_compare.csv(생보: 분석 엑셀 수작업 판독값과 대조)
"""
import os, re, sys
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
import seg

D, RAW = seg.DATA, seg.RAW
SCOPE = re.compile(r'(?<![가-힣])\(?(당|전)\)?\s*기')   # 당기, 전기, (당)기, 당기말, <전기>, 제80(당)기 … ('해당기간'의 '당기'는 제외)


def num(s):
    """'94.6%' '(0.037)' '(-) 5.86%' '-127%' '(-)6.3%p' '0.2%p' → (값, 소수 자리, % 표시 여부) / 숫자가 아니면 None"""
    raw = str(s)
    t = raw.replace(' ', '').replace('%p', '').replace('％', '').replace('%', '').replace('−', '-')
    if not t or t.lower() == 'nan':
        return None
    if t in ('-', '–', '—'):                             # 회계 표기의 '-' = 0
        return 0.0, 9, False
    neg = False
    if t.startswith('(+)'):                              # 메트라이프: '(+) 3.17'
        t = t[3:]
    if t.startswith('(-)'):
        neg, t = True, t[3:]
    if t.startswith('(') and t.endswith(')'):
        neg, t = True, t[1:-1]
    if t[:1] in ('-', '△', '▲'):
        neg, t = True, t[1:]
    t = t.replace(',', '')
    if not re.fullmatch(r'\d+(\.\d+)?', t):
        return None
    return (-float(t) if neg else float(t)), (len(t.split('.')[1]) if '.' in t else 0), ('%' in raw or '％' in raw)


def role(s):
    t = str(s).replace(' ', '')
    if '예실차' in t:
        return 'gap'
    if '예상손해율' in t:
        return 'exp'
    if '실제손해율' in t:
        return 'act'
    return None


BOTH = re.compile(r'당기\s*말?\s*(및|와|과|·|,)\s*전기\s*말?')     # '당기 및 전기 중 …' 같은 두 기간을 함께 가리키는 말은 표시가 아님


def scope_of(s):
    """글에서 마지막으로 나온 당기/전기 표시"""
    m = SCOPE.findall(BOTH.sub(' ', str(s)))
    return {'당': '당기', '전': '전기'}[m[-1]] if m else None


def grid(g):
    p = g.pivot_table(index='row', columns='col', values='value', aggfunc='first')
    p.index = p.index.astype(int); p.columns = p.columns.astype(int)
    p = p.sort_index()[sorted(p.columns)]
    return [[('' if pd.isna(v) else str(v)) for v in row] for row in p.values.tolist()]


def parse(G):
    """표 하나 → [(열·행 머리글의 당기/전기 표시, {exp, act, gap})]"""
    out = []
    # 가로형: 한 행에 예상·실제(·예실차) 머리글이 다른 열에 있음 → 아래 행들이 값
    #   같은 행에 머리글 묶음이 두 번 나오면 당기 | 전기 묶음(위 머리글 행의 '(당)기' '(전)기' 순서로 구분)
    for i, row in enumerate(G):
        blocks, cur = [], {}
        for j, v in enumerate(row):
            r = role(v)
            if r and num(v) is None:
                if r in cur.values():
                    blocks.append(cur); cur = {}
                cur[j] = r
        if cur:
            blocks.append(cur)
        blocks = [b for b in blocks if {'exp', 'act'} <= set(b.values())]
        if not blocks:
            continue
        heads = [scope_of(c) for r0 in G[:i] for c in r0 if scope_of(c)]
        heads = heads[-len(blocks):] if len(heads) >= len(blocks) and len(blocks) > 1 else []
        for row2 in G[i + 1:]:
            lab = scope_of(' '.join(c for j, c in enumerate(row2) if not any(j in b for b in blocks)))
            for k, b in enumerate(blocks):
                vals = {b[j]: num(row2[j]) for j in b if j < len(row2) and num(row2[j]) is not None}
                if 'exp' in vals and 'act' in vals:
                    out.append((heads[k] if heads else lab, vals))
        if out:
            return [fin(s, v) for s, v in out]
    # 세로형: 행 이름(첫 칸) + 값 열(당기·전기 열이 함께 있을 수 있음)
    rows = {}
    for i, row in enumerate(G):
        first = next((v for v in row if v), '')
        r = role(first)
        if r and r not in rows:
            rows[r] = i
    if {'exp', 'act'} <= set(rows):
        top = min(rows.values())
        lab = next(j for j, v in enumerate(G[rows['exp']]) if v)
        for j in range(lab + 1, max(len(r) for r in G)):
            vals = {k: num(G[i][j]) for k, i in rows.items() if j < len(G[i]) and num(G[i][j]) is not None}
            if 'exp' in vals and 'act' in vals:
                head = ' '.join(G[i][j] for i in range(top) if j < len(G[i]))
                out.append((scope_of(head), vals))
        if len(out) > 1 and not any(s for s, _ in out):     # 당기·전기가 아닌 여러 열(생명보험계약 | 비생명보험계약 등) → 첫 열(회사 본체)만
            out = out[:1]
    return [fin(s, v) for s, v in out]


def fin(s, v):
    """XBRL 비율(0.919)은 %로. 예실차 칸만 '-11.3%p'처럼 %로 적은 표(동양생명)도 있어 칸마다 판단"""
    frac = max(abs(v['exp'][0]), abs(v['act'][0])) < 5 and not (v['exp'][2] or v['act'][2])
    def pc(x):
        if x is None:
            return None, None
        return (x[0] * 100, max(0, x[1] - 2)) if frac and not x[2] else (x[0], x[1])
    (e, de), (a, da), (g, dg) = pc(v['exp']), pc(v['act']), pc(v.get('gap'))
    dec = min(d for d in (de, da, dg) if d is not None)
    return dict(col_scope=s, exp=round(e, 4), act=round(a, 4), gap=None if g is None else round(g, 4), dec=dec)


def candidates():
    src = os.path.join(RAW, 'extra_tables.csv.gz')
    if not os.path.exists(src):
        return pd.DataFrame()
    e = pd.read_csv(src, dtype=str)
    e = e[(e.kind == 'claims') & e.period.str.endswith('.12')]
    out = []
    for (co, per, rc, doc), gd in e.groupby(['co', 'period', 'rcept_no', 'doc'], sort=False):
        tabs = []
        for tn, g in sorted(gd.groupby('table_no'), key=lambda x: int(x[0])):
            G = grid(g); ctx = str(g.ctx.iloc[0])
            txt = ' '.join(' '.join(r) for r in G)
            title = scope_of(txt) if ('예실차' in txt and len(txt) < 60) else None      # 'XBRL 제목 표: 보험금 예실차비율에 대한 공시 / 당기'
            if seg.SEG == 'life' and re.search(r'\((장기)?손해보험\)\s*$', ctx):
                continue                                                         # 생보 연결 주석의 손보 자회사 표(한화생명 → 한화손보)
            tabs.append(dict(tn=int(tn), ctx=ctx, rows=parse(G), title=title,
                             cons=doc.endswith('_00761.xml') or '(연결)' in ctx or '연결회사' in txt))
        # 같은 문서 안에서도 연결·별도 절을 나눠 표 순서를 셈
        for cons in (False, True):
            T = [t for t in tabs if t['cons'] == cons]
            vals = [t for t in T if t['rows']]
            for k, t in enumerate(vals):
                prev = next((p for p in T if p['tn'] == t['tn'] - 1), None)
                t['hint'] = (prev or {}).get('title') or scope_of(t['ctx'])
            hints = [t['hint'] for t in vals]
            known = [h for h in hints if h]
            for k, t in enumerate(vals):
                for r in t['rows']:
                    if r['col_scope']:                                   # 표 안 머리글(제80(당)기 | 제79(전)기)
                        sc, how = r['col_scope'], '표 머리글'
                    elif t['hint']:                                      # 바로 앞 제목 표 또는 표 앞 문장의 마지막 당기/전기
                        sc, how = t['hint'], '표 앞 제목·문장'
                    elif len(vals) == 1:
                        sc, how = '당기', '단일 표'
                    elif len(vals) == 2 and len(known) == 1:             # 짝 중 하나만 표시 → 나머지는 반대
                        sc, how = ('전기' if known[0] == '당기' else '당기'), '짝 표의 반대'
                    elif not known and len(vals) % 2 == 0:               # 표시가 전혀 없으면 공시 순서(당기 → 전기, 연결·별도가 섞이면 짝마다)
                        sc, how = ('당기' if k % 2 == 0 else '전기'), '표 순서(당기 → 전기)'
                    else:
                        sc, how = None, '판단 불가'
                    if sc is None:
                        continue
                    out.append(dict(company=co, report=per, rcept_no=rc, doc=doc, table_no=t['tn'], section='연결' if cons else '별도',
                                    scope=sc, how=how, exp=r['exp'], act=r['act'], gap=r['gap'], dec=r['dec']))
    return pd.DataFrame(out)


def tol(dec):
    """반올림 허용오차(%p): 정수로 적은 회사는 ±1.5%p, 소수 첫째 자리는 ±0.15%p … 최소 0.1%p"""
    return max(0.1, 1.5 * 10 ** (-dec))


def pick(c):
    """(회사, 보고서, 당기/전기)마다 한 값: 별도 우선 → 소수 자리가 많은 표 → 앞 표"""
    c = c.assign(_i=(~c.ident_ok).astype(int), _s=(c.section != '별도').astype(int), _d=-c.dec)
    c = c.sort_values(['company', 'report', 'scope', '_i', '_s', '_d', 'doc', 'table_no'])   # 산식(예상−실제=예실차)이 맞는 표 → 별도 → 소수 자리
    best = c.groupby(['company', 'report', 'scope']).head(1).copy()
    sp = c.groupby(['company', 'report', 'scope']).apply(lambda g: float((g.exp.max() - g.exp.min()) + (g.act.max() - g.act.min())), include_groups=False)
    best['n_cand'] = best.set_index(['company', 'report', 'scope']).index.map(c.groupby(['company', 'report', 'scope']).size())
    best['spread'] = best.set_index(['company', 'report', 'scope']).index.map(sp)
    bad = {k: '; '.join(f"{r.section} 표 {r.table_no}: 예실차 {r.gap:+.2f} vs 예상−실제 {r.exp - r.act:+.2f}" for r in g.itertuples())
           for k, g in c[~c.ident_ok].groupby(['company', 'report', 'scope'])}
    best['other_bad'] = [bad.get(k, '') for k in zip(best.company, best.report, best.scope)]
    return best.drop(columns=['_i', '_s', '_d'])


def main():
    c = candidates()
    if c.empty:
        print('예실차 표 없음 — ae_auto.csv를 만들지 않음'); return
    c['ident_ok'] = c.gap.isna() | ((c.gap - (c.exp - c.act)).abs() <= c.dec.map(tol))
    c.to_csv(f'{D}/claims_candidates.csv', index=False, encoding='utf-8-sig')
    b = pick(c)
    b['year'] = b.report.str[:4].astype(int) - (b.scope == '전기').astype(int)
    rows = []
    for (co, y), g in b.groupby(['company', 'year']):
        cur = g[g.scope == '당기']; nxt = g[g.scope == '전기']
        r = (cur if len(cur) else nxt).iloc[0]
        gap = r.gap if pd.notna(r.gap) else round(r.exp - r.act, 4)
        rows.append(dict(company=co, year=str(y), exp=r.exp, act=r.act, ae=gap, dec=int(r.dec),
                         scope='당기' if len(cur) else '다음 해 보고서의 전기', report=r.report, table_no=int(r.table_no), section=r.section,
                         how=r.how, n_cand=int(r.n_cand), spread=round(float(r.spread), 4),
                         # 연속성(R8): 그해 당기 값 vs 다음 해 보고서가 다시 적은 전기 값
                         re_exp=nxt.exp.iloc[0] if len(cur) and len(nxt) else None, re_act=nxt.act.iloc[0] if len(cur) and len(nxt) else None,
                         re_ae=(nxt.gap.iloc[0] if pd.notna(nxt.gap.iloc[0]) else nxt.exp.iloc[0] - nxt.act.iloc[0]) if len(cur) and len(nxt) else None,
                         re_dec=int(nxt.dec.iloc[0]) if len(cur) and len(nxt) else None, gap_given=pd.notna(r.gap),
                         ident_ok=bool(r.ident_ok), other_bad=r.other_bad if isinstance(r.other_bad, str) else ''))
    a = pd.DataFrame(rows).sort_values(['company', 'year'])
    a.to_csv(f'{D}/ae_auto.csv', index=False, encoding='utf-8-sig')
    print(f'예실차 후보 표 {len(c)}개 → {a.company.nunique()}개사 {len(a)}건 (그해 당기 {int((a.scope == "당기").sum())} · 다음 해 전기 {int((a.scope != "당기").sum())})')
    print(c.how.value_counts().to_string())
    # 생보: 분석 엑셀의 수작업 판독값과 대조
    man_p = f'{D}/ae.csv'
    man = pd.read_csv(man_p, dtype={'year': str}) if os.path.exists(man_p) else pd.DataFrame()
    if len(man):
        m = man.merge(a, on=['company', 'year'], how='outer', suffixes=('_m', ''), indicator=True)
        def st(r):
            if r._merge == 'left_only': return '수작업만'
            if r._merge == 'right_only': return '자동만'
            t = max(1.5, tol(r.dec))                    # 수작업 판독값은 정수·소수 첫째 자리로 옮겨 적음
            ok = all(abs(r[k + '_m'] - r[k]) <= t for k in ('exp', 'act', 'ae') if pd.notna(r[k + '_m']))   # 수작업에 비어 있는 칸은 대조하지 않음
            return '일치' if ok else '차이'
        m['status'] = m.apply(st, axis=1)
        m[['company', 'year', 'status', 'exp_m', 'act_m', 'ae_m', 'exp', 'act', 'ae', 'report', 'table_no']].to_csv(
            f'{D}/ae_compare.csv', index=False, encoding='utf-8-sig')
        print('수작업 판독값 대조:', m.status.value_counts().to_dict())


if __name__ == '__main__':
    main()
