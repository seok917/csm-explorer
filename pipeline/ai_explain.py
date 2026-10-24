"""
AI 설명 생성 + 숫자·표현 가드레일
1) 회사·연도별 원표 숫자(변동표·요인)만 AI에게 전달 — 문장에 그대로 쓸 '표기' 값을 함께 줌
2) AI가 쓴 설명의 숫자를 guard.verify로 원표와 대조 + 계리 표현 원칙에 어긋나는 말(원인 추정·평가어) 점검
3) 틀린 숫자·표현이 있으면 무엇이 틀렸는지 알려주고 한 번 다시 쓰게 함
4) 그래도 틀리면 '반려'로 기록하고 화면에는 규칙 기반 요약만 표시

API 키는 코드에 쓰지 않음 → 환경변수 ANTHROPIC_API_KEY로만 읽음 (GitHub에 올릴 때도 키는 Secrets에)
키가 없으면 아무것도 하지 않고 종료 (나머지 파이프라인은 그대로 작동)
실행: ANTHROPIC_API_KEY=... python pipeline/ai_explain.py [--only 신한라이프] [--force]
- 원표 숫자(자료)가 바뀌지 않은 회사·연도는 다시 호출하지 않음(비용 절약). --force면 전부 다시 생성
- GitHub Actions(.github/workflows/ai-explain.yml)에서 저장소 Secret의 키로 실행
"""
import csv, hashlib, json, os, re, sys, time, urllib.request, urllib.error
sys.path.insert(0, os.path.dirname(__file__))
from guard import verify, facts_from

import seg
D = seg.DATA
MODEL = os.environ.get('CSM_AI_MODEL', 'claude-haiku-4-5-20251001')   # 비용이 낮은 모델 기본, 환경변수로 변경
API = 'https://api.anthropic.com/v1/messages'

SYSTEM = """당신은 보험사 계리 결산 담당자를 돕는 분석 보조입니다. 공시된 CSM(보험계약마진) 변동표 숫자를 보고 변동 내용을 설명합니다.
규칙:
- 숫자는 [자료]의 '표기' 값을 글자 그대로 옮겨 쓰세요. 직접 반올림하거나 단위를 바꾸지 마세요.
- 비율·차이·합계 등 새 숫자를 계산하지 마세요. [자료]에 없는 비율은 쓰지 마세요.
- 제목, 마크다운(#, **), 목록 없이 3~4문장의 한국어 존댓말 평문으로 쓰세요. 가장 크게 움직인 항목부터 설명하세요.
- 공시에 나온 항목명과 증감 방향만 서술하세요. '손실', '때문', '~로 보입니다' 같은 원인 추정 표현은 쓰지 말고, 원인을 알 수 없으면 '공시만으로는 확인되지 않습니다'라고 쓰세요.
- 가정 변경은 실제 경험과 전망을 반영한 조정으로 서술하고, 보수적·낙관적·부정확 같은 평가어와 회사에 대한 권고는 쓰지 마세요."""
PROMPT_VER = 'v2'   # v1(2026-10-14 첫 실행): 숫자를 억 원 소수로만 줘서 AI가 조·억 환산·반올림을 하다 틀림 → v2: 표기용 숫자 제공

# 표현 가드레일: 계리 표현 원칙에 어긋나는 말(원인 추정·평가어)과 마크다운을 잡음 ('손실요소'는 공시 용어라 허용)
STYLE = re.compile(r'손실(?!요소)|때문|로 보입니다|것으로 보|보수적|낙관적|부정확|^\s*#|\*\*', re.M)


def won(x):
    """억 원 → '7조 5,537억 원' (반올림 후 조 단위 올림까지 처리)"""
    s = '−' if x < 0 else ''
    a = abs(x)
    if a < 10:
        return f'{s}{a:.1f}억 원'
    n = round(a)
    jo, eok = divmod(n, 10000)
    return f'{s}{jo}조 {eok:,}억 원' if jo and eok else (f'{s}{jo}조 원' if jo else f'{s}{eok:,}억 원')


def build_facts_text(name, year, rec, ae=None):
    """AI에게 줄 자료: 항목마다 '표기'(문장에 그대로 쓸 값)를 함께 제공. 보험금 예실차는 CSM 변동 설명과 섞이지 않도록 제외"""
    item = lambda label, v: f'{label}: 표기 {won(v)} (원값 {v:,.1f}억)'
    lines = [f'회사: {name}', f'기간: {year}',
             item('기초 CSM', rec['open']), item('신계약 CSM', rec['new']), item('CSM 상각', rec['release']),
             item('이자부리', rec['interest']), item('조정 추정치 변동', rec['adjust']), item('기말 CSM', rec['close']),
             item('기말−기초 증감', rec['close'] - rec['open'])]
    if rec.get('offset') is not None:
        lines.append(f"조정 추정치 변동 ÷ 신계약 CSM: 표기 {rec['offset'] * 100:.0f}%")
    if rec.get('adjust_post') is not None:
        lines.append(item('그중 수정소급·공정가치법 외 계약(주로 IFRS17 전환일 이후 인식 계약)의 조정', rec['adjust_post']))
    if rec.get('assump'):
        lines.append('조정 추정치 변동의 요인별 금액: ' + ', '.join(f'{k} {won(v)}' for k, v in rec['assump'].items()))
    return '\n'.join(lines)


