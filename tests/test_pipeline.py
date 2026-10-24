"""
자동 테스트 — `python tests/test_pipeline.py` 또는 `pytest tests/`로 실행
1) 숫자 가드레일이 맞는 문장은 통과시키고 틀린 숫자는 잡아내는지
2) 데이터·검증 결과의 무결성
3) 과거에 발견한 오류가 다시 생기지 않는지(회귀 테스트)
4) 원문 표 자동 추출: 추출률, 수작업 판독값과의 차이 해명, 보정 사유, 스냅숏 재현 / 분기 자동 업데이트: 기간 표기, DART 원문 파싱, 새 공시 → 새 기간
5) AI 설명 모듈의 재시도·반려 흐름, 표현 가드레일 (실제 API 대신 가짜 응답으로)
"""
import json, os, sys, shutil, tempfile, csv
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
sys.path.insert(0, os.path.join(ROOT, 'pipeline'))
from guard import extract, verify, facts_from

D = os.path.join(ROOT, 'data')
WEB = json.load(open(os.path.join(D, 'web.json'), encoding='utf-8'))
CO = {c['name']: c for c in WEB['companies']}
SH25 = CO['신한라이프']['years']['2025']


def checks():
    return list(csv.DictReader(open(os.path.join(D, 'checks.csv'), encoding='utf-8-sig')))


# ---------- 1. 숫자 가드레일 ----------
def test_extract_units():
    n = {x['raw']: x['value'] for x in extract('기말 7조 5,537억 원, 신계약 1조 6,460억, 상각 −7,352억, 비율 51%, 예실차 −6.27%p, 7.6조')}
    assert n['7조 5,537억'] == 75537 and n['1조 6,460억'] == 16460 and n['−7,352억'] == -7352
    assert n['51%'] == 51 and n['−6.27%p'] == -6.27 and n['7.6조'] == 76000


def test_year_is_not_checked():
    assert verify('2025년 IFRS17 기준', {'억': [], '%': []})['checked'] == 0


def test_correct_summary_passes():
    g = SH25['summary_guard']
    assert g['ok'] and g['checked'] >= 10, g


def test_wrong_number_is_caught():
    bad = SH25['summary'].replace('7,352억', '7,532억')          # 숫자 자리 바뀜(흔한 전사 오류)
    g = verify(bad, facts_from(SH25, extra={'%': [WEB['meta']['offset_median']['2025'] * 100], '개수': [6, 0]}))
    assert not g['ok'] and any('7,532' in m for m in g['mismatches']), g


def test_wrong_sign_is_caught():
    g = verify('조정 추정치 변동 +8,470억 원', facts_from(SH25))
    assert not g['ok']


def test_rounded_jo_is_tolerated():
    assert verify('기말 CSM은 약 7.6조', facts_from(SH25))['ok']


# ---------- 2. 데이터·검증 무결성 ----------
def test_company_count():
    assert len(WEB['companies']) == 22


def test_all_summaries_pass_guard():
    bad = [(c['name'], y) for c in WEB['companies'] for y, r in c['years'].items() if not r['summary_guard']['ok']]
    assert not bad, bad


def test_every_flag_has_review_note():
    miss = [(r['company'], r['year'], r['rule']) for r in checks() if r['status'] in ('불일치', '주의') and not r['decision']]
    assert not miss, miss


def test_shinhan_2025_all_pass():
    st = {r['rule']: r['status'] for r in checks() if r['company'] == '신한라이프' and r['year'] == '2025'}
    assert st and all(v == '통과' for v in st.values()), st


# ---------- 3. 회귀 테스트(과거에 발견한 오류) ----------
def test_regression_mirae_unit():           # 원 vs 백만원 단위 불일치
    r = [x for x in checks() if x['company'] == '미래에셋생명' and x['year'] == '2024' and x['rule'].startswith('R5 급변 탐지(위험')]
    assert r and r[0]['status'] == '통과', r


def test_regression_hanwha_nonlife_table():  # 비생명 표 혼입
    r = [x for x in checks() if x['company'] == '한화생명' and x['rule'].startswith('R4')]
    assert r and all(x['status'] == '통과' for x in r), r


def test_regression_residual_rows_not_counted():  # 잔차로 만든 행이 항등식 '통과'로 세어지면 안 됨
    import pandas as pd
    mv = pd.read_csv(os.path.join(D, 'movement.csv'), dtype={'year': str})
    res = {(r.company, r.year) for r in mv.itertuples() if '잔차' in str(r.source)}
    st = {(x['company'], x['year']): x['status'] for x in checks() if x['rule'].startswith('R1')}
    assert res and all(st[k] == '자료없음' for k in res), res


