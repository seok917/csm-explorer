"""
1단계: 분석 결과(엑셀·피클)를 앱이 읽을 표준 데이터(CSV)로 정리
- movement.csv : 회사·연도별 CSM 변동표 (기초·신계약·상각·이자부리·조정·기말, 전환이후 계약)
- fisis.csv    : 금감원 FISIS CSM 잔액 (검증용)
- assump.csv   : 조정 추정치 변동의 요인별 분해
- ae.csv       : 보험금 예실차비율
- lossratio.csv: 포트폴리오별 미래 예상손해율(위험보험료 대비 예상보험금)
- pf_check.csv : 포트폴리오 합 vs 공시 합계 대조
"""
import pandas as pd, numpy as np, openpyxl, pickle, sys, os
# 원자료 위치 (본인 PC 경로에 맞게 수정)
XL = os.environ.get('CSM_XLSX', '/mnt/user-data/outputs/신한라이프_CSM_지표.xlsx')   # 분석 엑셀
X22 = os.environ.get('CSM_WORK', '/home/claude/x22/')                                  # 표 추출 중간 결과
OUT = os.path.join(os.path.dirname(__file__), '..', 'data')

def sheet_table(ws, header_row, stop_blank=True):
    """header_row부터 첫 빈 행 전까지를 표로 읽음"""
    rows = list(ws.iter_rows(min_row=header_row, values_only=True))
    hdr = [h for h in rows[0] if h is not None]
    out = []
    for r in rows[1:]:
        if r[0] is None or (isinstance(r[0], str) and r[0].startswith('주')):
            if stop_blank: break
            continue
        out.append(list(r[:len(hdr)]))
    return pd.DataFrame(out, columns=hdr)

SHORT = {'신한라이프생명보험': '신한라이프', '교보생명보험': '교보생명', '농협생명보험': 'NH농협생명', '케이비라이프생명보험': 'KB라이프',
         '메트라이프생명보험': '메트라이프', '동양생명보험': '동양생명', '흥국생명보험': '흥국생명', 'DB생명보험': 'DB생명',
         '에이아이에이생명보험': 'AIA생명', '에이비엘생명보험': 'ABL생명', '케이디비생명보험': 'KDB생명', '아이엠라이프생명보험': 'iM라이프',
         '하나생명보험': '하나생명', '아이비케이연금보험': 'IBK연금', '라이나생명보험': '라이나생명', '푸본현대생명보험': '푸본현대생명',
         '처브라이프생명보험': '처브라이프', '비엔피파리바카디프생명보험': 'BNP카디프생명', '교보라이프플래닛생명보험': '교보라이프플래닛',
         '삼성생명': '삼성생명', '한화생명': '한화생명', '미래에셋생명': '미래에셋생명'}

def main():
    wb = openpyxl.load_workbook(XL, data_only=True)

    # 1) CSM 변동표
    mv = sheet_table(wb['경쟁사_비교'], 4)
    mv = mv.rename(columns={'회사': 'company', '연도': 'year', '기초 CSM': 'open', '신계약 CSM': 'new', 'CSM 상각': 'release',
                            '이자부리': 'interest', '조정 추정치 변동': 'adjust', '기말 CSM': 'close',
                            '추정치변동_전환이후': 'adjust_post', '기말CSM_전환이후': 'close_post', '출처 보고서': 'source'})
    mv = mv[['company', 'year', 'open', 'new', 'release', 'interest', 'adjust', 'close', 'adjust_post', 'close_post', 'source']]
    mv['year'] = mv['year'].astype(str)
    mv.to_csv(f'{OUT}/movement.csv', index=False, encoding='utf-8-sig')

    # 2) FISIS 잔액
    fz = sheet_table(wb['검증_FISIS대조'], 4)
    fz = fz.rename(columns={'회사': 'company', '연도': 'year', 'FISIS 직전 연말 CSM': 'fisis_open', 'FISIS 기말 CSM': 'fisis_close'})
    fz[['company', 'year', 'fisis_open', 'fisis_close']].assign(year=lambda d: d.year.astype(str)).to_csv(f'{OUT}/fisis.csv', index=False, encoding='utf-8-sig')

    # 3) 가정변경 분해
    asm = sheet_table(wb['경쟁사_가정변경'], 4)
    asm = asm.rename(columns={'회사': 'company', '연도': 'year', '해지율': 'lapse', '위험률': 'risk', '사업비율': 'expense', '기타가정': 'other',
                              '물량차이·투자요소 예실차': 'volume', '손실요소': 'loss_comp', '합계': 'total'})
    asm = asm[['company', 'year', 'lapse', 'risk', 'expense', 'other', 'volume', 'loss_comp', 'total']]
    asm = asm[pd.to_numeric(asm['lapse'], errors='coerce').notna()]
    asm['year'] = asm['year'].astype(str)
    asm.to_csv(f'{OUT}/assump.csv', index=False, encoding='utf-8-sig')

    # 4) 예실차 (피클: 회사 정식명 → 약칭 변환)
    AE = pickle.load(open(X22 + 'ae22.pkl', 'rb'))
    ae = pd.DataFrame([dict(company=SHORT.get(c, c), year=y, **{k: v.get(k) for k in ('exp', 'act', 'ae')}) for (c, y), v in AE.items()])
    ae.to_csv(f'{OUT}/ae.csv', index=False, encoding='utf-8-sig')

    # 5) 포트폴리오별 예상손해율
    R = pd.read_pickle(X22 + 'pfab_R.pkl')
    R['company'] = R.co.map(SHORT)
    R[['company', 'y', 'cat', 'b1', 'r1', 'r1_5', 'r6_10', 'r11p', 'rPV', 'B1', 'A_PV']].rename(columns={'y': 'year'}) \
        .to_csv(f'{OUT}/lossratio.csv', index=False, encoding='utf-8-sig')

    # 6) 포트폴리오 합 vs 공시 합계 (표 단위)
    L = pd.read_pickle(X22 + 'pfab_long.pkl'); L = L[(L.fmt == 'row') & L.val.notna()]
    # 한화 2024 장기손해보험(비생명) 표는 범주 체계가 달라 대조 대상에서 제외, 메트 2024 표203은 유지비 표
    L = L[~((L.co == '한화생명') & L.tn.isin([595, 597])) & ~((L.co == '메트라이프생명보험') & (L.tn == 203))]
    def cat2(p):
        s = str(p).replace(' ', '')
        for k, c in (('건강', '건강'), ('사망', '사망'), ('연금', '연금저축'), ('저축', '연금저축')):
            if k in s: return c
        return '합계' if s.startswith('합계') else None
    L['cat'] = L.pf.map(cat2)
    key = ['co', 'rep', 'doc', 'tn', 'bucket']
    part = L[L.cat.isin(['사망', '건강', '연금저축']) & (L.met == 'A')].groupby(key).val.sum()
    tot = L[(L.cat == '합계') & (L.met == 'A')].groupby(key).val.sum()
    chk = pd.concat([part.rename('sum_parts'), tot.rename('reported_total')], axis=1).dropna().reset_index()
    chk['company'] = chk.co.map(SHORT)
    chk['diff_pct'] = (chk.sum_parts / chk.reported_total - 1) * 100
    chk[['company', 'rep', 'doc', 'tn', 'bucket', 'sum_parts', 'reported_total', 'diff_pct']].to_csv(f'{OUT}/pf_check.csv', index=False, encoding='utf-8-sig')

    print('movement', len(mv), '| fisis', len(fz), '| assump', len(asm), '| ae', len(ae), '| lossratio', len(R), '| pf_check', len(chk))


if __name__ == '__main__':
    main()
