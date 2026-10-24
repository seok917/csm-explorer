"""
FISIS(금융통계정보시스템) 오픈API 수집: 보험사 통계 중 CSM 해석에 쓸 지표만 (업권은 seg.py)
- 손보(CSM_SEGMENT=nonlife): SI021 자본적정성·SI025 유지율·SI114 경영효율·SI137 주요손익·SI151 손익원천·SI152 책임준비금 등
- 생보 SH021 지급여력(RBC/K-ICS)  · SH025 유지율(13·25회차 등)  · SH160 신계약  · SH166 수입보험료
- 키: 환경변수 FISIS_API_KEY (GitHub Secrets) — 코드에 없음
- 결과: raw/fisis_stats.csv  (co, list_no, list_nm, term, base_month, account_cd, account_nm, column, value)
        raw/fisis_companies.csv (FISIS 회사코드 ↔ 분석 대상 22개사 대응표, 확인용)
- 반기·분기 공시가 없는 지표가 있어 분기(Q) → 반기(H) → 연(Y) 순서로 시도
실행: FISIS_API_KEY=... python pipeline/collect_fisis.py
"""
import json, os, re, sys, time, urllib.parse, urllib.request
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from collect_dart import note, B

import seg

API = 'https://fisis.fss.or.kr/openapi'
LISTS = {'SH021': '지급여력', 'SH025': '유지율', 'SH160': '신계약', 'SH166': '수입보험료'}
PART = 'H'                                              # FISIS 업권 코드: H 생명보험, I 손해보험
if seg.SEG == 'nonlife':
    PART = 'I'
    LISTS = {'SI021': '자본적정성', 'SI025': '유지율', 'SI114': '경영효율지표', 'SI137': '주요손익현황', 'SI138': '종목별 보유보험료',
             'SI147': '재무상태표(부채·자본)', 'SI150': '손익계산서', 'SI151': '손익발생원천별 실적', 'SI152': '책임준비금',
             'SI027': '보험료 수입(수납형태)', 'SI028': '보험료 수입(납입방법)', 'SI029': '보험료 수입(모집방법)'}
START, END = '202312', time.strftime('%Y%m')
# FISIS 회사명(정식 명칭·음차 표기) → 분석 대상 이름
KEYS = [('케이디비', 'KDB생명'), ('KDB', 'KDB생명'), ('삼성생명', '삼성생명'), ('한화생명', '한화생명'), ('교보라이프플래닛', '교보라이프플래닛'), ('라이프플래닛', '교보라이프플래닛'),
        ('교보생명', '교보생명'), ('신한라이프', '신한라이프'), ('농협생명', 'NH농협생명'), ('KB라이프', 'KB라이프'), ('케이비라이프', 'KB라이프'),
        ('메트라이프', '메트라이프'), ('동양생명', '동양생명'), ('흥국생명', '흥국생명'), ('라이나', '라이나생명'), ('미래에셋', '미래에셋생명'),
        ('DB생명', 'DB생명'), ('디비생명', 'DB생명'), ('AIA', 'AIA생명'), ('에이아이에이', 'AIA생명'), ('ABL', 'ABL생명'), ('에이비엘', 'ABL생명'),
        ('iM라이프', 'iM라이프'), ('아이엠라이프', 'iM라이프'), ('DGB생명', 'iM라이프'),
        ('하나생명', '하나생명'), ('IBK', 'IBK연금'), ('아이비케이', 'IBK연금'), ('푸본현대', '푸본현대생명'), ('처브', '처브라이프'),
        ('카디프', 'BNP카디프생명')]
ALLOW_CLOSED = ()                                        # [폐] 표시가 있어도 받을 법인(합병으로 사라졌지만 그 전 기간은 분석 대상)
if seg.SEG == 'nonlife':
    KEYS = [('삼성화재', '삼성화재'), ('DB손해', 'DB손해보험'), ('현대해상', '현대해상'), ('KB손해', 'KB손해보험'), ('메리츠화재', '메리츠화재'),
            ('한화손해', '한화손해보험'), ('롯데손해', '롯데손해보험'), ('흥국화재', '흥국화재'), ('농협손해', 'NH농협손해보험'),
            ('하나손해', '하나손해보험'), ('엠지손해', 'MG손해보험'), ('악사손해', 'AXA손해보험'), ('에이아이지손해', 'AIG손해보험'),
            ('신한EZ', '신한EZ손해보험'), ('카카오페이손해', '카카오페이손해보험'), ('캐롯손해', '캐롯손해보험')]
    ALLOW_CLOSED = ('캐롯',)


def call(op, key, **params):
    url = f'{API}/{op}.json?' + urllib.parse.urlencode(dict(auth=key, lang='kr', **params))
    for attempt in range(3):
        try:
            with urllib.request.urlopen(url, timeout=60) as r:
                res = json.loads(r.read().decode('utf-8'))['result']
            break
        except Exception:
            if attempt == 2:
                raise
            time.sleep(3)
    if res.get('err_cd') not in ('000', None):
        return res.get('err_cd'), res.get('err_msg'), res
    return '000', '', res