# ---------- 4. 원문 표 → 변동표 자동 추출 ----------
def _csv(name):
    import pandas as pd
    return pd.read_csv(os.path.join(D, name), dtype={'year': str})


def test_extraction_coverage():          # 72건 중 자동 추출 성공·수작업 판독값과 일치 건수
    rep = _csv('extract_report.csv')
    assert (rep.status != '자동추출 실패').sum() >= 71
    assert rep.status.isin(['일치', '일치(추가값)']).sum() >= 66, rep.status.value_counts().to_dict()


def test_extraction_differences_are_resolved():
    """자동 ≠ 수작업인 건은 (a) 보정 파일에 사유가 있거나 (b) 자동값이 금감원 잔액·전기 기말과 맞아야 함"""
    rep, ov, mv, fz = _csv('extract_report.csv'), _csv('overrides.csv'), _csv('movement.csv'), _csv('fisis.csv')
    ovk = set(zip(ov.company, ov.year))
    close = {(r.company, r.year): r.close for r in mv.itertuples()}
    fis = {(r.company, r.year): r.fisis_close for r in fz.itertuples()}
    for r in rep[rep.status.isin(['차이', '자동추출 실패'])].itertuples():
        k = (r.company, r.year)
        if k in ovk:
            continue
        m = mv[(mv.company == r.company) & (mv.year == r.year)].iloc[0]
        prev = close.get((r.company, str(int(r.year[:4]) - 1)))
        assert abs(m.close - fis[k]) <= 1 and (prev is None or abs(m.open - prev) <= 1), (k, m.open, m.close, prev, fis[k])


def test_overrides_have_reason_and_are_applied():
    ov, mv = _csv('overrides.csv'), _csv('movement.csv')
    for r in ov.itertuples():
        assert len(str(r.reason)) >= 20, r
        src = mv[(mv.company == r.company) & (mv.year == r.year)].source.iloc[0]
        assert '수작업' in src, src


def test_regression_net_row_preferred():           # 자산 쪽 계약의 CSM까지 포함한 '순부채' 행을 써야 함
    mv = _csv('movement.csv')
    assert abs(mv[(mv.company == '삼성생명') & (mv.year == '2023')].open.iloc[0] - 107486.9) < 0.5


def test_regression_participating_tables_included():   # 유배당 표 누락(수작업 오류) 재발 방지
    mv = _csv('movement.csv').set_index(['company', 'year'])
    for co, y, nxt in [('KB라이프', '2025', '2026 상반기'), ('iM라이프', '2025', None), ('처브라이프', '2025', None)]:
        assert abs(mv.loc[(co, y)].open - mv.loc[(co, str(int(y) - 1))].close) < 0.5, co
        if nxt:
            assert abs(mv.loc[(co, nxt)].open - mv.loc[(co, y)].close) < 0.5, co


def test_snapshot_reproduces_shinhan_2025():      # 저장소 스냅숏만으로 신한라이프 2025 변동표 재현
    import extract_movement as E, pandas as pd
    snap = pd.read_csv(E.SNAP, dtype=str).fillna('')
    C = E.candidates(snap[(snap.co == '신한라이프') & (snap.period == '2025.12')])
    res = E.choose(C, 72241.1 * 100, 75537.3 * 100)
    for k in ('open', 'new', 'release', 'interest', 'adjust', 'close'):
        assert abs(res[k] / 100 - SH25[k]) < 0.5, (k, res[k] / 100, SH25[k])


# ---------- 4b. 분기 자동 업데이트(수집기·새 기간) ----------
def test_period_labels():
    from periods import label, prev_year_end, sort_labels
    assert label('2026.09') == '2026 3분기' and label('2026.12') == '2026' and label('2026.06') == '2026 상반기'
    assert prev_year_end('2026 3분기') == '2025' and prev_year_end('2025') == '2024'
    assert sort_labels(['2026', '2026 3분기', '2025', '2026 상반기']) == ['2025', '2026 상반기', '2026 3분기', '2026']


def test_collector_parses_dart_tables():   # DART 원문(XML) → 표 셀: 대문자 태그·TE/TU 셀·공백 정리·중첩 표·CSM 표만 남기기
    import io, zipfile, collect_dart as C
    xml = ('<?xml version="1.0" encoding="utf-8"?><DOCUMENT><BODY><P>보험계약마진의 변동</P>'
           '<TABLE><TR><TH>구 분</TH><TH>보험계약마진</TH></TR><TR><TD>기초</TD><TE>1,000</TE></TR>'
           '<TR><TD>신계약</TD><TU> (50) </TU></TR><TR><TD>기말</TD><TE>950</TE></TR></TABLE>'
           '<TABLE><TR><TD>단위<TABLE><TR><TD>중첩</TD></TR></TABLE></TD></TR></TABLE></BODY></DOCUMENT>')
    b = io.BytesIO(); z = zipfile.ZipFile(b, 'w'); z.writestr('20261114000001.xml', xml.encode()); z.close()
    cells = C.tables_of(b.getvalue())
    assert ('20261114000001.xml', 1, 3, 2, '(50)') in cells and ('20261114000001.xml', 2, 2, 1, '중첩') in cells
    df = C.csm_only(cells, '신한라이프', dict(period='2026.09', rcept_no='20261114000001'))
    assert set(df.table_no) == {'1'} and len(df) == 8