def check(text, allowed):
    """숫자 가드레일 + 표현 가드레일"""
    g = verify(text, allowed)
    g['style'] = sorted(set(m.group(0).strip() for m in STYLE.finditer(text)))
    g['ok'] = g['ok'] and not g['style']
    return g


def feedback(g):
    out = []
    if g['mismatches']:
        out.append(f"다음 숫자가 [자료]와 맞지 않습니다: {', '.join(g['mismatches'])}. [자료]의 표기 값만 그대로 쓰세요.")
    if g['style']:
        out.append(f"다음 표현은 쓰지 마세요: {', '.join(g['style'])}. 항목명과 증감 방향만 평문으로 쓰세요.")
    return ' '.join(out) + ' 다시 작성해 주세요.'


def call(messages, key):
    body = json.dumps({'model': MODEL, 'max_tokens': 700, 'system': SYSTEM, 'messages': messages}).encode()
    req = urllib.request.Request(API, data=body, headers={'x-api-key': key, 'anthropic-version': '2023-06-01',
                                                          'content-type': 'application/json'})
    with urllib.request.urlopen(req, timeout=60) as r:
        out = json.loads(r.read())
    return ''.join(b.get('text', '') for b in out.get('content', []))


def main():
    key = os.environ.get('ANTHROPIC_API_KEY')
    if not key:
        print('ANTHROPIC_API_KEY가 없어 AI 설명을 건너뜁니다 (화면에는 규칙 기반 요약 표시).')
        return
    only = sys.argv[sys.argv.index('--only') + 1] if '--only' in sys.argv else None
    force = '--force' in sys.argv
    web = json.load(open(f'{D}/web.json', encoding='utf-8'))
    path = f'{D}/ai_explanations.json'
    done = json.load(open(path, encoding='utf-8')) if os.path.exists(path) else {}
    stats = dict(ok=0, fixed=0, rejected=0, error=0, skipped=0)
    for c in web['companies']:
        if only and c['name'] != only: continue
        aes = {a['year']: a for a in c['ae']}
        for year, rec in c['years'].items():
            k = f"{c['name']}|{year}"
            ae = aes.get(year[:4]) if aes.get(year[:4], {}).get('exp') is not None else None
            facts = build_facts_text(c['name'], year, rec)
            allowed = facts_from(rec)
            h = hashlib.sha1((MODEL + SYSTEM + facts).encode()).hexdigest()[:12]   # 자료·지시문·모델이 같으면 다시 부르지 않음
            if not force and done.get(k, {}).get('hash') == h:
                stats['skipped'] += 1; continue
            msgs = [{'role': 'user', 'content': f'[자료]\n{facts}\n\n위 자료로 CSM 변동 원인을 설명해 주세요.'}]
            try:
                text = call(msgs, key); g = check(text, allowed); attempts = 1
                first = g
                if not g['ok']:                                  # 틀린 숫자·표현을 알려주고 한 번 더
                    msgs += [{'role': 'assistant', 'content': text}, {'role': 'user', 'content': feedback(g)}]
                    text = call(msgs, key); g = check(text, allowed); attempts = 2
                status = 'ok' if g['ok'] and attempts == 1 else ('fixed' if g['ok'] else 'rejected')
                stats[status] += 1
                done[k] = dict(text=text.strip(), model=MODEL, guard=g, attempts=attempts, status=status,
                               created=time.strftime('%Y-%m-%d'), hash=h, prompt=PROMPT_VER,
                               first_try=dict(numbers=first['mismatches'], style=first['style']))
                stats['first_num_err'] = stats.get('first_num_err', 0) + bool(first['mismatches'])
                stats['first_style_err'] = stats.get('first_style_err', 0) + bool(first['style'])
                print(f"{k:<24} {status:<9} 숫자 {g['checked']}개 대조, 불일치 {g['mismatches']}")
            except urllib.error.HTTPError as e:
                if e.code in (401, 403):                         # 키 오류는 72번 반복하지 않고 바로 중단
                    print(f'API 키 인증 실패({e.code}): 저장소 Secret의 ANTHROPIC_API_KEY를 확인하세요.')
                    sys.exit(1)
                stats['error'] += 1; print(f'{k:<24} 호출 실패: HTTP {e.code} {e.read()[:200]!r}')
            except (urllib.error.URLError, TimeoutError, KeyError) as e:
                stats['error'] += 1; print(f'{k:<24} 호출 실패: {e}')
            time.sleep(0.5)
    json.dump(done, open(path, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print('결과:', stats, '→', path)
    called = stats['ok'] + stats['fixed'] + stats['rejected']
    if called:                                           # 실행 기록(감사 추적): 언제·어떤 지시문으로·몇 건이 처음에 틀렸는지
        log = f'{D}/ai_runs.csv'
        new = not os.path.exists(log)
        with open(log, 'a', newline='', encoding='utf-8') as f:
            w = csv.writer(f)
            if new:
                w.writerow(['date', 'model', 'prompt', 'called', 'ok', 'fixed', 'rejected', 'first_num_err', 'first_style_err'])
            w.writerow([time.strftime('%Y-%m-%d'), MODEL, PROMPT_VER, called, stats['ok'], stats['fixed'], stats['rejected'],
                        stats.get('first_num_err', 0), stats.get('first_style_err', 0)])


if __name__ == '__main__':
    main()
