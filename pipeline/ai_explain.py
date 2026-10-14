"""
AI 설명 생성 + 숫자 가드레일
1) 회사·연도별 원표 숫자(변동표·요인·예실차)만 AI에게 전달
2) AI가 쓴 설명의 숫자를 guard.verify로 원표와 대조
3) 틀린 숫자가 있으면 어떤 숫자가 틀렸는지 알려주고 한 번 다시 쓰게 함
4) 그래도 틀리면 '반려'로 기록하고 화면에는 규칙 기반 요약만 표시

API 키는 코드에 쓰지 않음 → 환경변수 ANTHROPIC_API_KEY로만 읽음 (GitHub에 올릴 때도 키는 Secrets에)
키가 없으면 아무것도 하지 않고 종료 (나머지 파이프라인은 그대로 작동)
실행: ANTHROPIC_API_KEY=... python pipeline/ai_explain.py [--only 신한라이프]
"""
import json, os, sys, time, urllib.request, urllib.error
sys.path.insert(0, os.path.dirname(__file__))
from guard import verify, facts_from

D = os.path.join(os.path.dirname(__file__), '..', 'data')
MODEL = os.environ.get('CSM_AI_MODEL', 'claude-haiku-4-5-20251001')   # 비용이 낮은 모델 기본, 환경변수로 변경
API = 'https://api.anthropic.com/v1/messages'

SYSTEM = """당신은 보험사 계리 결산 담당자를 돕는 분석 보조입니다. 공시된 CSM(보험계약마진) 변동표 숫자를 보고 변동 원인을 설명합니다.
규칙:
- 아래 [자료]에 있는 숫자만 쓰세요. 계산해서 새 숫자를 만들지 마세요. 단위는 '억 원', 큰 금액은 '7조 5,537억 원'처럼 쓰세요.
- 3~4문장, 한국어, 존댓말 서술형. 가장 크게 움직인 요인부터 설명하세요.
- 원인을 단정하지 마세요. 공시로 알 수 없는 원인은 추측하지 말고 '공시만으로는 확인되지 않는다'고 쓰세요.
- 예실차가 크다는 사실만으로 가정이 부정확하다고 판단하지 마세요. 가정 변경은 실제 경험과 전망을 반영한 조정으로 서술하세요.
- 보수적/낙관적 같은 평가어, 회사에 대한 권고는 쓰지 마세요."""


def build_facts_text(name, year, rec, ae):
    lines = [f'회사: {name}', f'기간: {year}', '단위: 억 원',
             f"기초 CSM {rec['open']:,.1f}", f"신계약 CSM {rec['new']:,.1f}", f"CSM 상각 {rec['release']:,.1f}",
             f"이자부리 {rec['interest']:,.1f}", f"조정 추정치 변동 {rec['adjust']:,.1f}", f"기말 CSM {rec['close']:,.1f}",
             f"기말−기초 {rec['close'] - rec['open']:,.1f}"]
    if rec.get('offset') is not None: lines.append(f"조정 추정치 변동 ÷ 신계약 CSM = {rec['offset'] * 100:.0f}%")
    if rec.get('adjust_post') is not None: lines.append(f"그중 IFRS17 전환일 이후 인식 계약의 조정 {rec['adjust_post']:,.1f}")
    if rec.get('assump'):
        lines.append('조정 추정치 변동의 요인: ' + ', '.join(f'{k} {v:,.1f}' for k, v in rec['assump'].items()))
    if ae:
        lines.append(f"보험금 예상손해율 {ae['exp']}%, 실제손해율 {ae['act']}%, 예실차 {ae['ae']}%p")
    return '\n'.join(lines)


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
    web = json.load(open(f'{D}/web.json', encoding='utf-8'))
    path = f'{D}/ai_explanations.json'
    done = json.load(open(path, encoding='utf-8')) if os.path.exists(path) else {}
    stats = dict(ok=0, fixed=0, rejected=0, error=0)
    for c in web['companies']:
        if only and c['name'] != only: continue
        aes = {a['year']: a for a in c['ae']}
        for year, rec in c['years'].items():
            k = f"{c['name']}|{year}"
            ae = aes.get(year[:4]) if aes.get(year[:4], {}).get('exp') is not None else None
            facts = build_facts_text(c['name'], year, rec, ae)
            allowed = facts_from(rec, ae)
            msgs = [{'role': 'user', 'content': f'[자료]\n{facts}\n\n위 자료로 CSM 변동 원인을 설명해 주세요.'}]
            try:
                text = call(msgs, key); g = verify(text, allowed); attempts = 1
                if not g['ok']:                                  # 틀린 숫자를 알려주고 한 번 더
                    msgs += [{'role': 'assistant', 'content': text},
                             {'role': 'user', 'content': f"다음 숫자가 [자료]와 맞지 않습니다: {', '.join(g['mismatches'])}. [자료]에 있는 숫자만 써서 다시 작성해 주세요."}]
                    text = call(msgs, key); g = verify(text, allowed); attempts = 2
                status = 'ok' if g['ok'] and attempts == 1 else ('fixed' if g['ok'] else 'rejected')
                stats[status] += 1
                done[k] = dict(text=text.strip(), model=MODEL, guard=g, attempts=attempts, status=status,
                               created=time.strftime('%Y-%m-%d'))
                print(f"{k:<24} {status:<9} 숫자 {g['checked']}개 대조, 불일치 {g['mismatches']}")
            except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, KeyError) as e:
                stats['error'] += 1; print(f'{k:<24} 호출 실패: {e}')
            time.sleep(0.5)
    json.dump(done, open(path, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print('결과:', stats, '→', path)


if __name__ == '__main__':
    main()
