"""기간 표기 규칙 (한 곳에서 관리)
보고서 기간 'YYYY.MM' → 화면 표기:  12 → 'YYYY'(연간) · 06 → 'YYYY 상반기' · 03 → 'YYYY 1분기' · 09 → 'YYYY 3분기'
반기·분기 변동표는 그해 1월 1일부터의 누적이므로, 기초는 항상 전년도 연말(=전년 연간 기말)과 같아야 함
"""
SUFFIX = {'12': '', '06': ' 상반기', '03': ' 1분기', '09': ' 3분기'}
ORDER_OF = {'': 4, ' 1분기': 1, ' 상반기': 2, ' 3분기': 3}


def label(report_period):
    y, m = report_period.split('.')
    return y + SUFFIX[m]


def report_period(lbl):
    y, rest = lbl[:4], lbl[4:]
    return f"{y}.{next(m for m, s in SUFFIX.items() if s == rest)}"


def order_key(lbl):
    """정렬용: 같은 해 안에서 1분기 < 상반기 < 3분기 < 연간"""
    return int(lbl[:4]) * 10 + ORDER_OF[lbl[4:]]


def prev_year_end(lbl):
    """이 기간의 기초와 같아야 하는 전년도 연간 기간"""
    return str(int(lbl[:4]) - 1)


def sort_labels(labels):
    return sorted(set(labels), key=order_key)


def wanted(lbl, known_labels):
    """새로 받을(추출할) 기간인가: 연간은 아직 없으면 언제든(늦게 낸 감사보고서·빠진 해 포함),
    상반기는 이미 있는 마지막 연간보다 뒤일 때만(지난 반기는 화면 기간에 없음), 1·3분기는 다루지 않음"""
    if lbl.endswith('분기'):
        return False
    if lbl.endswith('상반기'):
        annual = [order_key(k) for k in known_labels if len(k) == 4]
        return not annual or order_key(lbl) > max(annual)
    return True