def test_new_filing_becomes_new_period():   # 새 연간 공시가 스냅숏에 들어오면 기간이 자동으로 추가되고 기초=전년 연말로 표를 고름 (1·3분기는 올리지 않음)
    import pandas as pd, extract_movement as E
    tmp = tempfile.mkdtemp()
    try:
        for f in os.listdir(D):
            if f.endswith('.csv'):
                shutil.copy(os.path.join(D, f), tmp)
        s = pd.read_csv(E.SNAP, dtype=str).fillna('')
        fake = s[(s.co == '신한라이프') & (s.period == '2026.06')].assign(period='2026.12', rcept_no='20270331999999')
        q3 = s[(s.co == '신한라이프') & (s.period == '2026.06')].assign(period='2026.09', rcept_no='20261114999998')
        fake = pd.concat([fake, q3])
        snap = os.path.join(tmp, 'snap.csv.gz'); pd.concat([s, fake]).to_csv(snap, index=False, compression='gzip')
        old = (E.D, E.SNAP, E.DUMP)
        E.D, E.SNAP, E.DUMP = tmp, snap, os.path.join(tmp, 'no-dump')
        try:
            E.main()
        finally:
            E.D, E.SNAP, E.DUMP = old
        mv = pd.read_csv(os.path.join(tmp, 'movement.csv'), dtype={'year': str}).set_index(['company', 'year'])
        assert abs(mv.loc[('신한라이프', '2026')].open - mv.loc[('신한라이프', '2025')].close) < 0.5
        assert ('신한라이프', '2026 3분기') not in mv.index                 # 분기는 화면 기간이 아님
        assert ('신한라이프', '2026') not in set(pd.read_csv(os.path.join(D, 'movement.csv'), dtype={'year': str})[['company', 'year']].itertuples(index=False, name=None))
    finally:
        shutil.rmtree(tmp)


# ---------- 5. AI 설명 모듈 (가짜 응답) ----------
def test_ai_retry_then_fixed():
    import ai_explain
    tmp = tempfile.mkdtemp()
    try:
        web = {'companies': [dict(CO['신한라이프'], years={'2025': SH25})]}
        json.dump(web, open(os.path.join(tmp, 'web.json'), 'w', encoding='utf-8'), ensure_ascii=False)
        answers = iter(['조정 추정치 변동은 −9,000억 원입니다.',                 # 1차: 틀린 숫자
                        '조정 추정치 변동 −8,470억 원은 신계약 CSM의 51%입니다.'])  # 2차: 고친 답
        ai_explain.D, ai_explain.call = tmp, (lambda msgs, key: next(answers))
        os.environ['ANTHROPIC_API_KEY'] = 'test-key'
        ai_explain.main()
        out = json.load(open(os.path.join(tmp, 'ai_explanations.json'), encoding='utf-8'))['신한라이프|2025']
        assert out['status'] == 'fixed' and out['attempts'] == 2 and out['guard']['ok'], out
    finally:
        os.environ.pop('ANTHROPIC_API_KEY', None); shutil.rmtree(tmp)


def test_ai_rejected_when_still_wrong():
    import importlib, ai_explain
    importlib.reload(ai_explain)
    tmp = tempfile.mkdtemp()
    try:
        json.dump({'companies': [dict(CO['신한라이프'], years={'2025': SH25})]},
                  open(os.path.join(tmp, 'web.json'), 'w', encoding='utf-8'), ensure_ascii=False)
        ai_explain.D, ai_explain.call = tmp, (lambda msgs, key: '신계약 CSM은 2조 원입니다.')
        os.environ['ANTHROPIC_API_KEY'] = 'test-key'
        ai_explain.main()
        out = json.load(open(os.path.join(tmp, 'ai_explanations.json'), encoding='utf-8'))['신한라이프|2025']
        assert out['status'] == 'rejected', out
    finally:
        os.environ.pop('ANTHROPIC_API_KEY', None); shutil.rmtree(tmp)


