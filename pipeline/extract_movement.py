"""
0단계: 공시 원문 표 → CSM 변동표 자동 추출
입력 : DART 정기보고서 표 덤프(17_dart_all_tables.R 결과, 회사 폴더별 *_cells.csv.gz)
       + 금감원 FISIS CSM 잔액(data/fisis.csv, 표를 고르는 기준)
출력 : data/movement_auto.csv   자동 추출 결과(회사·연도별, 사용한 표 번호와 단위 포함)
       data/extract_report.csv  자동 추출값 vs 최종 변동표(movement.csv) 항목별 대조

작동 순서
1) 보고서마다 표를 행 단위(라벨, 숫자 목록)로 읽음
2) 'CSM 상각' 행으로 CSM 열 위치를 찾음 — 상각은 CSM 열과 합계 열에만 숫자가 있으므로
   표 형식(전환방법별 열 유무, 소계 열 유무)이 회사마다 달라도 같은 방법으로 찾을 수 있음
3) 기초·신계약·상각·이자부리·조정 추정치 변동·기말 행에서 CSM 값을 읽음
4) 한 보고서 안의 후보 표(포트폴리오별 표, 당기·전기 표, 유배당·무배당 표) 중
   기초·기말 합이 FISIS 잔액과 맞는 조합과 단위(백만원·천원·원)를 고름
5) 최종 변동표와 항목별로 대조해 일치 여부를 기록
실행: CSM_DUMP=<덤프 폴더> python pipeline/extract_movement.py
"""
import glob, itertools, os, re, sys
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
import seg
SHORT = seg.short_map()
from periods import label, report_period, order_key, prev_year_end, wanted

DUMP = os.environ.get('CSM_DUMP', 'output/all_tables')
D = seg.DATA

# 행 라벨 정규식 — 회사마다 다른 표현을 모음
REL = (r'상각|제공된 서비스에 대해 인식한|제공된 서비스 관련|서비스를 제공한|당기인식분|인식한 보험계약마진|당기손익으로 인식[한된] 보험계약마진|서비스 제공에 따른 보험계약마진'
       r'|제공한 서비스|보험계약마진의? ?인식|이전을 반영')
NEW = r'최초로? ?인식|신계약|처음 ?인식'
EST = (r'조정하는 추정|조정 추정치|보험계약마진 추정의 변경|추정치의 변동분|보험계약마진을 조정|보험계약마진에 반영된 추정치'
       r'|변경하는 추정치|신계약 외 변동|보험계약마진\s?금액을 조정하는|변동\s?-\s?신계약 외')
REL2 = r'당기서비스와 관련된 변동|현행서비스와 관련된 변동'
INT = r'보험금융|금융손익|금융비용|이자|부리'
MOVE_ROW = r'변동|증가|감소|인식|현금흐름|상각|조정|금융'
TRANSFER = r'계약\s?이전|합병|사업\s?양수|사업\s?양도|사업결합'
LOSS_IN_CSM = r'손실부담계약의? ?(인식|환입)'   # 손보: 엠지손보 계약이전(2025), 캐롯 → 한화손보 합병(2025)   # 기초·기말 바로 아래에 오는 변동 항목 행
SCALES = (1, 1e-3, 1e-6, 100)          # 표 단위 → 백만원 (백만원·천원·원·억원)
TOL = (0.0005, 0.003)                  # FISIS 잔액 허용오차: 먼저 0.05%, 안 되면 0.3% (최소 3억)


def num(s):
    s = str(s).strip().replace('−', '-').replace('△', '-').replace('▲', '-')
    if s in ('-', '—', '–', '－'):
        return 0.0
    if re.fullmatch(r'\(?-?[\d,]+(\.\d+)?\)?', s):
        v = float(s.replace(',', '').replace('(', '').replace(')', ''))
        return -v if s.startswith('(') else v
    return None


def table_rows(g):
    """표 셀 → [(라벨, [숫자...]), ...]  (행 병합 때문에 열 번호가 밀리므로 숫자는 순서만 사용)"""
    out = []
    for _, r in g.sort_values(['row', 'col']).groupby('row', sort=True):
        cells = [v for v in r.value if v != '']
        n = [num(v) for v in cells]
        out.append((re.sub(r'\s+', ' ', ' '.join(c for c, x in zip(cells, n) if x is None)), [x for x in n if x is not None]))
    return out


def head_text(R):
    """표 머리글: 첫 숫자 행 앞까지의 라벨"""
    out = []
    for l, n in R[:20]:                    # XBRL형 표(2025년 이후 일부 회사)는 머리글이 10줄을 넘음
        if n and len(n) >= 3:
            break
        out.append(l)
    return ' '.join(out)


