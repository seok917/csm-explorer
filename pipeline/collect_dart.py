"""
분기 자동 업데이트 1단계: DART Open API에서 새 정기보고서를 받아 CSM 표만 원문 스냅숏에 추가
- 키: 환경변수 DART_API_KEY (GitHub Secrets) — 코드에 없음
- 대상: 분석 대상 22개사. 정기보고서(사업·반기·분기)를 내는 회사는 정기보고서, 아니면 감사보고서
- 새 보고서 = 이미 받은 최신 기간(raw/filings.csv) 이후 기간, 또는 이미 받은 기간의 정정공시(더 최근 접수)
- 원문(document.xml) → 문서 순서대로 최상위 표를 행·열 셀로 펼침 (R 17_dart_all_tables.R과 같은 규칙: libxml2 HTML 파서)
- '보험계약마진'과 '기말'이 있는 표만 raw/csm_tables.csv.gz에 추가 (정정공시면 같은 기간의 표를 교체)

실행: DART_API_KEY=... python pipeline/collect_dart.py                          새 공시 확인·추가
      DART_API_KEY=... python pipeline/collect_dart.py --verify 신한라이프 2025.12   받은 원문을 다시 펼쳐 스냅숏과 대조(수집기 점검)
GitHub Actions에서는 새로 추가한 보고서 수를 출력값 new로 남김 → 0이면 뒤 단계(추출·검증·AI·화면)를 건너뜀
"""
import io, json, os, re, sys, time, urllib.parse, urllib.request, zipfile
import pandas as pd
from lxml import etree, html as LH

sys.path.insert(0, os.path.dirname(__file__))
from build_data import SHORT
from periods import label, order_key

B = os.path.join(os.path.dirname(__file__), '..')
SNAP = os.path.join(B, 'raw', 'csm_tables.csv.gz')
FILINGS = os.path.join(B, 'raw', 'filings.csv')
API = 'https://opendart.fss.or.kr/api'
UA = {'User-Agent': 'Mozilla/5.0'}
COLS = ['co', 'period', 'rcept_no', 'doc', 'table_no', 'row', 'col', 'value']


def get(path, params, binary=False):
    url = f'{API}/{path}?' + urllib.parse.urlencode(params)
    for attempt in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=180) as r:
                b = r.read()
            return b if binary else json.loads(b.decode('utf-8'))
        except Exception:
            if attempt == 2:
                raise
            time.sleep(5)


def unzip_or_die(b, what):
    """DART는 오류일 때 zip 대신 짧은 XML(status·message)을 돌려줌 → 그 내용을 그대로 보여주고 중단"""
    try:
        return zipfile.ZipFile(io.BytesIO(b))
    except zipfile.BadZipFile:
        raise SystemExit(f'{what} 응답이 zip이 아닙니다: {b[:300].decode("utf-8", "replace")}  (010/011 인증키, 020 하루 한도, 800 점검)')


def corp_codes(key):
    """회사명 → DART 고유번호(동명 법인이 있으면 모두)"""
    z = unzip_or_die(get('corpCode.xml', {'crtfc_key': key}, binary=True), 'corpCode')
    root = etree.fromstring(z.read(z.namelist()[0]))
    out = {}
    for it in root.iter('list'):
        nm = ' '.join((it.findtext('corp_name') or '').split()).replace('(주)', '').replace('주식회사', '').strip()
        if nm in SHORT:
            out.setdefault(SHORT[nm], []).append(it.findtext('corp_code'))
    return out


def list_filings(key, codes, ty, since):
    rows = []
    for cc in codes:
        p = 1
        while True:
            js = get('list.json', dict(crtfc_key=key, corp_code=cc, pblntf_ty=ty, bgn_de=since,
                                       end_de=time.strftime('%Y%m%d'), page_no=p, page_count=100))
            if js['status'] == '013':                      # 조회된 공시 없음(정상)
                break
            if js['status'] != '000':
                raise SystemExit(f"DART 응답 오류 {js['status']}: {js['message']}  (010/011 인증키, 020 하루 한도, 800 점검)")
            rows += js['list']
            if p >= int(js['total_page']):
                break
            p += 1
        time.sleep(0.3)
    return rows


