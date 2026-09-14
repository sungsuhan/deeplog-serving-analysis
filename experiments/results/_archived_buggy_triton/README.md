# 왜 여기로 옮겼나

`serving/triton/app.py`가 동기(gevent 기반) tritonclient를 FastAPI 스레드풀에서
호출해 `greenlet.error: Cannot switch to a different thread`로 일부 요청이
에러 처리되던 버그가 있었다(2026-08-06 발견). 이 버그는 concurrency=4/8에서만
발현됐고(n_errors=12~30), 그 결과 성공한 요청의 지연시간은 실제보다 낮게,
처리량은 실제보다 낮게 측정됐다 — cycle2/3의 triton 데이터가 바로 이 버그가
있는 상태에서 측정된 것이다.

`tritonclient.http.aio`(비동기 클라이언트)로 교체해 수정한 뒤(cycle4 이후),
같은 concurrency=4/8에서 처리량이 약 2배(300→620 rps)로 뛰고 n_errors=0이 됨을
확인했다 — 즉 cycle2/3의 triton 수치는 "다른 조건"이 아니라 "고장난 상태의
측정값"이라 통계 분석에서 제외해야 한다.

fastapi/bentoml은 이 버그와 무관(tritonclient를 쓰지 않음)하므로 cycle2/3의
해당 데이터는 그대로 유효하며 원래 위치에 남아 있다. 여기 옮겨진 건 오염된
triton 데이터 2개 사이클(cycle2, cycle3)뿐이다.

상세 경위는 EXPERIMENT.md §4.3 참고.
