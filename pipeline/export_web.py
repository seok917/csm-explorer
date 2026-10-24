"""
3단계: 검색 화면용 데이터(JSON) 만들기 + 회사·연도별 자동 요약 문장 생성(규칙 기반)
숫자는 모두 원표에서 자동 대입 → 사람이 옮겨 적는 단계 없음
"""
import pandas as pd, numpy as np, json, os, time, sys
sys.path.insert(0, os.path.dirname(__file__))
from guard import verify, facts_from
import seg
D = seg.DATA
t0 = time.perf_counter()
mv = pd.read_csv(f'{D}/movement.csv', dtype={'year': str})
asm = pd.read_csv(f'{D}/assump.csv', dtype={'year': str})
ae = pd.read_csv(f'{D}/ae.csv', dtype={'year': str})
lr = pd.read_csv(f'{D}/lossratio.csv')
ck = pd.read_csv(f'{D}/checks.csv', dtype={'year': str}).fillna({'decision': '', 'note': ''})
fx = pd.read_csv(f'{D}/fix_log.csv')
AI = json.load(open(f'{D}/ai_explanations.json', encoding='utf-8')) if os.path.exists(f'{D}/ai_explanations.json') else {}
ns = pd.read_csv(f'{D}/note_signals.csv', dtype={'report': str})
# 상각 예정표(있을 때만) · FISIS 지표(있을 때만)
SCH = pd.read_csv(f'{D}/schedule.csv', dtype={'asof': str, 'report': str}) if os.path.exists(f'{D}/schedule.csv') else None
SEV = pd.read_csv(f'{D}/schedule_eval.csv', dtype={'asof': str, 'report': str}) if os.path.exists(f'{D}/schedule_eval.csv') else None
SEN = pd.read_csv(f'{D}/sensitivity.csv', dtype={'asof': str, 'report': str}) if os.path.exists(f'{D}/sensitivity.csv') else None
FIS = pd.read_csv(f'{D}/fisis_metrics.csv', dtype={'year': str}) if os.path.exists(f'{D}/fisis_metrics.csv') else None
STD_LB = ['1년 이내', '1~2년', '2~5년', '5~10년', '10년 초과']
from periods import sort_labels
YEARS = sort_labels(mv.year)          # 기간 목록은 데이터에서 (새 분기 공시가 추가되면 자동 확장)
ALIAS = {'신한라이프': ['신한', 'shinhan'], 'NH농협생명': ['농협', 'nh'], 'KB라이프': ['kb', '국민', '케이비'], 'iM라이프': ['im', '아이엠', 'dgb'],
         'KDB생명': ['kdb', '산은'], 'ABL생명': ['abl'], 'AIA생명': ['aia'], 'DB생명': ['db', '동부'], 'IBK연금': ['ibk', '기업은행'],
         'BNP카디프생명': ['bnp', '카디프'], '교보라이프플래닛': ['라이프플래닛', '교보라플'], '푸본현대생명': ['푸본', '현대라이프'],
         '처브라이프': ['처브', 'chubb'], '메트라이프': ['met', '메트'], '미래에셋생명': ['미래에셋'], '흥국생명': ['흥국'], '동양생명': ['동양'],
         '삼성생명': ['삼성'], '한화생명': ['한화'], '교보생명': ['교보'], '라이나생명': ['라이나', 'chubb라이나'], '하나생명': ['하나']}
SEGS = {   # 화면 설정(업권별) — 같은 템플릿을 생보(index.html)·손보(nonlife.html)에 씀
    'life': dict(key='life', label='생명보험', page='index.html', default='신한라이프', ex='신한, 삼성, 농협',
                 quick=['신한라이프', '삼성생명', '한화생명', '교보생명', 'NH농협생명', '미래에셋생명']),
    'nonlife': dict(key='nonlife', label='손해보험', page='nonlife.html', default='DB손해보험', ex='DB, 삼성화재, 현대',
                    quick=['DB손해보험', '삼성화재', '메리츠화재', '현대해상', 'KB손해보험', '한화손해보험']),
}
if seg.SEG == 'nonlife':
    ALIAS = {'삼성화재': ['삼성', 'samsung'], 'DB손해보험': ['db', '동부', 'db손보'], '현대해상': ['현대', 'hyundai'], 'KB손해보험': ['kb', '케이비', 'kb손보'],
             '메리츠화재': ['메리츠', 'meritz'], '한화손해보험': ['한화', '한화손보'], '롯데손해보험': ['롯데', '롯데손보'], '흥국화재': ['흥국'],
             'NH농협손해보험': ['농협', 'nh', 'nh손보'], '하나손해보험': ['하나', '하나손보'], 'MG손해보험': ['mg', '엠지', '예별'],
             'AXA손해보험': ['axa', '악사'], 'AIG손해보험': ['aig', '에이아이지'], '신한EZ손해보험': ['신한', 'ez', '신한ez'],
             '카카오페이손해보험': ['카카오', 'kakao'], '캐롯손해보험': ['캐롯', 'carrot']}