def test_ai_style_guard_retry():           # 숫자는 맞아도 원인 추정·평가어('손실', '때문')는 다시 쓰게 함
    import importlib, ai_explain
    importlib.reload(ai_explain)
    tmp = tempfile.mkdtemp()
    try:
        json.dump({'companies': [dict(CO['신한라이프'], years={'2025': SH25})]},
                  open(os.path.join(tmp, 'web.json'), 'w', encoding='utf-8'), ensure_ascii=False)
        answers = iter(['조정 추정치 변동 −8,470억 원은 해지율 손실 때문입니다.',
                        '조정 추정치 변동은 −8,470억 원으로 CSM을 줄였습니다.'])
        ai_explain.D, ai_explain.call = tmp, (lambda msgs, key: next(answers))
        os.environ['ANTHROPIC_API_KEY'] = 'test-key'
        ai_explain.main()
        out = json.load(open(os.path.join(tmp, 'ai_explanations.json'), encoding='utf-8'))['신한라이프|2025']
        assert out['status'] == 'fixed' and out['first_try']['style'] == ['때문', '손실'], out
    finally:
        os.environ.pop('ANTHROPIC_API_KEY', None); shutil.rmtree(tmp)


# ---------- 6. 미래 이익 체력: 상각률·대체율·상각 예정표 ----------
def test_release_rate_and_replace_identity():
    for c in WEB['companies']:
        for y, r in c['years'].items():
            if r.get('release_rate') is None:
                continue
            base = r['close'] - r['release']
            assert abs(r['release_rate'] * base + r['release']) <= base * 0.00005 + 0.5, (c['name'], y)   # 비율은 소수 4자리 반올림
            assert abs(r['replace'] * -r['release'] - r['new']) <= max(1, r['new'] * 0.002), (c['name'], y)


def test_guard_reads_times():
    n = {x['raw']: x['value'] for x in extract('신계약 CSM이 상각액의 2.24배')}
    assert n['2.24배'] == 2.24
    assert verify('대체율 2.24배', {'배': [2.239]})['ok'] and not verify('대체율 2.42배', {'배': [2.239]})['ok']


def test_schedule_bucket_labels():
    import extract_schedule as S, numpy as np
    assert S.buckets(['구분', '1년이내', '1년초과~2년이내', '2년초과~5년이내', '합 계']) == [None, (0, 1), (1, 2), (2, 5), 'total']
    b = S.buckets(['1년', '2년', '10년', '11년~15년', '30년 이후', '현재가치'])
    assert b[0] == (0, 1) and b[1] == (1, 2) and b[2] == (2, 10) and b[3] == (10, 15) and b[4] == (30, np.inf) and b[5] == 'total', b
    b = S.buckets(['1년이내', '1년초과~2년이내', '10년초과'])
    assert b == [(0, 1), (1, 2), (10, np.inf)], b


def test_schedule_asof_never_after_report():
    """예정표 기준일은 그 보고서의 당기 또는 전기 연말이어야 함(보고서보다 뒤 시점 불가)"""
    import pandas as pd
    for f in ('schedule.csv', 'sensitivity.csv'):
        p = os.path.join(D, f)
        if not os.path.exists(p):
            continue
        d = pd.read_csv(p, dtype={'asof': str, 'report': str})
        for r in d.itertuples():
            assert r.asof <= r.report and r.asof in (r.report, f'{int(r.report[:4]) - 1}.12'), (f, r.company, r.asof, r.report)


def test_no_identical_series_across_companies():
    """다른 회사 값이 섞이면 두 회사가 같은 숫자 묶음을 갖게 됨 — 변동표·예정표·FISIS·민감도 모두 회사 간 중복 금지"""
    import pandas as pd
    checks = [('movement.csv', ['open', 'new', 'release', 'close']), ('fisis_metrics.csv', ['kics', 'p13', 'p25', 'prot_init']),
              ('schedule.csv', ['1년 이내', '1~2년', '2~5년', '5~10년', '10년 초과']), ('sensitivity.csv', ['d_mort', 'd_lapse_up'])]
    for f, cols in checks:
        p = os.path.join(D, f)
        if not os.path.exists(p):
            continue
        d = pd.read_csv(p).dropna(subset=cols)
        sig = d[cols].round(1).astype(str).agg('|'.join, axis=1)
        owners = d.groupby(sig).company.nunique()
        assert (owners <= 1).all(), (f, owners[owners > 1].index.tolist()[:3])


