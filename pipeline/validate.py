"""
2단계: 자동 검증 엔진 — 규칙 6개를 회사·연도마다 실행하고 결과를 checks.csv로 저장
상태: 통과 / 주의(사람 확인 필요) / 불일치 / 자료없음
"""
import pandas as pd, numpy as np, os, time
D = os.path.join(os.path.dirname(__file__), '..', 'data')
t0 = time.perf_counter()
mv = pd.read_csv(f'{D}/movement.csv', dtype={'year': str})
fz = pd.read_csv(f'{D}/fisis.csv', dtype={'year': str})
lr = pd.read_csv(f'{D}/lossratio.csv')
pc = pd.read_csv(f'{D}/pf_check.csv', dtype={'rep': str})
ns = pd.read_csv(f'{D}/note_signals.csv', dtype={'report': str})
ORDER = ['2023', '2024', '2025', '2026 상반기']
TOL = lambda x: max(1.0, abs(x) * 0.001)          # 허용오차: 1억 원 또는 0.1% 중 큰 값
out = []
def add(co, y, rule, status, detail): out.append(dict(company=co, year=y, rule=rule, status=status, detail=detail))

for _, r in mv.iterrows():
    co, y = r.company, r.year
    # R1 항등식: 기초 + 신계약 + 상각 + 이자부리 + 조정 = 기말
    gap = r.open + r.new + r.release + r.interest + r.adjust - r.close
    add(co, y, 'R1 변동표 항등식', '통과' if abs(gap) <= TOL(r.close) else '불일치',
        f'기초+변동−기말 = {gap:,.1f}억')
    # R2 금감원(FISIS) 잔액 대조
    f = fz[(fz.company == co) & (fz.year == y)]
    if len(f) and pd.notna(f.fisis_close.iloc[0]):
        d = r.close - f.fisis_close.iloc[0]
        add(co, y, 'R2 금감원 잔액 대조', '통과' if abs(d) <= TOL(r.close) else '불일치',
            f'DART 기말 {r.close:,.0f}억 vs FISIS {f.fisis_close.iloc[0]:,.0f}억 (차이 {d:,.1f}억)')
    else:
        add(co, y, 'R2 금감원 잔액 대조', '자료없음', 'FISIS 잔액 미게시 또는 미수집')
    # R3 연속성: 당기 기초 = 전기 기말
    i = ORDER.index(y)
    prev = mv[(mv.company == co) & (mv.year == ORDER[i - 1])] if i > 0 else pd.DataFrame()
    if len(prev):
        d = r.open - prev.close.iloc[0]
        add(co, y, 'R3 기초=전기 기말', '통과' if abs(d) <= TOL(r.open) else '불일치',
            f'기초 {r.open:,.0f}억 vs 전기 기말 {prev.close.iloc[0]:,.0f}억 (차이 {d:,.1f}억)')
    else:
        add(co, y, 'R3 기초=전기 기말', '자료없음', '전기 자료 없음')

# R4 포트폴리오 합 = 공시 합계 (예상보험금 표)
pc['year'] = pc.rep.str[:4]   # 보고서 연도 기준(당기·전기 표 포함)
for (co, y), g in pc.groupby(['company', 'year']):
    worst = g.loc[g.diff_pct.abs().idxmax()]
    add(co, y, 'R4 포트폴리오 합계 대조', '통과' if abs(worst.diff_pct) <= 0.5 else '불일치',
        f'표 {g.tn.nunique()}개·구간 {len(g)}개 중 최대 차이 {worst.diff_pct:+.2f}% ({worst.bucket})')

# R5 급변 탐지: (a) 1년차 위험보험료 전년 대비 ±50% 이상 (b) 상쇄율이 업계 분포에서 크게 벗어남
t = lr[lr.cat == '합계'].sort_values(['company', 'year'])
t['B1_chg'] = t.groupby('company').B1.pct_change() * 100
for _, r in t.dropna(subset=['B1_chg']).iterrows():
    st = '주의' if abs(r.B1_chg) >= 50 else '통과'
    add(r.company, str(int(r.year)), 'R5 급변 탐지(위험보험료)', st, f'1년차 위험보험료 전년 대비 {r.B1_chg:+.0f}%')
mv['offset'] = -mv.adjust / mv.new
for y, g in mv[mv.year.isin(ORDER[:3])].groupby('year'):
    med = g.offset.median(); mad = (g.offset - med).abs().median() * 1.4826
    for _, r in g.iterrows():
        z = (r.offset - med) / mad if mad > 0 else 0
        add(r.company, y, 'R5 급변 탐지(상쇄율)', '주의' if abs(z) >= 3 else '통과',
            f'상쇄율 {r.offset:.0%} (업계 중앙값 {med:.0%}, 강건 z {z:+.1f})')

# R6 공시 문구 기반 기준 변경 탐지
for _, r in ns.iterrows():
    add(r.company, r.report[:4], 'R6 기준 변경 문구', '주의', f'[{r.kind}] …{r.text.strip()[:110]}…')

ck = pd.DataFrame(out)
ck.to_csv(f'{D}/checks.csv', index=False, encoding='utf-8-sig')
el = time.perf_counter() - t0
print(f'검증 {len(ck)}건 · {ck.company.nunique()}개사 · {el:.2f}초')
print(ck.pivot_table(index='rule', columns='status', values='company', aggfunc='count', fill_value=0).to_string())
print(ck[ck.status.isin(['불일치'])].to_string())