def cols_by_release(R):
    """방법 1 — 'CSM 상각' 행: 상각은 CSM 열과 합계 열에만 숫자가 있으므로 0이 아닌 열이 CSM 열"""
    head = head_text(R)
    trans = re.search('소급|공정가치', head)
    for lab, n in R:
        if not re.search(REL, lab) or len(n) < 4 or n[-1] == 0:
            continue
        L = len(n)
        cols = [j for j in range(L - 1) if abs(n[j]) > 0]
        sub = []
        if cols and abs(sum(n[j] for j in cols) - n[-1]) > 2:               # 소계 열이 있으면 이중 합산 제거
            sub = [j for j in cols if abs(n[j] - n[-1]) <= 2]
            if sub and len(cols) > 1:
                cols = [j for j in cols if j not in sub]
        if not cols or abs(sum(n[j] for j in cols) - n[-1]) > 2:
            continue
        post = None                                                        # 전환방법별 열이 있는 표: '그 밖의 계약' 열
        if trans:
            post = [sub[0] - 1] if sub else ([cols[-1]] if len(cols) >= 2 else None)
        return L, cols, post, 'release'
    return None


def cols_by_header(R):
    """방법 2 — 머리글 구조: [현재가치, 위험조정, CSM(전환방법별 3열 [+소계]), 합계]가 포트폴리오 수만큼 반복.
    마감 행에서 '현재가치+위험조정+CSM=합계' 항등식이 모든 묶음에서 성립할 때만 채택"""
    head = head_text(R)
    trans = bool(re.search('소급|공정가치', head)); sub = trans and bool(re.search(r'소\s?계', head))
    gs = 7 if sub else (6 if trans else 4)
    closes = [n for l, n in R if '기말' in l and len(n) >= gs]
    for n in closes:
        L = len(n)
        for extra in (0, 1):                                               # 맨 끝 전체 합계 열 유무
            if (L - extra) % gs:
                continue
            g = (L - extra) // gs
            B = [gs * k for k in range(g)]                                 # 묶음별 첫 열
            if sub:     # [현재가치, 위험조정, 수정소급, 공정가치, 그 밖, CSM 소계, 합계]
                cols = [b + 5 for b in B]; post = [b + 4 for b in B]
                ok = all(abs(sum(n[b + 2:b + 5]) - n[b + 5]) <= 3 and abs(n[b] + n[b + 1] + n[b + 5] - n[b + 6]) <= 3 for b in B)
            elif trans:  # [현재가치, 위험조정, 수정소급, 공정가치, 그 밖, 합계]
                cols = [b + j for b in B for j in (2, 3, 4)]; post = [b + 4 for b in B]
                ok = all(abs(sum(n[b:b + 5]) - n[b + 5]) <= 5 for b in B)
            else:        # [현재가치, 위험조정, CSM, 합계]
                cols = [b + 2 for b in B]; post = None
                ok = all(abs(sum(n[b:b + 3]) - n[b + 3]) <= 3 for b in B)
            if ok and abs(sum(n[j] for j in cols)) > 0:
                return L, cols, post, 'header'
    return None


def cols_csm_only(R):
    """방법 3 — CSM만 전환방법별로 나눈 표: [수정소급, 공정가치, 그 밖(, 합계)]"""
    head = head_text(R)
    if not re.search('소급|공정가치', head) or re.search('현재가치|위험조정', head):
        return None
    for l, n in R:
        if '기말' in l and len(n) == 6 and seg.SEG == 'nonlife' and re.search(r'발행한 보험계약.*보유\s?재보험', head):
            return 6, [0, 1, 2], [2], 'csm-only'          # [발행 3열 | 보유 재보험 3열] — 발행한 계약 쪽만
        if '기말' in l and len(n) in (3, 4) and abs(sum(n[:3])) > 0:
            if len(n) == 4 and abs(sum(n[:3]) - n[3]) > 3:
                return None
            if (len(n) == 3 and '수정소급' not in head and re.search(r'합\s*계', head)
                    and abs(n[0] + n[1] - n[2]) <= 3):      # [공정가치, 그 밖, 합계] — 수정소급 열이 없는 회사(KB손보)
                return 3, [0, 1], [1], 'csm-only'
            return len(n), [0, 1, 2], [2], 'csm-only'
    return None