def test_fisis_company_codes_pinned():
    """FISIS 회사코드 ↔ 회사명 대응을 고정값과 대조(이름 부분일치로 다른 회사가 붙는 것 방지)"""
    import pandas as pd
    p = os.path.join(ROOT, 'raw', 'fisis_companies.csv')
    if not os.path.exists(p):
        return
    pin = {'0010599': '신한라이프', '0010595': '삼성생명', '0010593': '한화생명', '0010597': '교보생명', '0013173': 'NH농협생명',
           '0010616': 'KB라이프', '0010607': 'KDB생명', '0010619': 'DB생명', '0010620': '메트라이프', '0011434': '라이나생명',
           '0010605': 'iM라이프', '0012455': 'IBK연금', '0010594': 'ABL생명', '0010615': 'AIA생명', '0011226': 'BNP카디프생명',
           '0013436': '교보라이프플래닛', '0010608': '미래에셋생명', '0010622': '동양생명', '0010596': '흥국생명', '0010618': '하나생명', '0010625': '처브라이프', '0011320': '푸본현대생명'}
    m = pd.read_csv(p, dtype=str).dropna(subset=['co'])
    got = dict(zip(m.finance_cd, m.co))
    for cd, co in pin.items():
        assert got.get(cd) == co, (cd, got.get(cd), co)
    assert m.co.value_counts().max() == 1 and len(m) == 22, m.co.value_counts().head()


def test_schedule_totals_match_close():
    """예정표는 합계가 그 시점 기말 CSM과 맞는 표만 채택 — 구간 합도 잔액과 맞아야 함(구간 해석 오류 탐지)"""
    import pandas as pd
    p = os.path.join(D, 'schedule.csv')
    if not os.path.exists(p):
        return
    sc = pd.read_csv(p, dtype={'asof': str})
    for r in sc.itertuples():
        if r.basis == '할인 전':                          # 할인 전 금액으로 공시한 회사: 구간 합이 현재가치보다 조금 큼
            assert 1.0 <= r.nominal / r.total <= 1.2, (r.company, r.asof, r.total, r.nominal)
        else:
            assert abs(r.total - r.nominal) <= max(2, abs(r.total) * 0.01) or r.basis == '합계 열', (r.company, r.asof, r.total, r.nominal)
        vals = [sc.loc[r.Index, c] for c in ('1년 이내', '1~2년', '2~5년', '5~10년', '10년 초과')]
        if any(pd.isna(v) for v in vals):                # 구간이 표준 경계를 가로지르면(예: 1~3년) 표준 5구간 합계는 비교하지 않음
            continue
        parts = sum(vals)
        assert abs(parts - r.nominal) <= max(2, abs(r.nominal) * 0.002), (r.company, r.asof, parts, r.nominal)


def test_fisis_kics_matches_dart():
    """FISIS 지급여력비율 = DART 사업보고서의 지급여력비율 (두 출처 대조) — 신한라이프 2023"""
    p = os.path.join(D, 'fisis_metrics.csv')
    if not os.path.exists(p):
        return
    import pandas as pd
    f = pd.read_csv(p, dtype={'year': str}).set_index(['company', 'year'])
    assert abs(f.loc[('신한라이프', '2023'), 'kics'] - 250.8) < 0.1


def test_fisis_persistency_matches_dart():
    """FISIS 13·25회차 유지율 = DART 사업보고서 '계약유지율' (DB생명·흥국생명이 직접 공시) — 두 출처 대조"""
    p = os.path.join(D, 'fisis_metrics.csv')
    if not os.path.exists(p):
        return
    import pandas as pd
    f = pd.read_csv(p, dtype={'year': str}).set_index(['company', 'year'])
    dart = {('DB생명', '2023'): (87.43, 68.65), ('DB생명', '2024'): (90.33, 71.98), ('DB생명', '2025'): (91.13, 77.82),
            ('흥국생명', '2023'): (76.15, 45.54), ('흥국생명', '2024'): (85.15, 60.78), ('흥국생명', '2025'): (84.86, 72.77)}
    for k, (a, b) in dart.items():
        # 6건 중 5건은 소수 둘째 자리까지 같음. DB생명 2023만 0.06·0.59%p 차이(FISIS 사후 수정 추정) → 1%p 안이면 같은 값으로 봄
        assert abs(f.loc[k, 'p13'] - a) < 1.0 and abs(f.loc[k, 'p25'] - b) < 1.0, (k, f.loc[k, ['p13', 'p25']].tolist(), a, b)


def test_schedule_summary_is_consistent():
    p = os.path.join(D, 'schedule_summary.json')
    if not os.path.exists(p):
        return
    j = json.load(open(p, encoding='utf-8'))
    assert j['n_company'] == 22 and j['n_stable'] >= 10
    assert j['d_s1_median'] < 0.01                       # 같은 기준이면 해마다 거의 그대로
    assert 0 < j['s1_range'][0] < j['s1_range'][1] < 0.5
    assert all(1 < v < 3 for v in j['bt_median'].values())   # 실제 상각 > 예정 1년 이내(신계약·이자부리 추가)


