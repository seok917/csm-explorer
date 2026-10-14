"""
R6 보조: 공시 원문(표 셀·표 앞 문단)에서 '산출 기준 변경' 신호 문구를 자동으로 찾음
대상: 예상보험금·예실차·손해율·보험계약마진 관련 표와 그 주석
"""
import pandas as pd, glob, re, os
DUMP = os.environ.get('CSM_DUMP', 'output/all_tables')   # 17_dart_all_tables.R 결과 폴더
OUT = os.path.join(os.path.dirname(__file__), '..', 'data')
from build_data import SHORT
TOPIC = re.compile(r'예상보험금|예실차|손해율|보험계약마진|계약서비스마진|위험보험료')
SIGNAL = re.compile(r'(?:제외하여 작성|제외하고 작성|제도\s?변경|산출\s?기준.{0,8}변경|변경된 기준|기준을 변경|재작성|포함하여 작성)')
rows = []
FILES = sorted(glob.glob(f'{DUMP}/*/*.12_*_cells.csv.gz'))
if not FILES:
    print('전체 원문 덤프(CSM_DUMP)가 없어 건너뜁니다 (저장소의 note_signals.csv 사용).'); raise SystemExit(0)
for f in FILES:
    c = pd.read_csv(f, dtype=str).fillna('')
    co = SHORT.get(c.company.iloc[0], c.company.iloc[0]); rep = c.period.iloc[0]
    t = pd.read_csv(f.replace('_cells.csv.gz', '_tables.csv'), dtype=str).fillna('')
    # 1) 표 안 셀
    hit = c[c.value.str.contains(SIGNAL)]
    for (doc, tn), g in hit.groupby(['doc', 'table_no']):
        tbl = ' '.join(c[(c.doc == doc) & (c.table_no == tn)].value.head(60))
        if not TOPIC.search(tbl): continue
        for v in g.value.unique():
            m = SIGNAL.search(v); i = m.start()
            rows.append(dict(company=co, report=rep, where=f'{doc}#표{tn}', phrase=m.group(0), text=v[max(0, i - 90): i + 60]))
    # 2) 표 앞 문단
    for _, r in t[t.context.str.contains(SIGNAL) & (t.context.str.contains(TOPIC) | t.preview.str.contains(TOPIC))].iterrows():
        m = SIGNAL.search(r.context); i = m.start()
        rows.append(dict(company=co, report=rep, where=f'{r.doc}#표{r.table_no} 앞 문단', phrase=m.group(0), text=r.context[max(0, i - 90): i + 60]))
df = pd.DataFrame(rows).drop_duplicates(['company', 'report', 'text'])
# 잡음 제거: IFRS17 최초 도입(2023)에 따른 일반 재작성, 미래 기준서(제1118호) 안내, 할인율 제도변경 설명
NOISE = re.compile(r'제\s?1117호의? (?:적용|도입|회계정책 변경효과|조정사항)|1117호 회계정책|제1118호|2023년 도입된|비교정보를 재작성하지 않|기타포괄손익누계액 변동원인|2022년 12월 31일로 종료되는')
df = df[df.text.str.len() >= 20]
df = df[~df.text.str.contains(NOISE)]
def kind(t):
    if re.search(r'오류', t): return '오류 수정 소급재작성'
    if re.search(r'회계정책|회계처리 변경|질의회신', t): return '회계정책 변경 소급재작성'
    return '산출·공시 기준 변경'
df['kind'] = df.text.map(kind)
df = df.sort_values(['company', 'report']).drop_duplicates(['company', 'report', 'kind', 'phrase'])
df.to_csv(f'{OUT}/note_signals.csv', index=False, encoding='utf-8-sig')
print(len(df)); print(df.groupby(['company', 'report']).size().to_string())
for _, r in df.iterrows(): print(f'[{r.company} {r.report}] {r.kind} :: {r.text}')