def movement(R):
    """표 1개에서 CSM 변동 항목을 읽음. CSM 표가 아니면 None"""
    side = False
    if seg.SEG == 'nonlife':               # 손보: 한 표에 '보험계약' 다음 '출재보험계약' 구간이 이어지는 표(현대해상 전환방법별 CSM 표) → 출재 구간 앞에서 자름
        cut = next((i for i, (l, m) in enumerate(R) if i > 2 and not m and re.match(r'(출재|보유\s?재보험|재보험)', l.strip())), None)
        if cut:
            R = R[:cut]
        # 소제목 없이 같은 항목이 다시 시작되는 둘째 구간(삼성화재 전환방법별 표의 재보험 구간) 앞에서 자름
        opens = [i for i, (l, m) in enumerate(R) if m and re.match(r'(기초|전기말)\s?보험계약마진', l.strip())]
        if len(opens) >= 2 and opens[1] > opens[0] + 3:
            R = R[:opens[1]]
            while R and not R[-1][1]:                   # 둘째 구간의 소제목(예: '순재보험계약자산')도 함께 뺌
                R = R[:-1]
        h = head_text(R)                   # XBRL형 '발행한 보험계약 | 보유 재보험계약'을 한 행에 나란히 둔 표 → 발행한 계약 쪽만
        side = bool(re.search(r'발행한 보험계약.*보유\s?재보험', h))
    text = ' '.join(l for l, _ in R)
    if '보험계약마진' not in text or ('재보험' in text[:300] and not side):
        return None
    lay = cols_by_release(R)
    if lay is None or (lay[2] is None and re.search('소급|공정가치', head_text(R))):
        # 상각 행에서 0인 전환방법 열이 빠지면 CSM 일부와 전환이후 열을 놓침 → 머리글 구조로 다시 판단
        lay = cols_by_header(R) or lay or cols_csm_only(R)
    elif lay[3] == 'release' and re.search('소급|공정가치', head_text(R)) and seg.SEG == 'nonlife':
        # 손보: 상각 행에 '그 밖의 계약' 금액이 없으면(하나손보 유배당연금저축 표) 상각 행으로 정한 '그 밖' 열이 공정가치 열이 됨
        # → 머리글 구조([현재가치, 위험조정, 수정소급, 공정가치, 그 밖(, 소계), 합계])가 확인되면 그 열 위치를 씀
        lay = cols_by_header(R) or lay
    if not lay:
        return None
    L, cols, post, how = lay
    csm = lambda m: sum(m[j] for j in cols)
    pst = lambda m: sum(m[j] for j in post) if post else None

    def pick(key):
        """기초·기말 행 후보. 순부채(부채+자산) 행을 먼저 — 자산 쪽 계약에도 CSM이 있으므로 순액이 전체 CSM"""
        vals = []
        for i, (l, _) in enumerate(R):
            if key not in l:
                continue
            for l2, m2 in R[i:i + 4]:                                     # '기초' 다음 줄의 '순부채' 행 등
                if len(m2) == L and (l2 == l or re.search('부채|순|잔액|합계', l2)):
                    if l2 != l and key not in l2 and re.search(MOVE_ROW, l2):  # XBRL형 표는 모든 행 이름에 '보험계약부채(자산)'이 붙음 → 변동 행 제외
                        continue
                    lab2 = l2 if key in l2 else key + ' ' + l2
                    if ('자산' in lab2 and '부채' not in lab2) or '자산인' in lab2:   # 자산인 계약만의 행(순액·부채 행의 일부)
                        continue
                    vals.append((0 if '순' in lab2 else 1, csm(m2), pst(m2), lab2))
        net = next(((v, p) for _, v, p, lb in vals                        # 순액(부채 − 자산) 행: '순부채', '보험계약부채(자산)'
                    if ('순' in lb or re.search(r'부채\s?\(자산\)|자산\s?및\s?부채|부채\s?및\s?자산', lb)) and not re.search('자산인|부채인', lb)), None)
        return list(dict.fromkeys((v, p) for _, v, p, _ in sorted(vals, key=lambda x: x[0]))), net
    (op, op_net), (cl, cl_net) = pick('기초'), pick('기말')
    if not op and seg.SEG == 'nonlife' and how in ('csm-only', 'release'):   # '전기말 보험계약마진'으로 기초를 적은 전환방법별 표(현대해상)
        (op, op_net) = pick('전기말')
        cl = [(v, p) for v, p in cl if (v, p) not in op]
    if not op or not cl:
        return None
    first = lambda pat, bad=None, pos=False, nz=False: next((m for l, m in R if re.search(pat, l) and len(m) == L
                                                             and not (bad and re.search(bad, l)) and (not pos or csm(m) > 0)
                                                             and (not nz or csm(m) != 0)), None)
    sec, R2 = '', []                   # 소제목 행(숫자 없음)을 아래 행 이름 앞에 붙인 목록 — '신계약효과' 아래 '손실부담계약을 제외한 집합' 등
    for l, m in R:
        if not m: sec = l
        R2.append((f'{sec} {l}' if m and sec else l, m))
    first2 = lambda pat, bad=None, pos=False: next((m for l, m in R2 if re.search(pat, l) and len(m) == L and not (bad and re.search(bad, l))
                                                    and (not pos or csm(m) > 0)), None)
    nw = first(NEW, '신계약 외', pos=True) or first(NEW, '신계약 외')      # 신계약이 없는 폐쇄형 포트폴리오는 0
    if nw is None:
        nw = first2(NEW, '신계약 외|손실부담계약집합', pos=True)
    rl = first(REL, '위험조정'); es = first(EST, '조정하지|미조정|손실')
    if rl is None:                     # CSM만 있는 표: '당기서비스와 관련된 변동' 행이 곧 CSM 상각
        rl = first(REL2, '위험조정') if how == 'csm-only' else None
    if es is None and nw is not None:  # 합계 행 없이 요인별(해지율·위험률 등)로만 공시한 표 → '미래서비스 소계 − 신계약'
        i = next(i for i, (l, m) in enumerate(R) if m is nw)
        sub = next((m for l, m in R[i + 1:i + 3] if re.fullmatch(r'소\s?계|계', l.strip()) and len(m) == L), None)
        es = [a - b for a, b in zip(sub, nw)] if sub else None
    it = first(INT, '기타포괄|재보험', nz=True) or first(INT, '기타포괄|재보험')   # CSM 열이 0이 아닌 행 우선
    if it is None:                     # '보험금융손익'이 소제목 행이고 숫자는 아래 '당기손익 인식분' 행에 있는 표
        it = next((m for l, m in R2 if re.search(INT, l) and len(m) == L and not re.search('기타포괄|재보험', l) and csm(m) != 0), None)
    tr = [m for l, m in R if re.search(TRANSFER, l) and len(m) == L and csm(m) != 0]   # 계약이전·합병으로 넘어오거나 나간 CSM
    lc = [m for l, m in R if re.search(LOSS_IN_CSM, l) and len(m) == L and csm(m) != 0]  # 손실부담계약 인식·환입을 CSM 열에 적은 표(엠지손보)
    res = dict(open=op, close=cl, open_net=op_net, close_net=cl_net, transfer=sum(csm(m) for m in tr) if tr else None,
               other=sum(csm(m) for m in lc) if lc else None,
               new=csm(nw) if nw else None, release=csm(rl) if rl else None,
               adjust=csm(es) if es else None, adjust_post=pst(es) if es else None,
               interest=csm(it) if it is not None else None, ncol=L, how=how)
    if res['release'] is not None and res['release'] > 0 and res['new'] is not None and res['new'] < 0:
        # 변동 항목을 손익 효과 부호(상각 +, 신계약 −)로 적은 표(엠지손보 등) → CSM 증감 부호로 바꿈
        for k in ('new', 'release', 'adjust', 'adjust_post', 'interest', 'transfer', 'other'):
            if res[k] is not None:
                res[k] = -res[k]
        res['how'] += '·부호반전'
    return res