# ---------- 7. 가정 민감도 ----------
def _sens():
    import pandas as pd
    p = os.path.join(D, 'sensitivity.csv')
    return pd.read_csv(p, dtype={'asof': str, 'report': str}) if os.path.exists(p) else None


def test_sensitivity_hand_checked_shinhan_2025():
    """원문 손계산: 무배당 7,199,651→6,475,155, 유배당 130,865→130,294, 변액 223,218→190,331 (백만원) → 합 −757,954 = −7,579.5억"""
    s = _sens()
    if s is None:
        return
    r = s[(s.company == '신한라이프') & (s['asof'] == '2025.12') & (s.report == '2025.12')].iloc[0]   # .asof는 pandas 메서드
    assert abs(r.d_lapse_up + 7579.5) < 0.6 and abs(r.base - 75537.3) < 0.6, (r.d_lapse_up, r.base)


def test_sensitivity_sanity():
    s = _sens()
    if s is None:
        return
    for r in s.itertuples():
        assert 0.92 <= r.base / r.close <= 1.08, (r.company, r.asof, r.base, r.close)       # 그 회사 기말 CSM과 맞는 표만
        ps = [getattr(r, c) for c in s.columns if c.startswith('p_') and pd_notna(getattr(r, c))]
        assert all(abs(v) < 0.6 for v in ps), (r.company, r.asof)
        if pd_notna(r.p_lapse_dn):
            assert r.p_lapse_up * r.p_lapse_dn < 0, (r.company, r.asof)                     # 해지율 상승·하락은 반대 방향


def test_sensitivity_hand_checked_hanwha_2023():
    """독립 검토에서 찾은 오류의 회귀 테스트: 한화 2023.12 사업비 충격 −217,202 / 9,238,488 = −2.35% ('_' 빈칸 처리)"""
    s = _sens()
    if s is None:
        return
    r = s[(s.company == '한화생명') & (s['asof'] == '2023.12')]
    if len(r):
        assert abs(r.iloc[0].p_exp + 0.0235) < 0.0005, r.iloc[0].p_exp



# ---------- 손해보험(segments/nonlife) — 같은 파이프라인을 CSM_SEGMENT=nonlife로 돌린 결과 ----------
ND = os.path.join(ROOT, 'segments', 'nonlife', 'data')
NR = os.path.join(ROOT, 'segments', 'nonlife', 'raw')


def _nl(f, **kw):
    import pandas as pd
    p = os.path.join(ND, f)
    return pd.read_csv(p, dtype={'year': str, 'asof': str, 'report': str}, **kw) if os.path.exists(p) else None


def test_nonlife_identity_holds():
    """손보 변동표: 기초 + 신계약 + 상각 + 이자부리 + 조정 (+ 계약이전·합병, CSM 열의 손실부담 행) = 기말 — 모든 회사·기간"""
    mv = _nl('movement.csv')
    if mv is None:
        return
    assert len(mv) >= 70 and mv.close.notna().all(), len(mv)
    for r in mv.fillna({'transfer': 0, 'other': 0}).itertuples():
        if '잔차' in r.source:
            continue
        gap = r.open + r.new + r.release + r.interest + r.adjust + r.transfer + r.other - r.close
        assert abs(gap) <= max(1.0, abs(r.close) * 0.001), (r.company, r.year, round(gap, 1))


def test_nonlife_liability_rows_match_fisis():
    """표를 고른 근거: 표의 '부채인 계약' 기말 CSM = 금감원 FISIS(SI152 잔여보장요소 보험계약마진). 순액 ≥ 부채인 계약(자산인 계약의 CSM이 더해짐)"""
    mv, fz = _nl('movement.csv'), _nl('fisis.csv')
    if mv is None:
        return
    m = mv.merge(fz, on=['company', 'year'], how='left')
    n = 0
    for r in m.itertuples():
        if r.close_liab == r.close_liab and r.fisis_close == r.fisis_close and r.fisis_close > 0:
            assert abs(r.close_liab - r.fisis_close) <= max(1.0, r.fisis_close * 0.003), (r.company, r.year, r.close_liab, r.fisis_close)
            n += 1
        if r.close_liab == r.close_liab:
            assert r.close >= r.close_liab - 1, (r.company, r.year, r.close, r.close_liab)
        if '순액 범위' in r.source:                      # 순액만 공시한 표: 금감원 값 이상, +15% 이내
            assert r.fisis_close <= r.close + 1 and r.close <= r.fisis_close * 1.15 + 1, (r.company, r.year)
    assert n >= 45, n


