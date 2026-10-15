"""한 번에 실행: 데이터 정리 → 원문 표에서 변동표 추출 → 원문 신호 탐지 → 자동 검증 → 화면 데이터·요약 → 웹페이지 생성
실행 시간을 단계별로 기록 (자소서·면접용 실측값)"""
import subprocess, time, sys, os
H = os.path.dirname(os.path.abspath(__file__))
steps = [('0 DART 새 공시 수집(키 있을 때만)', 'collect_dart.py'), ('1 데이터 정리', 'build_data.py'), ('2 원문 표 → CSM 변동표 추출', 'extract_movement.py'),
         ('2b 원문 신호 탐지(109개 보고서)', 'scan_notes.py'),
         ('2c 상각 예정표 추출·평가', 'extract_schedule.py'), ('2d FISIS 지표', 'build_fisis.py'), ('2e 가정 민감도 추출', 'extract_sensitivity.py'),
         ('3 자동 검증(규칙 6개)', 'validate.py'), ('4 지표·요약 문장 생성', 'export_web.py'),
         ('5 AI 설명+숫자 대조(키 있을 때만)', 'ai_explain.py'), ('6 AI 결과 반영', 'export_web.py'), ('7 웹페이지 생성', 'build_site.py')]
tot = 0
for name, f in steps:
    t = time.perf_counter(); r = subprocess.run([sys.executable, os.path.join(H, f)], capture_output=True, text=True, cwd=H)
    el = time.perf_counter() - t; tot += el
    print(f'{name:<28} {el:6.1f}초  {"OK" if r.returncode == 0 else "실패: " + r.stderr[-200:]}')
print(f'{"전체":<28} {tot:6.1f}초')
