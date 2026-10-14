"""
숫자 가드레일: 문장(AI 설명 또는 규칙 기반 요약) 속 숫자를 모두 뽑아 원표 값과 대조
- 금액: '7조 5,537억 원', '−8,470억', '3,296억' → 억 원 단위로 환산
- 비율: '51%', '+4.6%' / 퍼센트포인트: '−6.27%p'
- 연도(2023 등)는 대조 대상에서 제외
원표에 없는 숫자가 하나라도 있으면 '수정 필요'로 표시 → 사람이 확인
"""
import re

NEG = '−-–'                     # 부호로 인정하는 문자(유니코드 마이너스·하이픈·대시)
NC = r'\u2212\-\u2013'            # 정규식 문자 클래스용
MONEY = re.compile(r'([+' + NC + r']?)\s*(?:(\d+(?:\.\d+)?)\s*조(?:\s*(\d[\d,]*)\s*억)?|(\d[\d,]*(?:\.\d+)?)\s*억)')
PP = re.compile(r'([+' + NC + r']?)(\d+(?:\.\d+)?)\s*%p')
PCT = re.compile(r'([+' + NC + r']?)(\d+(?:\.\d+)?)\s*%(?!p)')
COUNT = re.compile(r'(\d+)\s*(건|곳|개사|개)')


def _sign(s):
    return -1 if s and s in NEG else 1


def _dec(s):
    return len(s.split('.')[1]) if '.' in s else 0


def extract(text):
    """문장에서 (원문, 값, 종류, 허용오차)를 뽑음"""
    out, used = [], []
    for m in MONEY.finditer(text):
        sg = _sign(m.group(1))
        if m.group(2):                                   # 'n조 m억' 또는 'n.n조'
            jo = float(m.group(2)); eok = float((m.group(3) or '0').replace(',', ''))
            v = jo * 10000 + eok
            tol = 0.6 if m.group(3) else 0.5 * 10 ** (-max(_dec(m.group(2)), 1)) * 10000 + 0.6   # '2조'처럼 정수 조는 0.1조 정밀도로 간주(1.6조를 2조로 쓰면 오류)
        else:
            v = float(m.group(4).replace(',', '')); tol = max(0.6, 0.5 * 10 ** (-_dec(m.group(4))))
        out.append(dict(raw=m.group(0).strip(), value=sg * v, kind='억', tol=tol)); used.append(m.span())
    for m in PP.finditer(text):
        out.append(dict(raw=m.group(0), value=_sign(m.group(1)) * float(m.group(2)), kind='%p',
                        tol=0.5 * 10 ** (-_dec(m.group(2))) + 1e-9)); used.append(m.span())
    for m in PCT.finditer(text):
        if any(a <= m.start() < b for a, b in used):
            continue
        out.append(dict(raw=m.group(0), value=_sign(m.group(1)) * float(m.group(2)), kind='%',
                        tol=0.5 * 10 ** (-_dec(m.group(2))) + 1e-9))
    for m in COUNT.finditer(text):
        out.append(dict(raw=m.group(0), value=float(m.group(1)), kind='개수', tol=0))
    return out


def verify(text, facts):
    """facts: {'억': [...], '%': [...], '%p': [...], '개수': [...]} — 원표에서 계산한 허용 값 목록
    금액·비율은 부호까지 맞아야 하지만, 문장에 부호가 없으면(예: '3,296억 원 늘었습니다') 절댓값으로 대조"""
    nums = extract(text)
    bad = []
    for n in nums:
        pool = facts.get(n['kind'], [])
        explicit = n['raw'].lstrip()[:1] in '+' + NEG
        ok = any(abs((f if explicit else abs(f)) - (n['value'] if explicit else abs(n['value']))) <= n['tol'] for f in pool)
        if not ok:
            bad.append(n['raw'])
    return dict(ok=not bad, checked=len(nums), mismatches=bad)


def facts_from(rec, ae=None, extra=None):
    """회사·연도 레코드(web.json의 years[연도])와 예실차 값으로 허용 값 목록을 만듦"""
    e = [rec[k] for k in ('open', 'new', 'release', 'interest', 'adjust', 'close', 'adjust_post', 'close_post') if rec.get(k) is not None]
    e.append(rec['close'] - rec['open'])
    if rec.get('assump'):
        e += list(rec['assump'].values())
    p = [x * 100 for x in (rec.get('offset'), rec.get('post_share'), rec.get('growth'), rec.get('adj_post_share'),
                          rec.get('new_to_open')) if x is not None]
    pp = []
    if ae:
        p += [ae.get('exp'), ae.get('act')]; pp += [ae.get('ae')]
    f = {'억': e, '%': [x for x in p if x is not None], '%p': [x for x in pp if x is not None], '개수': []}
    for k, v in (extra or {}).items():
        f.setdefault(k, []).extend(v)
    return f
