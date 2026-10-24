"""
업권(생보·손보) 설정 — 같은 파이프라인을 업권별 폴더에 돌리기 위한 한 곳
- 환경변수 CSM_SEGMENT=life(기본) | nonlife
- life   : 저장소 루트의 raw/, data/ (기존 그대로)
- nonlife: segments/nonlife/raw/, segments/nonlife/data/
"""
import os

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
SEG = os.environ.get('CSM_SEGMENT', 'life')
BASE = ROOT if SEG == 'life' else os.path.join(ROOT, 'segments', SEG)
RAW = os.path.join(BASE, 'raw')
DATA = os.path.join(BASE, 'data')
LABEL = {'life': '생명보험', 'nonlife': '손해보험'}[SEG]
os.makedirs(RAW, exist_ok=True)
os.makedirs(DATA, exist_ok=True)

# 손보: DART 법인명(정리 후) → 화면 이름. 탐색(discover_nonlife.py) 결과로 확정
NONLIFE_SHORT = {
    '삼성화재해상보험': '삼성화재', 'DB손해보험': 'DB손해보험', '현대해상화재보험': '현대해상', 'KB손해보험': 'KB손해보험',
    '메리츠화재해상보험': '메리츠화재', '한화손해보험': '한화손해보험', '롯데손해보험': '롯데손해보험', '흥국화재해상보험': '흥국화재',
    '농협손해보험': 'NH농협손해보험', '엔에이치농협손해보험': 'NH농협손해보험', '하나손해보험': '하나손해보험',
    '엠지손해보험': 'MG손해보험', 'MG손해보험': 'MG손해보험', '악사손해보험': 'AXA손해보험', '에이엑스에이손해보험': 'AXA손해보험',
    '신한이지손해보험': '신한EZ손해보험', '카카오페이손해보험': '카카오페이손해보험', '캐롯손해보험': '캐롯손해보험',
}


def short_map():
    if SEG == 'life':
        from build_data import SHORT
        return SHORT
    return NONLIFE_SHORT