def test_nonlife_hand_checked_db_2025():
    """DB손해보험 2025 사업보고서 표 2114(XBRL형: 유배당·무배당 × [현재가치, 위험조정, CSM 3열, 합계])를 손으로 더한 값
    기말 순액 CSM = 15,626+0+2,831+2,533,282+3,569,353+6,084,251 = 12,205,343백만원, 부채인 계약 = 11,537,419, 신계약 = 325+2,932,368"""
    mv = _nl('movement.csv')
    if mv is None:
        return
    r = mv[(mv.company == 'DB손해보험') & (mv.year == '2025')].iloc[0]
    assert abs(r.close - 122053.4) < 0.1 and abs(r.close_liab - 115374.2) < 0.1 and abs(r.new - 29326.9) < 0.1, r.to_dict()
    assert abs(r.open - 122317.6) < 0.1                    # = 2024 기말(순액)


def test_nonlife_hand_checked_kb_2025_both_tables():
    """독립 검토가 찾은 오류의 회귀 테스트: KB손해보험은 순액만, 유배당(표 268)·무배당(표 270)을 나눠 공시 —
    둘 다 더해야 전환방법별 합계 표(286)와 같음: 기초 8,820,482 · 신계약 1,741,856 · 상각 (853,436) · 기말 9,285,033 백만원"""
    mv = _nl('movement.csv')
    if mv is None:
        return
    r = mv[(mv.company == 'KB손해보험') & (mv.year == '2025')].iloc[0]
    assert abs(r.open - 88204.8) < 0.1 and abs(r.new - 17418.6) < 0.1 and abs(r.release + 8534.4) < 0.1 and abs(r.close - 92850.3) < 0.1, r.to_dict()
    r = mv[(mv.company == 'KB손해보험') & (mv.year == '2026 상반기')].iloc[0]
    assert abs(r.open - 92850.3) < 0.1 and abs(r.close - 107216.4) < 0.1, r.to_dict()


def test_nonlife_continuity_strict():
    """손보 기초 = 전기 기말: 1억 원 또는 0.01% 안 — 넘으면 원문을 확인한 재작성(검토 기록)뿐이어야 함"""
    import sys as _s
    from periods import prev_year_end
    mv = _nl('movement.csv')
    if mv is None:
        return
    close = {(r.company, r.year): r.close for r in mv.itertuples()}
    gaps = [(r.company, r.year, round(r.open - close[(r.company, prev_year_end(r.year))], 1)) for r in mv.itertuples()
            if (r.company, prev_year_end(r.year)) in close and abs(r.open - close[(r.company, prev_year_end(r.year))]) > max(1.0, abs(r.open) * 0.0001)]
    assert gaps == [('롯데손해보험', '2024', -105.4)], gaps        # 2024 보고서의 2023년 말 재작성(review_notes.csv)


def test_nonlife_sensitivity_hand_checked_heungkuk_2025():
    """흥국화재 2025 표 1360(백만원): 장해·질병(정액) = 유배당 −10 + 무배당 −419,172 (장기재물기타 −13,399는 제외),
    사업비율 및 인플레이션율(합친 충격) = −173 − 99,487"""
    se = _nl('sensitivity.csv')
    if se is None:
        return
    r = se[(se.company == '흥국화재') & (se['asof'] == '2025.12') & (se.report == '2025.12')].iloc[0]
    assert abs(r.d_morb + 4191.8) < 0.1 and abs(r.d_exp + 996.6) < 0.1 and 'exp' in str(r.combined), r.to_dict()
    r = se[(se.company == '삼성화재') & (se['asof'] == '2025.12')].iloc[0]
    assert set(str(r.combined).split(';')) == {'exp', 'morb'}, r.combined     # 병합 셀로 합친 충격


def test_shown_ai_text_matches_current_numbers():
    """원표가 고쳐진 뒤 AI가 다시 쓰기 전까지는 옛 숫자로 쓴 AI 설명을 화면에 내지 않음(생보·손보)"""
    for path in (os.path.join(D, 'web.json'), os.path.join(ND, 'web.json')):
        if not os.path.exists(path):
            continue
        for c in json.load(open(path, encoding='utf-8'))['companies']:
            for y, r in c['years'].items():
                if r.get('ai'):
                    g = verify(r['ai']['text'], facts_from(r))
                    assert g['ok'], (c['name'], y, g['mismatches'])