def pick(rows, pattern, exclude=None):
    """보고서명으로 거르고 기간별 최신 접수본(정정공시 반영)만"""
    best = {}
    for r in rows:
        nm = r['report_nm']
        if not re.search(pattern, nm) or (exclude and re.search(exclude, nm)):
            continue
        m = re.search(r'(\d{4})\.(\d{2})', nm)
        if not m or m.group(2) not in ('03', '06', '09', '12'):
            continue
        per = m.group(0)
        if per not in best or (r['rcept_dt'], r['rcept_no']) > (best[per]['rcept_dt'], best[per]['rcept_no']):
            best[per] = dict(period=per, report_nm=nm, rcept_no=r['rcept_no'], rcept_dt=r['rcept_dt'])
    return list(best.values())


def tables_of(zbytes):
    """원문 zip → [(문서, 표번호, 행, 열, 값)] — 문서 순서대로 최상위 표만 번호를 매기고, 표 안의 모든 행·셀을 펼침"""
    z = unzip_or_die(zbytes, 'document')
    out, tno = [], 0
    for name in sorted(z.namelist()):
        text = z.read(name).decode('utf-8', 'replace')
        text = re.sub(r'^\s*<\?xml[^>]*\?>', '', text)
        try:
            doc = LH.document_fromstring(text)
        except Exception:
            continue
        for nd in doc.xpath('//table[not(ancestor::table)]'):
            tno += 1
            for r, tr in enumerate(nd.xpath('.//tr'), 1):
                for c, td in enumerate(tr.xpath('./*'), 1):
                    out.append((name, tno, r, c, ' '.join(td.text_content().split())))
    return out


def csm_only(cells, co, f):
    """'보험계약마진'과 '기말'이 들어 있는 표만 (스냅숏 기준과 같음)"""
    df = pd.DataFrame(cells, columns=['doc', 'table_no', 'row', 'col', 'value'])
    txt = df.groupby(['doc', 'table_no']).value.apply(''.join)
    keep = txt[txt.str.contains('보험계약마진') & txt.str.contains('기말')].index
    df = df.set_index(['doc', 'table_no']).loc[keep].reset_index()
    return df.assign(co=co, period=f['period'], rcept_no=f['rcept_no'])[COLS].astype(str)


def note(msg, level='notice'):
    """GitHub Actions 실행 화면에 결과 한 줄을 남김(로그를 열지 않아도 보이게)"""
    print(f'::{level} title=DART 수집::{msg}' if os.environ.get('GITHUB_ACTIONS') else msg)


def gh_output(**kv):
    p = os.environ.get('GITHUB_OUTPUT')
    if p:
        with open(p, 'a') as fh:
            for k, v in kv.items():
                fh.write(f'{k}={v}\n')


def verify(key, co, per):
    """수집기 점검: 이미 받은 보고서를 다시 받아 펼친 결과가 스냅숏(R로 만든 덤프)과 같은지"""
    have = pd.read_csv(FILINGS, dtype=str)
    f = have[(have.co == co) & (have.period == per)].iloc[0].to_dict()
    new = csm_only(tables_of(get('document.xml', {'crtfc_key': key, 'rcept_no': f['rcept_no']}, binary=True)), co, f)
    old = pd.read_csv(SNAP, dtype=str).fillna('')
    old = old[(old.co == co) & (old.period == per)]
    key_ = lambda d: set(zip(d.doc, d.table_no, d['row'], d['col'], d.value))
    a, b = key_(new.fillna('')), key_(old)
    same = len(a & b) / max(len(b), 1)
    note(f'점검 {co} {per}: CSM 표 {new.groupby(["doc", "table_no"]).ngroups}개(스냅숏 {old.groupby(["doc", "table_no"]).ngroups}개), '
         f'셀 {len(a):,} / {len(b):,}, 일치율 {same:.1%}', 'notice' if same >= 0.99 else 'error')
    import extract_movement as E
    for name, src in (('다시 받은 원문', new), ('스냅숏', old)):
        C = E.candidates(src)
        note(f'{name}: 변동표 후보 {len(C)}개, 기말 CSM 후보(억) {sorted({round(v / 100, 1) for _, m in C for v, _ in m["close"]})[:6]}')
    gh_output(new=0)
    return same