FACT = {'lapse': '해지율 가정', 'risk': '위험률 가정', 'expense': '사업비 가정', 'other': '기타 가정', 'volume': '물량차이·투자요소 예실차', 'loss_comp': '손실요소'}

def won(x):          # 억 원 → '7조 5,537억 원'
    s = '−' if x < 0 else ''; x = abs(x)
    jo, eok = int(x // 10000), x % 10000
    return f"{s}{jo}조 {eok:,.0f}억 원" if jo else f"{s}{eok:,.0f}억 원"

mv['offset'] = -mv.adjust / mv.new
mv['post_share'] = mv.close_post / mv.close
mv['adj_post_share'] = mv.adjust_post / mv.adjust
mv['growth'] = (mv.close - mv.open) / mv.open
for k in ('offset', 'post_share', 'adj_post_share', 'growth'):   # 기초·신계약·기말이 0인 해(첫 판매 해, 계약이전 해) → 비율 없음
    mv[k] = mv[k].replace([np.inf, -np.inf], np.nan)
# 상각률: 그해 상각 전 잔액(기말+상각 = 기초+신계약+이자부리+조정+기타) 중 이익으로 풀린 비율 / 대체율: 신계약 CSM ÷ 상각액(1배 초과면 풀린 것보다 많이 새로 쌓음)
mv['release_rate'] = -mv.release / (mv.close - mv.release)      # 상각 전 잔액 = 기말 + 상각 (합병 등 기타 변동까지 포함)
mv['replace'] = mv.new / -mv.release
for k in ('transfer', 'other'):                                  # 손보: 계약이전·합병, CSM 열의 손실부담 행 (생보에는 없음)
    if k not in mv:
        mv[k] = np.nan
gone = mv.close <= 0                                             # 계약을 모두 넘긴 해(엠지손보 2025: 예별손보로 계약이전) → 비율 지표 없음
mv.loc[gone, ['release_rate', 'replace']] = np.nan
mv['rank'] = mv.groupby('year').close.rank(ascending=False, method='min').astype(int)
mv['n_year'] = mv.groupby('year').company.transform('count')
med = mv.groupby('year').offset.median()
med_rr = mv.groupby('year').release_rate.median()
med_rep = mv.groupby('year')['replace'].median()

def summary(r):
    y = r.year; s = []; extra = {'%': [], '개수': []}
    d = r.close - r.open
    s.append(f"{y} {r.company}의 CSM은 기초 {won(r.open)}에서 기말 {won(r.close)}으로 {won(abs(d))} {'늘었' if d >= 0 else '줄었'}습니다"
             + (f"(순증률 {r.growth:+.1%})." if pd.notna(r.growth) else "."))
    s.append(f"신계약으로 {won(r.new)}이 쌓였고, 상각 {won(r.release)}과 이자부리 {won(r.interest)}이 반영됐습니다.")
    if pd.notna(r.transfer):
        s.append(f"계약이전·합병으로 {won(abs(r.transfer))}이 {'넘어왔' if r.transfer > 0 else '넘어갔'}습니다.")
    if r.adjust < 0:
        t = f"조정 추정치 변동 {won(r.adjust)}은 신계약 CSM의 {r.offset:.0%}에 해당합니다"
        if pd.notna(r.adj_post_share) and r.adjust_post < 0 and r.adj_post_share <= 1:
            t += f". 이 중 약 {r.adj_post_share:.0%}가 전환 시 수정소급·공정가치법을 적용하지 않은 계약(주로 IFRS17 전환일 이후 인식 계약)에서 나왔습니다"
        elif pd.notna(r.adj_post_share) and r.adjust_post < 0:      # 다른 계약 쪽이 늘어 일부 상쇄 → 비율이 100%를 넘으므로 금액으로 서술
            t += (f". 전환 시 수정소급·공정가치법을 적용하지 않은 계약(주로 IFRS17 전환일 이후 인식 계약)에서 {won(abs(r.adjust_post))}이 줄었고, "
                  f"나머지 계약에서는 늘어 일부 상쇄됐습니다")
        s.append(t + '.')
    else:
        s.append(f"조정 추정치 변동은 {won(r.adjust)}으로 CSM을 늘리는 방향이었습니다.")
    a = asm[(asm.company == r.company) & (asm.year == y)]
    if len(a):
        a = a.iloc[0]; f = {k: a[k] for k in FACT if pd.notna(a[k])}
        neg = min(f, key=f.get); pos = max(f, key=f.get)
        ig = lambda w: '이' if (ord(w[-1]) - 0xAC00) % 28 else '가'          # 괄호 앞 단어의 받침에 맞춘 조사
        t = f"요인별로는 {FACT[neg]}({won(f[neg])}){ig(FACT[neg])} 가장 크게 줄였고"
        t += f", {FACT[pos]}({won(f[pos])}){ig(FACT[pos])} 가장 크게 늘렸습니다." if f[pos] > 0 else " 늘린 요인은 없었습니다."
        s.append(t)
    if pd.notna(r.release_rate) and pd.notna(r['replace']):
        half = '' if y[:4].isdigit() and len(y) == 4 else '(반기 누적) '
        s.append(f"{half}상각률은 {r.release_rate:.1%}(업계 중앙값 {med_rr[y]:.1%})이고, 신계약 CSM이 상각액의 {r['replace']:.2f}배로 "
                 f"{'풀린 이익보다 많이 새로 쌓았' if r['replace'] > 1 else '새로 쌓은 이익이 풀린 이익보다 적었'}습니다.")
        extra['%'] += [med_rr[y] * 100]
    if y in med.index and not np.isnan(r.offset):
        m = med[y]
        s.append(f"신계약 대비 조정 비율은 같은 기간 업계 중앙값({m:.0%})보다 {'높' if r.offset > m else '낮'}습니다.")
        extra['%'].append(m * 100)
    c = ck[(ck.company == r.company) & ck.year.isin([y] if '상반기' in y else [y, y[:4]])]
    n_ok = (c.status == '통과').sum(); n_bad = (c.status == '불일치').sum(); n_w = (c.status == '주의').sum()
    s.append(f"자동 검증: 규칙 {len(c)}건 중 통과 {n_ok}, 불일치 {n_bad}, 확인 필요 {n_w}.")
    extra['개수'] += [len(c), n_ok, n_bad, n_w]
    return ' '.join(s), extra

def sched_of(co):
    """상각 예정표: 기준일·보고서별 구간 금액과 평가 지표"""
    if SCH is None:
        return []
    out = []
    for _, r in SCH[SCH.company == co].iterrows():
        e = SEV[(SEV.company == co) & (SEV['asof'] == r['asof']) & (SEV.report == r['report'])].iloc[0]   # .asof는 pandas 메서드라 [] 사용
        nn = lambda v: None if pd.isna(v) else float(v)
        out.append(dict(asof=r['asof'], report=r['report'], vintage=r['vintage'], fmt=r['fmt'], main=bool(r['main_fmt']), total=r['total'], nominal=r['nominal'],
                        b={lb: nn(r[lb]) for lb in STD_LB},
                        s1=nn(e.s1), s5=nn(e.s5), s10p=nn(e.s10p), half=nn(e.half), pred1=nn(e.pred1), tail_from=nn(e.get('tail_from')),
                        actual_next=nn(e.actual_next), bt=nn(e.bt_ratio), source=r['source']))
    return out


SENS_K = ['mort', 'morb', 'indem', 'lapse_up', 'lapse_dn', 'exp']


def sens_of(co):
    """가정 민감도: 기준일별(그 시점 보고서 값 우선) 충격별 CSM 변화율·금액·충격 크기"""
    if SEN is None:
        return []
    f = SEN[SEN.company == co].sort_values(['asof', 'vintage'])          # '당시'가 '재공시'보다 앞
    out = []
    for a, g in f.groupby('asof'):
        r = g.iloc[0]
        nn = lambda k: None if k not in r or pd.isna(r[k]) else float(r[k])
        comb = r['combined'] if 'combined' in r and isinstance(r['combined'], str) else ''
        out.append(dict(asof=a, report=r['report'], base=float(r['base']), close=float(r['close']), source=r['source'], combined=comb,
                        p={k: nn('p_' + k) for k in SENS_K}, d={k: nn('d_' + k) for k in SENS_K}, sz={k: nn('sz_' + k) for k in SENS_K}))
    return out


def fisis_of(co):
    if FIS is None:
        return {}
    f = FIS[FIS.company == co]
    return {r.year: {k: (None if pd.isna(getattr(r, k)) else float(getattr(r, k))) for k in FIS.columns if k not in ('company', 'year')}
            for r in f.itertuples()}


data = {'years': YEARS, 'companies': [], 'meta': {}}
for co, g in mv.groupby('company'):
    rows = {}
    for _, r in g.iterrows():
        a = asm[(asm.company == co) & (asm.year == r.year)]
        a0 = a.iloc[0] if len(a) else None
        split = None
        if a0 is not None:                       # 조정 = 가정 변경(해지율·위험률·사업비·기타) + 경험 차이(물량차이·투자요소 예실차) + 손실요소
            assume = sum(a0[k] for k in ('lapse', 'risk', 'expense', 'other') if pd.notna(a0[k]))
            split = dict(assume=round(float(assume), 1), experience=None if pd.isna(a0['volume']) else round(float(a0['volume']), 1),
                         loss=None if pd.isna(a0['loss_comp']) else round(float(a0['loss_comp']), 1))
        rows[r.year] = dict(split=split, open=r.open, new=r.new, release=r.release, interest=r.interest, adjust=r.adjust, close=r.close,
                            adjust_post=None if pd.isna(r.adjust_post) else r.adjust_post, close_post=None if pd.isna(r.close_post) else r.close_post,
                            offset=None if pd.isna(r.offset) else round(r.offset, 4), post_share=None if pd.isna(r.post_share) else round(r.post_share, 4),
                            growth=None if pd.isna(r.growth) else round(r.growth, 4), rank=int(r['rank']), n=int(r.n_year), source=r.source,
                            assump={FACT[k]: float(a.iloc[0][k]) for k in FACT if len(a) and pd.notna(a.iloc[0][k])} or None,
                            adj_post_share=None if pd.isna(r.adj_post_share) else round(r.adj_post_share, 4),
                            transfer=None if pd.isna(r.transfer) else r.transfer, other=None if pd.isna(r.other) else r.other,
                            release_rate=None if pd.isna(r.release_rate) else round(r.release_rate, 4),
                            replace=None if pd.isna(r['replace']) else round(r['replace'], 3))
        txt, extra = summary(r)
        rows[r.year]['summary'] = txt
        g = verify(txt, facts_from(rows[r.year], extra=extra))      # 규칙 기반 요약도 숫자 대조
        rows[r.year]['summary_guard'] = g
        if not g['ok']: print('[가드레일 경고]', co, r.year, g['mismatches'])
        a = AI.get(f'{co}|{r.year}')
        if a and a['status'] in ('ok', 'fixed'):                    # 숫자 대조를 통과한 AI 설명만 화면에 표시
            now = verify(a['text'], facts_from(rows[r.year]))          # 지금 원표와 다시 대조 — 원표가 고쳐진 뒤 AI가 다시 쓰기 전의 옛 숫자 설명은 숨김
            if now['ok']:
                rows[r.year]['ai'] = dict(text=a['text'], guard=a['guard'], status=a['status'], model=a['model'])
            else:
                print('[AI 설명 보류: 원표와 숫자 불일치 → 다음 AI 실행 때 다시 생성]', co, r.year, now['mismatches'][:3])
    e = ae[ae.company == co].sort_values('year')
    l = lr[(lr.company == co) & lr.cat.isin(['합계', '건강'])].sort_values('year')
    c = ck[ck.company == co]
    n = ns[ns.company == co]
    data['companies'].append(dict(
        name=co, alias=ALIAS.get(co, []), years=rows,
        ae=[dict(year=x.year, exp=x.exp, act=x.act, ae=x.ae) for x in e.itertuples()],
        lossratio=[dict(year=int(x.year), cat=x.cat, b1=x.b1, r1=None if pd.isna(x.r1) else round(x.r1, 2),
                        rPV=None if pd.isna(x.rPV) else round(x.rPV, 2)) for x in l.itertuples()],
        checks=[dict(year=x.year, rule=x.rule, status=x.status, detail=x.detail, decision=x.decision, note=x.note) for x in c.itertuples()],
        notes=[dict(report=x.report, kind=x.kind, text=x.text.strip()) for x in n.itertuples()],
        schedule=sched_of(co), fisis=fisis_of(co), sens=sens_of(co)))
data['companies'].sort(key=lambda d: -(d['years'].get('2025') or d['years'].get('2024'))['close'])
rules = sorted(set(ck.rule))
data['fixes'] = fx.to_dict('records')
# 원문 표 자동 추출 결과 (자동 vs 수작업 판독값)
if os.path.exists(f'{D}/extract_report.csv'):
    ex = pd.read_csv(f'{D}/extract_report.csv', dtype={'year': str})
    ov = pd.read_csv(f'{D}/overrides.csv', dtype={'year': str})
    man = pd.read_csv(f'{D}/movement_manual.csv', dtype={'year': str}).set_index(['company', 'year'])
    fin = mv.set_index(['company', 'year'])
    KO = dict(open='기초', close='기말', new='신계약', release='상각', interest='이자부리', adjust='조정')
    cases = []
    for r in ex[ex.status.isin(['차이', '자동추출 실패'])].itertuples():
        o = ov[(ov.company == r.company) & (ov.year == r.year)]
        k = (r.company, r.year)
        if len(o):
            verdict, why = '보정 파일', o.reason.iloc[0]
        else:
            verdict, why = '자동값 채택', '자동값이 금감원 잔액과 전기 기말에 모두 맞아 수작업 판독값을 수정'
        diff = [f'{KO[f]} {fin.loc[k, f]:,.1f} (수작업 {man.loc[k, f]:,.1f})' for f in ('open', 'close')
                if r.status == '차이' and not len(o) and abs(fin.loc[k, f] - man.loc[k, f]) > 0.5]
        cases.append(dict(company=r.company, year=r.year, verdict=verdict, why=why, diff=' · '.join(diff)))
    data['extract'] = dict(n=int(len(ex)), auto=int((ex.status != '자동추출 실패').sum()),
                           match=int(ex.status.isin(['일치', '일치(추가값)']).sum()), extra=int((ex.status == '일치(추가값)').sum()),
                           corrected=sum(c['verdict'] == '자동값 채택' for c in cases), overridden=int(len(ov)),
                           cases=sorted(cases, key=lambda c: c['verdict'] != '자동값 채택'))
if SEN is not None:                                  # 충격별 업계 중앙값(기준일별, 그 시점 보고서 값)
    L = SEN[SEN.vintage == '당시']
    data['sens_median'] = {a: {k: (None if g['p_' + k].isna().all() else round(float(g['p_' + k].median()), 4)) for k in SENS_K if 'p_' + k in g}
                           | {'n': int(len(g))} for a, g in L.groupby('asof')}
if os.path.exists(f'{D}/schedule_summary.json'):
    data['schedule_eval'] = json.load(open(f'{D}/schedule_summary.json', encoding='utf-8'))
data['ai_stats'] = {k: sum(1 for v in AI.values() if v['status'] == k) for k in ('ok', 'fixed', 'rejected')}
data['meta'] = dict(n_company=len(data['companies']), n_checks=int(len(ck)), n_reports=int(len(pd.read_csv(f'{D}/../raw/filings.csv'))),
                    check_counts=ck.status.value_counts().to_dict(), reviewed=int((ck.status.isin(['불일치','주의']) & (ck.decision != '')).sum()), flagged=int(ck.status.isin(['불일치','주의']).sum()), offset_median={k: round(v, 4) for k, v in med.items()},
                    release_rate_median={k: round(v, 4) for k, v in med_rr.items()}, replace_median={k: round(v, 3) for k, v in med_rep.items()},
                    built=pd.Timestamp.now(tz='Asia/Seoul').strftime('%Y-%m-%d'),
                    seg=SEGS[seg.SEG], segs=[dict(key=v['key'], label=v['label'], page=v['page']) for v in SEGS.values()],
                    rules=rules, n_rules=len({r.split(' ')[0] for r in rules}),
                    has=dict(assump=bool(len(asm)), ae=bool(len(ae)), lossratio=bool(len(lr)), notes=bool(len(ns))))
def clean(o):   # NaN·numpy 값 → JSON 표준값
    if isinstance(o, dict): return {k: clean(v) for k, v in o.items()}
    if isinstance(o, list): return [clean(v) for v in o]
    if isinstance(o, (np.integer,)): return int(o)
    if isinstance(o, (float, np.floating)): return None if (np.isnan(o) or np.isinf(o)) else round(float(o), 4)
    return o
json.dump(clean(data), open(f'{D}/web.json', 'w'), ensure_ascii=False, separators=(',', ':'), allow_nan=False)
print(f"회사 {len(data['companies'])} · 요약 문장 {sum(len(c['years']) for c in data['companies'])}개 · {time.perf_counter()-t0:.2f}초 · {os.path.getsize(f'{D}/web.json')/1024:.0f}KB")
_d = SEGS[seg.SEG]['default']
print(data['companies'][[c['name'] for c in data['companies']].index(_d)]['years']['2025']['summary'])
