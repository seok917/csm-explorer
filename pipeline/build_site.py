"""4단계: 데이터(JSON)를 화면 템플릿에 넣어 웹페이지 생성 (업권별 같은 템플릿)
- 생보(CSM_SEGMENT=life, 기본): data/web.json → index.html
- 손보(CSM_SEGMENT=nonlife)    : segments/nonlife/data/web.json → nonlife.html
"""
import json, os, sys
sys.path.insert(0, os.path.dirname(__file__))
import seg
raw = open(os.path.join(seg.DATA, 'web.json'), encoding='utf-8').read()
meta = json.loads(raw)['meta']
data = raw.replace('</', '<\\/')
tpl = open(os.path.join(seg.ROOT, 'site', 'template.html'), encoding='utf-8').read().replace('/*__DATA__*/', data)
desc = f"{meta['seg']['label']}사 {meta['n_company']}곳의 CSM 공시를 자동 수집·검증해 회사별로 조회하는 개인 프로젝트"
full = ('<!doctype html>\n<html lang="ko">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n'
        f'<meta name="description" content="{desc}">\n'
        '<style>:root{color-scheme:light}body{margin:0}[hidden]{display:none!important}img{max-width:100%}</style>\n'
        '</head>\n<body>\n' + tpl + '\n</body>\n</html>\n')
out = os.path.join(seg.ROOT, meta['seg']['page'])
open(out, 'w', encoding='utf-8').write(full)
print(meta['seg']['page'], round(len(full.encode()) / 1024), 'KB')