def load_cells(src):
    """보고서 1개의 표 셀. src = 덤프 파일 경로 또는 스냅숏에서 잘라낸 DataFrame"""
    c = pd.read_csv(src, dtype=str).fillna('') if isinstance(src, str) else src
    return c[c.value != ''].copy()


def candidates(src):
    """보고서 1개 → CSM 변동표 후보 목록 (같은 숫자의 중복 표는 하나만)"""
    c = load_cells(src)
    c[['row', 'col']] = c[['row', 'col']].astype(int)
    hit = c.groupby(['doc', 'table_no']).value.apply(lambda s: '보험계약마진' in ''.join(s) and '기말' in ''.join(s))
    out, seen = [], {}
    for (doc, tn) in hit[hit].index:
        m = movement(table_rows(c[(c.doc == doc) & (c.table_no == tn)]))
        if not m:
            continue
        key = (tuple(v for v, _ in m['open']), tuple(v for v, _ in m['close']), m['new'])
        if key in seen:                     # 같은 숫자의 표(연결·별도 반복 등)는 하나만, 전환방법별 열이 있는 쪽 우선
            i = seen[key]
            if out[i][1]['close'][0][1] is None and m['close'][0][1] is not None:
                out[i] = (int(tn), m)
            continue
        seen[key] = len(out); out.append((int(tn), m))
    return out


def _tsum(S, sc, key='transfer'):
    v = [x[1].get(key) for x in S if x[1].get(key) is not None]
    return sum(v) * sc if v else None


def _sum(S, key, sc):
    v = [x[1][key] for x in S]
    return None if None in v else sum(v) * sc


def choose(C, t_open, t_close, band_ok=True):
    """후보 표 조합 × 단위 × (기초·기말 행 선택) 중 FISIS 잔액과 가장 가까운 조합.
    허용오차 안의 조합이 여럿이면 오차가 가장 작은 것, 같으면 표 수가 적은 것"""
    for tol in TOL:
        lim = lambda t: max(300, abs(t) * tol)
        best = None
        for sc in SCALES:
            for k in range(1, min(5, len(C)) + 1):
                for S in itertools.combinations(C, k):
                    for vo in itertools.product(*[x[1]['open'] for x in S]):
                        o = sum(v for v, _ in vo) * sc
                        if t_open is not None and abs(o - t_open) > lim(t_open):
                            continue
                        for vc in itertools.product(*[x[1]['close'] for x in S]):
                            c = sum(v for v, _ in vc) * sc
                            if t_close is not None and abs(c - t_close) > lim(t_close):
                                continue
                            err = (abs(o - t_open) if t_open is not None else 0) + (abs(c - t_close) if t_close is not None else 0)
                            # 손보: 반올림 차이(10백만원=0.1억 미만)는 같은 것으로 보고, 같은 잔액의 표 중 변동 항목이 다 있는 표 우선
                            # (삼성화재: 변동표 1067과 기말이 1백만원 다른 전환방법별 CSM 표 1087 — 신계약·조정 행이 없음)
                            miss = sum(_sum(S, f, 1) is None for f in ITEMS) if seg.SEG == 'nonlife' else 0
                            e_key = round(err / 10) if seg.SEG == 'nonlife' else round(err)
                            if best and (best[0], best[1], best[4]) <= (e_key, k, miss):
                                continue
                            posts = [v for _, v in vc]
                            best = (e_key, k, dict(
                                err=err, tables='+'.join(str(x[0]) for x in S), how='+'.join(sorted({x[1]['how'] for x in S})),
                                scale=sc, tol=tol, open=o, close=c,
                                new=_sum(S, 'new', sc), release=_sum(S, 'release', sc), adjust=_sum(S, 'adjust', sc),
                                interest=_sum(S, 'interest', sc), adjust_post=_sum(S, 'adjust_post', sc),
                                transfer=_tsum(S, sc), other=_tsum(S, sc, 'other'), close_post=sum(posts) * sc if None not in posts else None,
                                t_open_used=t_open is not None), S, miss)
        if best:
            return to_net(best[2], best[3]) if seg.SEG == 'nonlife' else best[2]
    return None


