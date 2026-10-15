"""
FISIS 원자료(raw/fisis_stats.csv) → 회사·기간별 지표(data/fisis_metrics.csv)
- kics      : 지급여력비율(경과조치 적용 후, %)   kics_pre: 경과조치 적용 전
- p13, p25  : 13·25회차 계약유지율(%)
- prot_init : 개인 보장성 초회보험료(억 원, 그해 1월부터 누적)
- prot_share: 개인 초회보험료 중 보장성 비중
- multiple  : 신계약 CSM 배수 = 신계약 CSM(변동표) ÷ 보장성 초회보험료
              (월납 보장성 상품의 첫 회 보험료가 대부분이라 '월초보험료 대비 CSM'에 가까움. 저축성 신계약 CSM도 분자에 섞이는 근사치)
기간: 12월 → 'YYYY', 6월 → 'YYYY 상반기' (변동표 기간과 같게)
"""
import os
import pandas as pd

B = os.path.join(os.path.dirname(__file__), '..')
D = os.path.join(B, 'data')


def period(bm):
    bm = str(bm)
    return bm[:4] if bm.endswith('12') else (bm[:4] + ' 상반기' if bm.endswith('06') else None)


def main():
    src = os.path.join(B, 'raw', 'fisis_stats.csv')
    if not os.path.exists(src):
        print('FISIS 원자료 없음 — 건너뜀'); return None
    f = pd.read_csv(src, dtype=str)
    f['v'] = pd.to_numeric(f.value, errors='coerce')
    f['year'] = f.base_month.map(period)
    f = f.dropna(subset=['year'])
    pick = lambda ln, cd, col=None: (f[(f.list_no == ln) & (f.account_cd == cd) & ((f.column == col) if col else True)]
                                     .set_index(['co', 'year']).v)
    out = pd.DataFrame({
        'kics': pick('SH021', 'D'), 'kics_pre': pick('SH021', 'A'),
        'p13': pick('SH025', 'A'), 'p25': pick('SH025', 'B'),
        'prot_init': pick('SH166', 'A11', '초회') / 1e8, 'ind_init': pick('SH166', 'A1', '초회') / 1e8,
    })
    out.index = out.index.set_names(['company', 'year'])
    out['kics'] = out.kics.where(out.kics > 0, out.kics_pre)      # 경과조치를 쓰지 않는 회사는 '적용 후' 칸이 0 → 적용 전 값
    out['prot_share'] = out.prot_init / out.ind_init
    mv = pd.read_csv(os.path.join(D, 'movement.csv'), dtype={'year': str}).set_index(['company', 'year'])
    out = out.join(mv.new.rename('new_csm'), how='left')
    out['multiple'] = out.new_csm / out.prot_init
    out.loc[out.prot_init <= 0, 'multiple'] = None
    out = out.reset_index().drop(columns=['ind_init', 'new_csm'])
    out.round(4).to_csv(os.path.join(D, 'fisis_metrics.csv'), index=False, encoding='utf-8-sig')
    print(f"FISIS 지표 {len(out)}행 · 회사 {out.company.nunique()}곳")
    return out


if __name__ == '__main__':
    o = main()
    if o is not None:
        print(o[o.year == '2025'].round(2).sort_values('multiple', ascending=False).to_string())
        print(o[o.company == '신한라이프'].round(2).to_string())
