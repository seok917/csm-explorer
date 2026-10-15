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


def test_new_filing_becomes_new_period():   # 새 3분기 공시가 스냅숏에 들어오면 기간이 자동으로 추가되고 기초=전년 연말로 표를 고름
    import pandas as pd, extract_movement as E
    tmp = tempfile.mkdtemp()
    try:
        for f in os.listdir(D):
            if f.endswith('.csv'):
                shutil.copy(os.path.join(D, f), tmp)
        s = pd.read_csv(E.SNAP, dtype=str).fillna('')
        fake = s[(s.co == '신한라이프') & (s.period == '2026.06')].assign(period='2026.09', rcept_no='20261114999999')
        snap = os.path.join(tmp, 'snap.csv.gz'); pd.concat([s, fake]).to_csv(snap, index=False, compression='gzip')
        old = (E.D, E.SNAP, E.DUMP)
        E.D, E.SNAP, E.DUMP = tmp, snap, os.path.join(tmp, 'no-dump')
        try:
            E.main()
        finally:
            E.D, E.SNAP, E.DUMP = old
        mv = pd.read_csv(os.path.join(tmp, 'movement.csv'), dtype={'year': str}).set_index(['company', 'year'])
        assert abs(mv.loc[('신한라이프', '2026 3분기')].open - mv.loc[('신한라이프', '2025')].close) < 0.5
        assert ('신한라이프', '2026 3분기') not in set(pd.read_csv(os.path.join(D, 'movement.csv'), dtype={'year': str})[['company', 'year']].itertuples(index=False, name=None))
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
            base = r['open'] + r['new'] + r['interest'] + r['adjust']
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
        vals = [getattr(r, f'_{i}') for i in range(8, 13)]
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


def test_schedule_summary_is_consistent():
    p = os.path.join(D, 'schedule_summary.json')
    if not os.path.exists(p):
        return
    j = json.load(open(p, encoding='utf-8'))
    assert j['n_company'] == 22 and j['n_stable'] >= 10
    assert j['d_s1_median'] < 0.01                       # 같은 기준이면 해마다 거의 그대로
    assert 0 < j['s1_range'][0] < j['s1_range'][1] < 0.5
    assert all(1 < v < 3 for v in j['bt_median'].values())   # 실제 상각 > 예정 1년 이내(신계약·이자부리 추가)


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
