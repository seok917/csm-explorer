"""
손보 범위 탐색 (GitHub Actions에서 1회 실행): 어떤 회사를 넣을지, FISIS 통계 번호는 무엇인지 확인
- DART: 법인명에 '손해보험'·'화재'가 들어간 회사 → 고유번호, 2024년 이후 정기보고서·감사보고서 건수
- FISIS: 손해보험(partDiv I) 회사 목록 + 통계표 목록(lrgDiv I) 전체
결과: segments/nonlife/raw/discovery.json
"""
import json, os, sys, time
sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault('CSM_SEGMENT', 'nonlife')
import seg
from collect_dart import get, unzip_or_die, note
from lxml import etree


def dart(key):
    z = unzip_or_die(get('corpCode.xml', {'crtfc_key': key}, binary=True), 'corpCode')
    root = etree.fromstring(z.read(z.namelist()[0]))
    out = []
    for it in root.iter('list'):
        nm = ' '.join((it.findtext('corp_name') or '').split())
        if ('손해보험' in nm or '화재' in nm) and '대리점' not in nm and '중개' not in nm and '손해사정' not in nm:
            out.append(dict(name=nm, corp_code=it.findtext('corp_code'), stock=it.findtext('stock_code') or ''))
    for c in out:
        for ty in ('A', 'F'):
            js = get('list.json', dict(crtfc_key=key, corp_code=c['corp_code'], pblntf_ty=ty, bgn_de='20240101',
                                       end_de=time.strftime('%Y%m%d'), page_count=100))
            c['n_' + ty] = int(js.get('total_count', 0)) if js.get('status') == '000' else 0
            if js.get('status') == '000':
                c['reports_' + ty] = sorted({r['report_nm'] for r in js['list']})[:8]
            time.sleep(0.3)
    return [c for c in out if c.get('n_A') or c.get('n_F')]


def fisis(key):
    from collect_fisis import call, rows
    out = {}
    cd, msg, res = call('companySearch', key, partDiv='I')
    out['companies'] = rows(res) if cd == '000' else f'{cd} {msg}'
    cd, msg, res = call('statisticsListSearch', key, lrgDiv='I')
    out['lists'] = rows(res) if cd == '000' else f'{cd} {msg}'
    return out


def main():
    res = {}
    k = os.environ.get('DART_API_KEY')
    if k:
        res['dart'] = dart(k)
        note(f"DART 손보 후보 {len(res['dart'])}곳")
    k = os.environ.get('FISIS_API_KEY')
    if k:
        res['fisis'] = fisis(k)
        f = res['fisis']
        note(f"FISIS 손보 회사 {len(f['companies']) if isinstance(f['companies'], list) else f['companies']} · "
             f"통계표 {len(f['lists']) if isinstance(f['lists'], list) else f['lists']}")
    json.dump(res, open(os.path.join(seg.RAW, 'discovery.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=1)


if __name__ == '__main__':
    main()
