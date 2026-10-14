"""4단계: 데이터(JSON)를 화면 템플릿에 넣어 웹페이지 생성
- site/index.html   : GitHub Pages 등 어디에나 올릴 수 있는 완성 페이지
- site/artifact.html: 미리보기용(문서 골격 없이 본문만)
"""
import json, os
B = os.path.join(os.path.dirname(__file__), '..')
data = open(f'{B}/data/web.json', encoding='utf-8').read().replace('</', '<\\/')
tpl = open(f'{B}/site/template.html', encoding='utf-8').read().replace('/*__DATA__*/', data)
full = ('<!doctype html>\n<html lang="ko">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n'
        '<meta name="description" content="생명보험사 22곳의 CSM 공시를 자동 수집·검증해 회사별로 조회하는 개인 프로젝트">\n'
        '<style>:root{color-scheme:light}body{margin:0}[hidden]{display:none!important}img{max-width:100%}</style>\n'
        '</head>\n<body>\n' + tpl + '\n</body>\n</html>\n')
open(f'{B}/index.html', 'w', encoding='utf-8').write(full)
print('index.html', round(len(full.encode()) / 1024), 'KB')