def to_net(res, S):
    """손보: 금감원 통계(SI152)의 CSM은 '부채인 보험계약'의 잔여보장요소 CSM만이라, 표를 고를 때는 부채 행으로 맞추고
    화면 값은 같은 표의 순액(부채 − 자산인 계약) 행으로 바꿈 — 변동 항목(신계약·상각 등)은 순액 기준이라 이렇게 해야 항등식이 맞음.
    부채 행으로 맞춘 값은 open_liab·close_liab로 남겨 R2(금감원 잔액 대조)에 씀"""
    res['open_liab'], res['close_liab'] = (res['open'] if res.get('t_open_used') else None), res['close']
    if all(x[1]['open_net'] is not None and x[1]['close_net'] is not None for x in S):
        sc = res['scale']
        res['open'] = sum(x[1]['open_net'][0] for x in S) * sc
        res['close'] = sum(x[1]['close_net'][0] for x in S) * sc
        p = [x[1]['close_net'][1] for x in S]
        res['close_post'] = sum(p) * sc if None not in p else None
    return res


BAND = 0.15


def unanchored(C, prem=None):
    """손보 보조 기준 2: 금감원 CSM이 0인 회사(장기 계약이 모두 '자산인 계약' 쪽이라 부채 쪽 CSM이 없음, 예: 악사손보).
    같은 보고서의 당기 표와 전기 표는 '당기 기초 = 전기 기말'로 이어지므로, 그런 짝이 정확히 하나일 때 당기 표를 고름.
    단위는 CSM이 10억~30조 원 범위에 드는 것이 하나뿐일 때만(아니면 고르지 않음)"""
    net = lambda x, k: (x[1][k + '_net'] or x[1][k][0])[0]
    cur = [a for a in C if any(b is not a and net(a, 'open') != 0 and abs(net(a, 'open') - net(b, 'close')) < 0.5 for b in C)]
    if not cur and len(C) == 1:                       # 첫해(전기 표 없음)이고 CSM 표가 하나뿐
        cur = list(C)
    if len(cur) != 1:
        return None
    a = cur[0]
    # 단위: 장기 보유보험료(FISIS, 억)가 있으면 CSM이 그 0.5%~10배 범위, 없으면 10억~30조 원 범위에 드는 단위가 하나뿐일 때만
    lo, hi = (prem * 100 * 0.005, prem * 100 * 10) if prem and prem > 0 else (1e3, 3e7)   # 억 → 백만원
    scs = [sc for sc in SCALES if lo <= abs(net(a, 'close')) * sc <= hi]
    if len(scs) != 1:
        return None
    sc = scs[0]; m = a[1]
    p = (m['close_net'] or m['close'][0])[1]
    return dict(err=None, tables=str(a[0]), how=m['how'] + '·당기전기연결', scale=sc, tol=None,
                open=net(a, 'open') * sc, close=net(a, 'close') * sc, open_liab=None, close_liab=None,
                new=None if m['new'] is None else m['new'] * sc, release=None if m['release'] is None else m['release'] * sc,
                adjust=None if m['adjust'] is None else m['adjust'] * sc, interest=None if m['interest'] is None else m['interest'] * sc,
                adjust_post=None if m['adjust_post'] is None else m['adjust_post'] * sc, close_post=None if p is None else p * sc,
                transfer=_tsum([a], sc), other=_tsum([a], sc, 'other'))


