"""
FISIS(금융통계정보시스템) 오픈API 수집: 생명보험사 통계 중 CSM 해석에 쓸 지표만
- SH021 지급여력(RBC/K-ICS)  · SH025 유지율(13·25회차 등)  · SH160 신계약  · SH166 수입보험료
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

API = 'https://fisis.fss.or.kr/openapi'
LISTS = {'SH021': '지급여력', 'SH025': '유지율', 'SH160': '신계약', 'SH166': '수입보험료'}
START, END = '202312', time.strftime('%Y%m')
# FISIS 회사명(정식 명칭·음차 표기) → 분석 대상 이름
KEYS = [('삼성생명', '삼성생명'), ('한화생명', '한화생명'), ('교보라이프플래닛', '교보라이프플래닛'), ('라이프플래닛', '교보라이프플래닛'),
        ('교보생명', '교보생명'), ('신한라이프', '신한라이프'), ('농협생명', 'NH농협생명'), ('KB라이프', 'KB라이프'), ('케이비라이프', 'KB라이프'),
        ('메트라이프', '메트라이프'), ('동양생명', '동양생명'), ('흥국생명', '흥국생명'), ('라이나', '라이나생명'), ('미래에셋', '미래에셋생명'),
        ('DB생명', 'DB생명'), ('디비생명', 'DB생명'), ('AIA', 'AIA생명'), ('에이아이에이', 'AIA생명'), ('ABL', 'ABL생명'), ('에이비엘', 'ABL생명'),
        ('KDB', 'KDB생명'), ('케이디비', 'KDB생명'), ('iM라이프', 'iM라이프'), ('아이엠라이프', 'iM라이프'), ('DGB생명', 'iM라이프'),
        ('하나생명', '하나생명'), ('IBK', 'IBK연금'), ('아이비케이', 'IBK연금'), ('푸본현대', '푸본현대생명'), ('처브', '처브라이프'),
        ('카디프', 'BNP카디프생명')]


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


def match(name):
    n = name.replace(' ', '').upper()
    for k, v in KEYS:
        if k.upper() in n:
            return v
    return None


def main():
    key = os.environ.get('FISIS_API_KEY')
    if not key:
        note('FISIS_API_KEY가 등록되지 않아 FISIS 수집을 건너뜁니다.', 'warning'); return
    cd, msg, res = call('companySearch', key, partDiv='H')
    if cd != '000':
        raise SystemExit(f'FISIS 회사 목록 오류 {cd}: {msg}')
    comp = pd.DataFrame(rows(res))
    comp['co'] = comp.finance_nm.map(match)
    comp.to_csv(os.path.join(B, 'raw', 'fisis_companies.csv'), index=False, encoding='utf-8-sig')
    tgt = comp.dropna(subset=['co']).drop_duplicates('co')
    note(f"FISIS 생명보험사 {len(comp)}곳 중 분석 대상 {len(tgt)}곳 대응"
         + (f" · 못 찾음: {sorted(set(KEYS[i][1] for i in range(len(KEYS))) - set(tgt.co))}" if len(tgt) < 22 else ''))
    out = []
    for r in tgt.itertuples():
        for ln, lnm in LISTS.items():
            for term in ('Q', 'H', 'Y'):
                cd, msg, res = call('statisticsInfoSearch', key, financeCd=r.finance_cd, listNo=ln, term=term,
                                    startBaseMm=START, endBaseMm=END)
                if cd == '000' and rows(res):
                    break
                time.sleep(0.2)
            else:
                print(f'  {r.co} {ln}: 자료 없음 ({cd} {msg})'); continue
            cols = {c['column_id']: c['column_nm'] for c in (res.get('description') or {}).get('column', []) or []} \
                if isinstance((res.get('description') or {}).get('column'), list) else {}
            for x in rows(res):
                for k, v in x.items():
                    if k in ('base_month', 'finance_cd', 'finance_nm', 'account_cd', 'account_nm'):
                        continue
                    out.append(dict(co=r.co, list_no=ln, list_nm=lnm, term=term, base_month=x.get('base_month'),
                                    account_cd=x.get('account_cd'), account_nm=x.get('account_nm'),
                                    column=cols.get(k, k), value=v))
            time.sleep(0.2)
        print(f'  {r.co}: 누적 {len(out):,}행')
    df = pd.DataFrame(out)
    df.to_csv(os.path.join(B, 'raw', 'fisis_stats.csv'), index=False, encoding='utf-8-sig')
    note(f"FISIS 통계 {len(df):,}행 저장 · 지표별 회사 수 " +
         ', '.join(f"{LISTS[k]} {v}" for k, v in df.groupby('list_no').co.nunique().items()))


if __name__ == '__main__':
    main()
