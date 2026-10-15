"""
보강 수집: 이미 받은 보고서(raw/filings.csv)의 원문을 다시 받아, 변동표 외에 분석에 쓸 표를 추가로 모음
- 대상 표(표 전체 글자 기준)
  schedule : '보험계약마진' + 기간 구간('1년 이내/이하', '10년 초과' 등) → CSM 상각(인식) 예정표
  bucket   : 그 밖에 '○년'이 3번 이상 나오는 보험 관련 표 → 머리글 형식이 다른 예정표 후보(합계가 기말 CSM과 맞는지로 판별)
  csm      : '보험계약마진'이 있는 나머지 표 → 조정 항목 분해 등
  kics     : '지급여력' 또는 'K-ICS' → 지급여력비율
  biz      : '유지율' 또는 '초회보험료'/'신계약' 실적 → 영업 지표
- 결과: raw/extra_tables.csv.gz  (열: co, period, rcept_no, doc, table_no, kind, row, col, value)
- 키: 환경변수 DART_API_KEY (GitHub Secrets). 실행: GitHub Actions '보강 수집' 워크플로
  --only 신한라이프 처럼 회사를 지정하면 그 회사만(시험용)
"""
import os, re, sys, time
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from collect_dart import get, unzip_or_die, note, FILINGS, B
from lxml import html as LH

OUT = os.path.join(B, 'raw', 'extra_tables.csv.gz')
COLS = ['co', 'period', 'rcept_no', 'doc', 'table_no', 'kind', 'ctx', 'row', 'col', 'value']
BUCKET = re.compile(r'1\s*년\s*(이내|이하|미만)|10\s*년\s*(초과|이상|이후)|5\s*년\s*(초과|이내|이하)')


def before(nd, n=160):
    """표 바로 앞의 글(표 제외) 끝 n자 — 앞 형제 → 부모의 앞 형제 순으로 거슬러 올라가며 모음(빠름)"""
    parts, cur, hops = [], nd, 0
    while cur is not None and sum(map(len, parts)) < n and hops < 12:
        prev = cur.getprevious()
        while prev is not None and sum(map(len, parts)) < n:
            if prev.tag != 'table' and not prev.xpath('.//table'):
                parts.insert(0, ' '.join(prev.text_content().split()))
            prev = prev.getprevious(); hops += 1
        cur = cur.getparent()
    return ' '.join(' '.join(parts).split())[-n:]


def tables_ctx(zbytes):
    """원문 zip → [(문서, 표번호, 표 앞 문장 120자, 행, 열, 값)] — collect_dart.tables_of와 같은 번호 규칙에 표 앞 문맥을 더함
    (상각 예정표는 '보험계약마진'이 표 안이 아니라 표 위 제목에만 있는 경우가 많음)"""
    z = unzip_or_die(zbytes, 'document')
    out, tno = [], 0
    for name in sorted(z.namelist()):
        text = re.sub(r'^\s*<\?xml[^>]*\?>', '', z.read(name).decode('utf-8', 'replace'))
        try:
            doc = LH.document_fromstring(text)
        except Exception:
            continue
        for nd in doc.xpath('//table[not(ancestor::table)]'):
            tno += 1
            ctx = before(nd)
            for r, tr in enumerate(nd.xpath('.//tr'), 1):
                for c, td in enumerate(tr.xpath('./*'), 1):
                    out.append((name, tno, ctx, r, c, ' '.join(td.text_content().split())))
    return out


YEARS = re.compile(r'\d+\s*년')


def kind_of(txt, ctx=''):
    if BUCKET.search(txt) and ('보험계약마진' in txt or '보험계약마진' in ctx):
        return 'schedule'
    if len(YEARS.findall(txt)) >= 3 and ('보험' in txt or '보험' in ctx):
        return 'bucket'                               # 연도 구간 표(머리글 형식이 다른 상각 예정표 후보) — 합계로 판별
    if '보험계약마진' in txt:
        return 'csm'
    if '지급여력' in txt or 'K-ICS' in txt.upper():
        return 'kics'
    if '유지율' in txt or '초회보험료' in txt:
        return 'biz'
    return None


def pick_tables(cells, f):
    df = pd.DataFrame(cells, columns=['doc', 'table_no', 'ctx', 'row', 'col', 'value'])
    if df.empty:
        return pd.DataFrame(columns=COLS)
    g = df.groupby(['doc', 'table_no'])
    txt, ctx = g.value.apply(' '.join), g.ctx.first()
    kinds = pd.Series({k: kind_of(txt[k], ctx[k]) for k in txt.index}, dtype=object).dropna()
    if kinds.empty:
        return pd.DataFrame(columns=COLS)
    kinds.index = pd.MultiIndex.from_tuples(kinds.index, names=['doc', 'table_no'])
    df = df.set_index(['doc', 'table_no']).loc[kinds.index].reset_index()
    df['kind'] = df.set_index(['doc', 'table_no']).index.map(kinds)
    return df.assign(co=f['co'], period=f['period'], rcept_no=f['rcept_no'])[COLS].astype(str)


def main():
    key = os.environ.get('DART_API_KEY')
    if not key:
        raise SystemExit('DART_API_KEY가 없습니다.')
    have = pd.read_csv(FILINGS, dtype=str)
    done = pd.read_csv(OUT, dtype=str).fillna('') if os.path.exists(OUT) else pd.DataFrame(columns=COLS)
    if 'ctx' not in done.columns:                     # 표 앞 문맥이 없던 이전 형식 → 처음부터 다시
        done = pd.DataFrame(columns=COLS)
    stale = set(done.rcept_no) - set(have.rcept_no)   # 정정공시로 대체된 옛 보고서의 표는 버림(새 표와 섞이지 않게)
    if stale:
        print(f'정정으로 대체된 보고서 {len(stale)}건의 표 제거')
        done = done[~done.rcept_no.isin(stale)]
    if '--only' in sys.argv:                          # 시험 실행: 그 회사만 다시 받음
        only = sys.argv[sys.argv.index('--only') + 1]
        have, done = have[have.co == only], done[done.co != only]
    got = set(done.rcept_no)
    out, n = [done], 0
    for f in have.to_dict('records'):
        if f['rcept_no'] in got:
            continue
        try:
            cells = tables_ctx(get('document.xml', {'crtfc_key': key, 'rcept_no': f['rcept_no']}, binary=True))
        except SystemExit as e:                       # 한도 초과 등 → 받은 데까지 저장하고 멈춤
            note(f"{f['co']} {f['period']}: {e}", 'warning'); break
        df = pick_tables(cells, f)
        cnt = df.groupby('kind')[['doc', 'table_no']].apply(lambda d: len(d.drop_duplicates())).to_dict() if len(df) else {}
        print(f"{f['co']} {f['period']}: {cnt}")
        out.append(df); n += 1
        if n % 10 == 0:                               # 중간 저장(시간 초과에 대비)
            pd.concat(out, ignore_index=True).to_csv(OUT, index=False, encoding='utf-8', compression='gzip')
        time.sleep(0.5)
    res = pd.concat(out, ignore_index=True)
    res.to_csv(OUT, index=False, encoding='utf-8', compression='gzip')
    note(f'보강 수집: 보고서 {n}건 새로 처리, 표 {res.groupby(["rcept_no", "doc", "table_no"]).ngroups:,}개 · '
         + ', '.join(f'{k} {v}' for k, v in res.drop_duplicates(["rcept_no", "doc", "table_no"]).kind.value_counts().items()))


if __name__ == '__main__':
    main()