def band(C, t_open, t_close, prev=None):
    """손보 보조 기준: 부채·자산을 나누지 않고 순액만 공시한 표는 금감원 값(부채인 계약만)과 정확히 맞을 수 없음.
    순액 CSM ≥ 부채인 계약의 CSM(자산인 계약의 CSM이 더해짐)이므로, 기초·기말이 모두 금감원 값 이상 +15% 이내인 조합만 후보.
    전기 기말(이미 고른 순액, prev)이 있으면 기초가 그 값과 정확히 같은 조합만 남김 — 유배당·무배당 표가 나뉜 회사에서
    한쪽 표만 골라도 범위 안에 들 수 있기 때문(독립 검토가 찾은 KB 2025 무배당 표만 선택한 오류).
    그중 초과폭이 가장 작은 것(같으면 표 수가 적은 것). 기준 값이 하나도 없으면 고르지 않음"""
    if t_open is None and t_close is None:
        return None
    ok = lambda v, t: t is None or (t - max(300, abs(t) * 0.003) <= v <= t * (1 + BAND))
    cont = lambda o: prev is None or abs(o - prev) <= max(50, abs(prev) * 0.0005)
    best = None
    for sc in SCALES:
        for k in range(1, min(4, len(C)) + 1):
            for S in itertools.combinations(C, k):
                nets_o = [x[1]['open_net'] or x[1]['open'][0] for x in S]
                nets_c = [x[1]['close_net'] or x[1]['close'][0] for x in S]
                o = sum(v for v, _ in nets_o) * sc; c = sum(v for v, _ in nets_c) * sc
                if not (ok(o, t_open) and ok(c, t_close) and cont(o)):
                    continue
                ex = sum((v - t) / t for v, t in ((o, t_open), (c, t_close)) if t)
                if best and (best[0], best[1]) <= (round(ex, 4), k):
                    continue
                p = [q for _, q in nets_c]
                best = (round(ex, 4), k, dict(
                    err=None, excess=ex, tables='+'.join(str(x[0]) for x in S), how='+'.join(sorted({x[1]['how'] for x in S})) + '·순액범위',
                    scale=sc, tol=BAND, open=o, close=c, open_liab=None, close_liab=None,
                    new=_sum(S, 'new', sc), release=_sum(S, 'release', sc), adjust=_sum(S, 'adjust', sc),
                    interest=_sum(S, 'interest', sc), adjust_post=_sum(S, 'adjust_post', sc),
                    transfer=_tsum(S, sc), other=_tsum(S, sc, 'other'), close_post=sum(p) * sc if None not in p else None))
    return best[2] if best else None


def post_split(C, res):
    """선택한 표에 전환방법별 열이 없으면(예: 4열 표), 같은 보고서에서 기말 CSM 합이 같은 전환방법별 표를 찾아
    '그 밖의 계약(전환일 이후 + 완전소급)' 값을 가져옴"""
    sc = res['scale']
    P = [x for x in C if x[1]['close'][0][1] is not None]
    for k in range(1, min(4, len(P)) + 1):
        for S in itertools.combinations(P, k):
            for vc in itertools.product(*[x[1]['close'] for x in S]):
                if abs(sum(v for v, _ in vc) * sc - res['close']) <= max(300, abs(res['close']) * 0.0005):
                    res['close_post'] = sum(p for _, p in vc) * sc
                    res['adjust_post'] = _sum(S, 'adjust_post', sc)
                    res['post_tables'] = '+'.join(str(x[0]) for x in S)
                    return res
    return res


ITEMS = ('new', 'release', 'interest', 'adjust')


def fill_residual(res):
    """표에 행이 없는 항목이 하나뿐이면 '기말 − 기초 − 나머지 항목'으로 채우고 표시(잔차에는 기타 변동이 섞임)"""
    miss = [k for k in ITEMS if res.get(k) is None]
    if len(miss) == 1:
        k = miss[0]
        res[k] = res['close'] - res['open'] - sum(res[j] for j in ITEMS if j != k)
        res['filled'] = {'new': '신계약', 'release': '상각', 'interest': '이자부리', 'adjust': '조정'}[k] + '=잔차'
    return res


SNAP = os.path.join(seg.RAW, 'csm_tables.csv.gz')


def reports():
    """{(약칭, 보고서 기간): 입력}. 전체 덤프(CSM_DUMP)가 있으면 덤프, 없으면 저장소의 스냅숏(raw/csm_tables.csv.gz)"""
    out = {}
    for f in glob.glob(f'{DUMP}/*/*_cells.csv.gz'):
        co = os.path.basename(os.path.dirname(f))
        co = co.replace('#U', '\\u').encode().decode('unicode_escape') if '#U' in co else co
        out[(SHORT.get(co, co), os.path.basename(f).split('_')[0])] = f
    if out:
        return out
    if os.path.exists(SNAP):
        s = pd.read_csv(SNAP, dtype=str).fillna('')
        for (co, per), g in s.groupby(['co', 'period']):
            out[(co, per)] = g
    return out