def main():
    key = os.environ.get('DART_API_KEY')
    if not key:
        note('DART_API_KEY가 등록되지 않아 수집을 건너뜁니다.', 'warning'); gh_output(new=0)
        if '--verify' in sys.argv:
            sys.exit(1)                                  # 점검을 요청했는데 키가 없으면 실패로 표시
        return
    if '--verify' in sys.argv:
        i = sys.argv.index('--verify')
        same = verify(key, sys.argv[i + 1], sys.argv[i + 2])
        sys.exit(0 if same >= 0.99 else 1)

    have = pd.read_csv(FILINGS, dtype=str)
    latest = max(order_key(label(p)) for p in have.period)
    last = {(r.co, r.period): r for r in have.itertuples()}
    audit_only = set(have[have.source == '감사보고서'].co)          # 정기보고서를 내지 않는 회사
    codes = corp_codes(key)
    todo = []
    for co in sorted(set(have.co)):
        if co not in codes:
            print(f'{co}: DART 고유번호를 찾지 못함'); continue
        if co in audit_only:
            fs, src = pick(list_filings(key, codes[co], 'F', '20240101'), '감사보고서', exclude='연결'), '감사보고서'
        else:
            fs, src = pick(list_filings(key, codes[co], 'A', '20240101'), '사업보고서|반기보고서|분기보고서'), '정기보고서'
        for f in fs:
            old = last.get((co, f['period']))
            is_new = old is None and order_key(label(f['period'])) > latest
            is_fix = old is not None and (f['rcept_dt'], f['rcept_no']) > (old.rcept_dt, old.rcept_no)
            if is_new or is_fix:
                todo.append((co, src, f, '정정' if is_fix else '신규'))
    print(f'확인한 회사 {len(set(have.co))}곳 · 새로 받을 보고서 {len(todo)}건')

    added = []
    snap = pd.read_csv(SNAP, dtype=str).fillna('')
    for co, src, f, why in todo:
        print(f"  [{why}] {co} {f['period']} {f['report_nm']} ({f['rcept_no']})")
        cells = tables_of(get('document.xml', {'crtfc_key': key, 'rcept_no': f['rcept_no']}, binary=True))
        df = csm_only(cells, co, f)
        if df.empty:
            print('    CSM 표를 찾지 못해 건너뜀'); continue
        snap = pd.concat([snap[~((snap.co == co) & (snap.period == f['period']))], df], ignore_index=True)
        have = pd.concat([have[~((have.co == co) & (have.period == f['period']))],
                          pd.DataFrame([dict(co=co, source=src, **f)])], ignore_index=True)
        added.append(f"{co} {f['period']}")
        print(f"    CSM 표 {df.groupby(['doc', 'table_no']).ngroups}개 추가")
        time.sleep(1)
    if added:
        snap.to_csv(SNAP, index=False, encoding='utf-8', compression='gzip')
        have.sort_values(['co', 'period']).to_csv(FILINGS, index=False, encoding='utf-8')
    note(f"확인 {len(set(have.co))}개사 · 새 보고서 {len(todo)}건 · 추가: {', '.join(added) if added else '없음'}")
    gh_output(new=len(added))


if __name__ == '__main__':
    try:
        main()
    except SystemExit as e:
        if e.code not in (None, 0):
            note(str(e.code), 'error')
        raise
    except Exception as e:                                # 네트워크·형식 오류도 실행 화면에 바로 보이게
        note(f'{type(e).__name__}: {e}', 'error')
        raise