def test_nonlife_contract_transfer():
    """엠지손해보험 2025: 계약이전 행으로 CSM 전액이 넘어가 기말 0 — 화면은 '계약이전' 막대로, 비율 지표는 비움"""
    mv = _nl('movement.csv')
    if mv is None:
        return
    r = mv[(mv.company == 'MG손해보험') & (mv.year == '2025')].iloc[0]
    assert r.close == 0 and abs(r.transfer + 2713.3) < 0.2, r.to_dict()
    web = json.load(open(os.path.join(ND, 'web.json'), encoding='utf-8'))
    y = {c['name']: c for c in web['companies']}['MG손해보험']['years']['2025']
    assert y['release_rate'] is None and y['replace'] is None and y['transfer'] < 0


def test_nonlife_fisis_company_codes_pinned():
    import pandas as pd
    p = os.path.join(NR, 'fisis_companies.csv')
    if not os.path.exists(p):
        return
    pin = {'0010633': '삼성화재', '0010636': 'DB손해보험', '0010634': '현대해상', '0010635': 'KB손해보험', '0010626': '메리츠화재',
           '0010627': '한화손해보험', '0010628': '롯데손해보험', '0010630': '흥국화재', '0013174': 'NH농협손해보험', '0011354': '하나손해보험',
           '0013376': 'MG손해보험', '0010653': 'AXA손해보험', '0013245': 'AIG손해보험', '0011353': '신한EZ손해보험',
           '0019399': '카카오페이손해보험', '0017080': '캐롯손해보험'}
    m = pd.read_csv(p, dtype=str).dropna(subset=['co'])
    got = dict(zip(m.finance_cd, m.co))
    for cd, co in pin.items():
        assert got.get(cd) == co, (cd, got.get(cd), co)
    assert m.co.value_counts().max() == 1 and len(m) == 16, m.co.value_counts().head()


def test_nonlife_no_identical_series_across_companies():
    for f, cols in (('movement.csv', ['open', 'new', 'release', 'close']), ('fisis_metrics.csv', ['kics', 'p13', 'p25', 'init_m']),
                    ('schedule.csv', ['1년 이내', '1~2년', '2~5년', '5~10년', '10년 초과']), ('sensitivity.csv', ['d_mort', 'd_lapse_up'])):
        d = _nl(f)
        if d is None:
            continue
        d = d.dropna(subset=cols)
        sig = d[cols].round(1).astype(str).agg('|'.join, axis=1)
        owners = d.groupby(sig).company.nunique()
        assert (owners <= 1).all(), (f, owners[owners > 1].index.tolist()[:3])


def test_nonlife_schedule_and_sensitivity_match_close():
    """손보 예정표 합계·민감도 기준 CSM이 그 시점 기말 CSM(순액)과 맞고, 예정표 기준일은 보고서 기간 이후가 아님"""
    import pandas as pd
    sc, se, mv = _nl('schedule.csv'), _nl('sensitivity.csv'), _nl('movement.csv')
    if sc is None:
        return
    close = {(r.company, r.year[:4] + ('.06' if '상반기' in r.year else '.12')): r.close for r in mv.itertuples()}
    for r in sc.itertuples():
        assert r.asof <= r.report, (r.company, r.asof, r.report)
        c = close.get((r.company, r.asof))
        if c is not None:
            assert abs(r.total - c) <= max(1.0, abs(c) * 0.002), (r.company, r.asof, r.total, c)
        if r.basis != '할인 전':
            assert abs(r.total - r.nominal) <= max(2, abs(r.total) * 0.01) or r.basis == '합계 열', (r.company, r.asof)
    for r in se.itertuples():
        assert 0.92 <= r.base / r.close <= 1.08, (r.company, r.asof, r.base, r.close)


def test_nonlife_every_flag_reviewed_and_summaries_pass():
    ck = list(csv.DictReader(open(os.path.join(ND, 'checks.csv'), encoding='utf-8-sig')))
    bad = [(k['company'], k['year'], k['rule']) for k in ck if k['status'] in ('불일치', '주의') and not k['decision']]
    assert not bad, bad
    web = json.load(open(os.path.join(ND, 'web.json'), encoding='utf-8'))
    assert web['meta']['seg']['key'] == 'nonlife' and web['meta']['n_company'] >= 15
    for c in web['companies']:
        for y, r in c['years'].items():
            assert r['summary_guard']['ok'], (c['name'], y, r['summary_guard'])


def pd_notna(v):
    import pandas as pd
    return pd.notna(v)


if __name__ == '__main__':
    tests = [v for k, v in dict(globals()).items() if k.startswith('test_')]
    fail = 0
    for t in tests:
        try:
            t(); print('통과', t.__name__)
        except Exception as e:
            fail += 1; print('실패', t.__name__, '→', repr(e)[:300])
    print(f'\n{len(tests) - fail}/{len(tests)} 통과')
    sys.exit(1 if fail else 0)
