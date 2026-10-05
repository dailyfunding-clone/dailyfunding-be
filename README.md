# dailyfunding-be

데일리펀딩 클론 백엔드. Django 5 + DRF + PostgreSQL + Celery/Redis.
투자자 생명주기 전체(가입 → 본인인증 → 가상계좌 발급 → 모의은행 입금 → 투자 → 대출 실행 → 상환 → 출금)를
이중분개 원장 위에서 API만으로 완주할 수 있다. 외부 금융/인증 연동은 전부 모의 구현이다.

## 구성

- `config/` — Django 설정, URL 라우팅, Celery
- `api/accounts` — 회원, 본인인증(모의), PIN/재인증, 가상계좌, 연결계좌, 투자자 등급
- `api/ledger` — 이중분개 원장(LedgerEntry, 분개 그룹별 차대 합계 0을 DB deferred trigger로 강제), 예치금, 출금 상태머신, 포인트, 멱등 레코드
- `api/webhooks` — 은행 웹훅 수신: `X-Bank-Signature: t={ts},v1={hmac}` HMAC-SHA256 검증, ±5분 skew, `event_id` 멱등, sender mismatch 보류 큐
- `api/products` — 상품 상태머신(`draft→scheduled→recruiting→recruited→executed→repaying→repaid|overdue|loss`), 상환 스케줄 계산기(원리금균등/만기일시/원금균등, 세금 15.4%, 마지막 회차 반올림 흡수)
- `api/investments` — 투자 주문(`SELECT FOR UPDATE` 잔액 직렬화, `Idempotency-Key` 멱등, 등급/동일차주 한도), 적합성 테스트, 장바구니, 예약투자
- `api/loans` — 대출 신청/심사, `api/contents` — 공지/이벤트/FAQ, `api/mypage` — 마이페이지 집계, `api/notifications` — 알림/디바이스
- `api/adminpanel` — 운영자 API(상품/대출/배치/시드/시간 진행)
- `mockbank` — 모의 은행. 입금/이체 실행 후 서명된 웹훅을 발행. `force_fail`, `force_mismatch`, `delay_ms` 실패 주입 지원
- `jobs` — Celery 배치: 상환 실행, 포인트 소멸, 원장 대사, 웹훅 재시도(지수 백오프, 최대 8회), 예약 롤오버
- `ops` — `e2e_check.py` E2E 스크립트, `k6/` 투자 경합 시나리오
- `tests` — PRD §5의 6개 정합성 시나리오 포함 pytest 스위트

## 실행

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env

# Postgres/Redis: 로컬 또는
docker compose up -d db redis

python manage.py migrate
python manage.py runserver
```

배치 워커/스케줄러(선택):

```bash
celery -A config worker -l info
celery -A config beat -l info
```

## 검증

```bash
pytest                                          # 31개 테스트 (정합성 6 시나리오 포함)
python manage.py spectacular --format openapi-json --file schema.json
python ops/e2e_check.py                         # 가입→입금→투자→상환→출금 E2E (runserver 기동 상태에서)
```

## 운영자 유틸

- `POST /api/admin/seed/products` — 시드 상품 생성. `raised_amount`는 시드 봇의 실제 투자 주문으로만 채워 원장 정합성을 유지한다.
- `POST /api/admin/batch/repay?date=YYYY-MM-DD` — 상환 배치(멱등)
- `POST /api/admin/batch/reconcile` — 원장 대사(raised_amount ↔ 투자 합계 등 불일치 리포트)
- `POST /mockbank/deposits/execute`, `POST /mockbank/transfers/execute` — 모의 은행 실행 + 웹훅 발행

## 주요 규약

- JWT(access 15분 / refresh 14일), 쿠키 기반. 재인증: `X-Reauth-Token` 헤더
- 멱등 쓰기: `Idempotency-Key` 헤더, `(user, key)` 단위로 응답 재생, 페이로드 불일치 시 거절
- 에러 형식: `{"code","message","details"}` / 페이지네이션: `page`, `page_size` (최대 100)
- 금액은 정수 KRW, 시각은 UTC ISO 8601