def snapshot():
    """전체 덤프(약 100MB)에서 '보험계약마진'과 '기말'이 들어 있는 표만 잘라 저장소용 스냅숏으로 저장"""
    keep = []
    for f in sorted(glob.glob(f'{DUMP}/*/*_cells.csv.gz')):
        c = pd.read_csv(f, dtype=str).fillna('')
        co = os.path.basename(os.path.dirname(f))
        co = SHORT.get(co.replace('#U', '\\u').encode().decode('unicode_escape') if '#U' in co else co, co)
        txt = c.groupby(['doc', 'table_no']).value.apply(''.join)
        ids = txt[txt.str.contains('보험계약마진') & txt.str.contains('기말')].index
        c = c.set_index(['doc', 'table_no']).loc[ids].reset_index()
        keep.append(c.assign(co=co, period=os.path.basename(f).split('_')[0])[['co', 'period', 'rcept_no', 'doc', 'table_no', 'row', 'col', 'value']])
    os.makedirs(os.path.dirname(SNAP), exist_ok=True)
    pd.concat(keep).to_csv(SNAP, index=False, encoding='utf-8', compression='gzip')
    print('스냅숏 저장:', SNAP, f'{os.path.getsize(SNAP) / 1e6:.1f}MB')


def extract_one(R, cache, co, y, t_open, t_close, prem=None, prev=None):
    """회사·기간 하나: 해당 보고서의 후보 표 중 기준 잔액(백만원)에 맞는 조합 → 결과 행"""
    order = [report_period(y)] if len(y) > 4 else [f'{y}.12', f'{int(y) + 1}.12']   # 연간은 당해 보고서, 없으면 다음 해 보고서의 전기 비교
    for rep in order:
        if (co, rep) not in R:
            continue
        if rep not in cache.setdefault(co, {}):
            cache[co][rep] = candidates(R[(co, rep)])
        zero = t_open in (None, 0) and t_close in (None, 0) and 0 in (t_open, t_close)
        if zero and rep != order[0]:                  # 기준 없이 고르는 방식은 그 기간의 보고서(당기 표)에서만
            continue
        res = unanchored(cache[co][rep], prem) if zero else choose(cache[co][rep], t_open, t_close)
        if res is None and not zero and seg.SEG == 'nonlife' and t_close is not None and rep == order[0]:
            res = choose(cache[co][rep], None, t_close, band_ok=False)   # 기초가 재작성된 보고서: 기말만 금감원 값에 맞춤(R3가 재작성을 표시)
            if res:
                res['how'] += '·기말만 대조'
                res['open_liab'] = None                    # 기초는 금감원 값과 맞추지 않았음(표의 순액 기초를 씀)
            else:
                res = band(cache[co][rep], t_open, t_close, prev)
        if res:
            if res['close_post'] is None:
                res = post_split(cache[co][rep], res)
            res = fill_residual(res)
            row = dict(company=co, year=y, report=rep)
            row.update({k: (round(v / 100, 1) if isinstance(v, float) and k not in ('scale', 'tol') else v) for k, v in res.items()})
            return row
    return dict(company=co, year=y, report=None)


def main():
    if '--snapshot' in sys.argv:
        return snapshot()
    R = reports()
    if not R:
        print('원문 표(CSM_DUMP 또는 raw/csm_tables.csv.gz)가 없어 추출을 건너뜁니다 (기존 movement.csv 유지).')
        return
    fz = pd.read_csv(f'{D}/fisis.csv', dtype={'year': str})
    cache = {}
    m100 = lambda v: None if v is None or pd.isna(v) else v * 100                     # 억 → 백만원
    # 1) 금감원 잔액이 있는 회사·기간: 기초·기말을 금감원 값에 맞춰 표 선택
    rows, got = [], {}                     # got: 이미 고른 기말(백만원) — 손보 순액 범위 대조에서 다음 기간 기초와 이어지는지 확인
    for r in fz.itertuples():
        if pd.isna(r.fisis_open) and pd.isna(r.fisis_close):           # 기준 잔액이 하나도 없으면 고르지 않음
            rows.append(dict(company=r.company, year=r.year, report=None)); continue
        row = extract_one(R, cache, r.company, r.year, m100(r.fisis_open), m100(r.fisis_close), getattr(r, 'prem_long', None),
                          prev=got.get((r.company, prev_year_end(r.year))))
        if row.get('close') is not None:
            got[(r.company, r.year)] = row['close'] * 100
        rows.append(row)
    auto = pd.DataFrame(rows)
    rep = compare(auto)
    final = assemble(auto)
    # 2) 그 뒤에 새로 나온 공시(분기 자동 업데이트로 받은 보고서): 금감원 값이 아직 없으므로
    #    기초를 '전년 연말 최종 기말(이미 검증된 값)'에 맞춰 표 선택
    known = set(zip(fz.company, fz.year))
    new = sorted({(co, label(p)) for co, p in R if (co, label(p)) not in known and wanted(label(p), set(fz.year))},
                 key=lambda k: (k[0], order_key(k[1])))
    if new:
        closes = {(r.company, r.year): r.close for r in final.itertuples()}
        extra = []
        held = []
        for co, y in new:
            t_open = closes.get((co, prev_year_end(y)))
            if t_open is None:                      # 기초를 맞출 전년 기말이 없으면 표를 고를 근거가 없음 → 올리지 않고 보류
                held.append(f'{co} {y}'); continue
            row = extract_one(R, cache, co, y, m100(t_open), None)
            if row.get('close') is None:
                held.append(f'{co} {y}(추출 실패)'); continue
            extra.append(row)
            if row.get('close') is not None:
                closes[(co, y)] = row['close']
        print('새 공시에서 추가한 기간:', ', '.join(f"{r['company']} {r['year']}" for r in extra) or '없음')
        if held:
            print('보류(화면에 올리지 않음):', ', '.join(held))
        pd.DataFrame({'held': held}).to_csv(f'{D}/held_periods.csv', index=False, encoding='utf-8-sig')
        if extra:
            auto = pd.concat([auto, pd.DataFrame(extra)], ignore_index=True)
            final = assemble(auto)
    auto.to_csv(f'{D}/movement_auto.csv', index=False, encoding='utf-8-sig')
    final.to_csv(f'{D}/movement.csv', index=False, encoding='utf-8-sig')
    print(f"자동 추출 {auto.tables.notna().sum()}/{len(auto)}건 | 수작업 판독값과 일치 {(rep.status == '일치').sum()}건 · "
          f"자동이 새로 찾은 값만 다름 {(rep.status == '일치(추가값)').sum()}건 · 차이 {(rep.status == '차이').sum()}건 · 실패 {(rep.status == '자동추출 실패').sum()}건 "
          f"| 보정 적용 {final.source.str.contains('보정|수작업').sum()}건")


