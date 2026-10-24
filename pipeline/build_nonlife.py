"""
손보 1단계(생보 build_data.py 자리): 원자료 → 추출·검증이 읽을 표준 파일
- fisis.csv : 금감원 FISIS 책임준비금 통계(SI152)의 원수 보험계약부채 CSM(잔여보장요소 보험계약마진, A113) → 표를 고르는 기준 잔액
              연간(12월)과 반기(6월). 기초 = 직전 연말 값 (반기도 1월 1일부터의 누적이라 기초는 직전 연말)
- 생보에만 있는 수작업 자료(분석 엑셀의 수작업 판독값·요인 분해·예실차)는 손보에 없으므로 빈 표로 둠
  → 화면은 해당 패널을 '공시 자동 추출 대상 아님'으로 표시하고, 검증 규칙도 해당 자료가 있을 때만 실행
실행: CSM_SEGMENT=nonlife python pipeline/build_nonlife.py
"""
import os, sys
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault('CSM_SEGMENT', 'nonlife')
import seg

STUBS = {   # 파일명: 열 (생보와 같은 형식 — 같은 코드가 그대로 읽음)
    'movement_manual.csv': 'company,year,open,new,release,interest,adjust,close,adjust_post,close_post,source',
    'overrides.csv': 'company,year,fields,reason',
    'assump.csv': 'company,year,lapse,risk,expense,other,volume,loss_comp,total',
    'ae.csv': 'company,year,exp,act,ae',
    'lossratio.csv': 'company,year,cat,b1,r1,r1_5,r6_10,r11p,rPV,B1,A_PV',
    'pf_check.csv': 'company,rep,doc,tn,bucket,sum_parts,reported_total,diff_pct',
    'note_signals.csv': 'company,report,where,phrase,text,kind',
    'review_notes.csv': 'company,year,rule,decision,note',
    'fix_log.csv': 'found_by,issue,action,effect',
}


def label(bm):
    bm = str(bm)
    return bm[:4] if bm.endswith('12') else (bm[:4] + ' 상반기' if bm.endswith('06') else None)


def main():
    D = seg.DATA
    for f, cols in STUBS.items():
        p = os.path.join(D, f)
        if not os.path.exists(p):
            open(p, 'w', encoding='utf-8-sig').write(cols + '\n')
    src = os.path.join(seg.RAW, 'fisis_stats.csv')
    if not os.path.exists(src):
        print('FISIS 원자료 없음 — fisis.csv를 만들지 못함'); return
    f = pd.read_csv(src, dtype=str)
    c = f[(f.list_no == 'SI152') & (f.account_cd == 'A113')].copy()
    c['v'] = pd.to_numeric(c.value, errors='coerce') / 1e8            # 원 → 억 원
    c['year'] = c.base_month.map(label)
    c = c.dropna(subset=['year', 'v'])
    close = c.set_index(['co', 'year']).v
    pl = f[(f.list_no == 'SI138') & (f.account_cd == 'A')].copy()            # 장기 보유보험료(억) — 기준 잔액이 0인 회사의 표 단위 확인용
    pl['v'] = pd.to_numeric(pl.value, errors='coerce') / 1e8
    pl['year'] = pl.base_month.map(label)
    prem = pl.dropna(subset=['year', 'v']).groupby(['co', 'year']).v.first()
    fil = pd.read_csv(os.path.join(seg.RAW, 'filings.csv'), dtype=str)
    from periods import label as plabel, prev_year_end, wanted
    annual = {plabel(p) for p in fil.period if p.endswith('.12')}
    rows = []
    # 기간 구성은 생보와 같게: 연간 전부 + 마지막 연간보다 뒤의 반기만(지난 반기는 화면 기간에 없음 — periods.wanted)
    for co, per in sorted({(r.co, plabel(r.period)) for r in fil.itertuples() if r.period[-2:] in ('06', '12') and wanted(plabel(r.period), annual)}):
        v = close.get((co, per)); o = close.get((co, prev_year_end(per)))
        # 0 = 부채인 계약의 CSM이 없음(장기 계약이 모두 자산 쪽이거나 장기보험이 없음) → 추출은 '당기·전기 표 연결' 방식으로
        pm = prem.get((co, per))
        rows.append(dict(company=co, year=per, fisis_open=None if o is None else round(max(o, 0), 1),
                         fisis_close=None if v is None else round(max(v, 0), 1), prem_long=None if pm is None else round(pm * (2 if '상반기' in per else 1), 1)))
    out = pd.DataFrame(rows)
    out.to_csv(os.path.join(D, 'fisis.csv'), index=False, encoding='utf-8-sig')
    print(f'fisis.csv {len(out)}행 · 기준 잔액 있음 {out.fisis_close.notna().sum()}행')


if __name__ == '__main__':
    main()