def rows(res):
    x = res.get('list') or []
    return [x] if isinstance(x, dict) else x


def legend(res):
    """값 열 이름표: description은 [{column_id, column_nm}] 목록(한 개면 객체, 드물게 {'column': [...]})"""
    d = res.get('description')
    if isinstance(d, dict) and 'column_id' not in d:
        d = d.get('column')
    if isinstance(d, dict):
        d = [d]
    return {c.get('column_id'): c.get('column_nm') for c in (d or []) if isinstance(c, dict)}


def match(name):
    if '[폐]' in name and not any(k in name for k in ALLOW_CLOSED):   # 폐업·합병된 옛 법인 제외
        return None
    n = name.replace(' ', '').upper()
    for k, v in KEYS:
        if k.upper() in n:
            return v
    return None


def fetch_one(key, r, ln, lnm):
    for term in ('Q', 'H', 'Y'):
        cd, msg, res = call('statisticsInfoSearch', key, financeCd=r.finance_cd, listNo=ln, term=term,
                            startBaseMm=START, endBaseMm=END)
        if cd == '000' and rows(res):
            break
        time.sleep(0.2)
    else:
        return None
    cols = legend(res)
    units = [u.strip() for u in str(res.get('unit') or '').split(',')]
    uo = dict(zip(cols, units)) if len(units) == len(cols) else {}
    out = []
    for x in rows(res):
        for k, v in x.items():
            if k in ('base_month', 'finance_cd', 'finance_nm', 'account_cd', 'account_nm'):
                continue
            out.append(dict(co=r.co, list_no=ln, list_nm=lnm, term=term, base_month=x.get('base_month'),
                            account_cd=x.get('account_cd'), account_nm=x.get('account_nm'),
                            column=cols.get(k, k), unit=uo.get(k, ''), value=v))
    return out


def main():
    key = os.environ.get('FISIS_API_KEY')
    if not key:
        note('FISIS_API_KEY가 등록되지 않아 FISIS 수집을 건너뜁니다.', 'warning'); return
    cd, msg, res = call('companySearch', key, partDiv=PART)
    if cd != '000':
        raise SystemExit(f'FISIS 회사 목록 오류 {cd}: {msg}')
    comp = pd.DataFrame(rows(res))
    comp['co'] = comp.finance_nm.map(match)
    comp.to_csv(os.path.join(B, 'raw', 'fisis_companies.csv'), index=False, encoding='utf-8-sig')
    tgt = comp.dropna(subset=['co']).drop_duplicates('co')
    miss = sorted(set(v for _, v in KEYS) - set(tgt.co))
    note(f"FISIS {seg.LABEL}사 {len(comp)}곳 중 분석 대상 {len(tgt)}곳 대응" + (f" · 못 찾음: {miss}" if miss else ''))
    out = []
    errs = []
    for r in tgt.itertuples():
        for ln, lnm in LISTS.items():
            try:
                got = fetch_one(key, r, ln, lnm)
            except Exception as e:                      # 한 지표 실패가 전체를 멈추지 않게
                errs.append(f'{r.co} {ln}: {type(e).__name__} {e}'); continue
            if got is None:
                print(f'  {r.co} {ln}: 자료 없음'); continue
            out += got
            time.sleep(0.2)
        print(f'  {r.co}: 누적 {len(out):,}행')
    for e in errs[:10]:
        note(e, 'warning')
    df = pd.DataFrame(out)
    old_p = os.path.join(B, 'raw', 'fisis_stats.csv')
    if os.path.exists(old_p) and len(df):                # 이번에 실패한 회사·지표는 지난번 값을 유지(빈칸으로 덮어쓰지 않음)
        old = pd.read_csv(old_p, dtype=str)
        got = set(zip(df.co, df.list_no))
        keep = old[[(c, l) not in got for c, l in zip(old.co, old.list_no)]]
        if len(keep):
            note(f'이번에 못 받은 {keep.groupby(["co", "list_no"]).ngroups}개 회사·지표는 지난 값 유지', 'warning')
        df = pd.concat([df, keep], ignore_index=True)
    if df.empty:
        raise SystemExit('FISIS 통계를 하나도 받지 못해 기존 파일을 그대로 둡니다.')
    df.to_csv(os.path.join(B, 'raw', 'fisis_stats.csv'), index=False, encoding='utf-8-sig')
    note(f"FISIS 통계 {len(df):,}행 저장 · 지표별 회사 수 " +
         ', '.join(f"{LISTS[k]} {v}" for k, v in df.groupby('list_no').co.nunique().items()))


if __name__ == '__main__':
    main()