def assemble(auto):
    """최종 변동표 = 자동 추출값 + 보정 파일(overrides.csv)에 사유와 함께 적은 항목만 수작업 판독값으로 대체"""
    man = pd.read_csv(f'{D}/movement_manual.csv', dtype={'year': str}).set_index(['company', 'year'])
    ov = pd.read_csv(f'{D}/overrides.csv', dtype={'year': str})
    out = []
    for a in auto.itertuples():
        d = {f: getattr(a, f, None) for f in FIELDS}
        src = f'{a.report} 보고서 표 {a.tables}' if pd.notna(a.tables) else '자동 추출 실패'
        how = str(getattr(a, 'how', ''))
        if '순액범위' in how:
            src += ' · 순액 범위 대조'
        elif '당기전기연결' in how:
            src += ' · 당기·전기 표 연결'
        if pd.notna(getattr(a, 'post_tables', None)):
            src += f' (전환구분 표 {a.post_tables})'
        if pd.notna(getattr(a, 'filled', None)):
            src += f' · {a.filled}'
        for o in ov[(ov.company == a.company) & (ov.year == a.year)].itertuples():
            fs = FIELDS if o.fields == '*' else o.fields.split(';')
            for f in fs:
                d[f] = man.loc[(a.company, a.year), f]
            src = ('수작업 판독' if o.fields == '*' else src + ' · 수작업 보정(' + ', '.join(fs) + ')') + ' — 보정 사유는 overrides.csv'
        liab = {f: getattr(a, f) for f in (('open_liab', 'close_liab', 'transfer', 'other') if seg.SEG == 'nonlife' else ())
                if hasattr(a, f) and pd.notna(getattr(a, f))}   # 손보만: 금감원 대조용 부채 행 값, 계약이전·합병, CSM 열의 손실부담 행   # 손보: 금감원 대조용 부채 행 값
        out.append(dict(company=a.company, year=a.year, **{f: (None if pd.isna(v) else v) for f, v in d.items()}, source=src, **liab))
    return pd.DataFrame(out)


FIELDS = ['open', 'new', 'release', 'interest', 'adjust', 'close', 'adjust_post', 'close_post']


def compare(auto):
    """자동 추출값 vs 수작업 판독값(분석 엑셀) — 항목별 차이(억 원). 0.5억 이내면 일치"""
    mv = pd.read_csv(f'{D}/movement_manual.csv', dtype={'year': str})
    m = mv.merge(auto, on=['company', 'year'], how='left', suffixes=('', '_auto'))
    out = []
    for r in m.itertuples():
        d = r._asdict()
        if pd.isna(d.get('tables')):
            out.append(dict(company=r.company, year=r.year, status='자동추출 실패', detail='')); continue
        diff, extra = [], []
        for f in FIELDS:
            a, b = d[f + '_auto'], d[f]
            if pd.isna(b) and pd.isna(a): continue
            if pd.isna(b):
                extra.append(f'{f}: 자동 {a}'); continue                     # 수작업에 없던 값을 자동이 찾음
            if pd.isna(a) or abs(a - b) > 0.5:
                diff.append(f'{f}: 자동 {a} / 수작업 {b}')
        st = '차이' if diff else ('일치(추가값)' if extra else '일치')
        out.append(dict(company=r.company, year=r.year, status=st, detail=' | '.join(diff + extra),
                        report=d['report'], tables=d['tables'], scale=d['scale'], fisis_err=d['err']))
    rep = pd.DataFrame(out, columns=['company', 'year', 'status', 'detail', 'report', 'tables', 'scale', 'fisis_err'])   # 수작업 판독값이 없는 업권(손보)은 빈 표
    rep.to_csv(f'{D}/extract_report.csv', index=False, encoding='utf-8-sig')
    return rep


if __name__ == '__main__':
    main()
