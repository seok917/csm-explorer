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
