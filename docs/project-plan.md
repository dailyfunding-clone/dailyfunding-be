# 프로젝트 기획서 — dailyfunding-be

- 문서 버전: v2.0 · 작성일: 2026-10-01
- 상위 문서: `../../docs/PRD.md` · `../../docs/feature-spec.md` · `../../docs/tech-stack.md` · `../../docs/feature-map.md`
- 저장소 범위: Django + DRF API 서비스 + 모의 은행(mockbank) + 배치(Celery)

---

## 1. 목표

온투업 플랫폼의 서버 전체를 실제 동작하는 코드로 구현한다.

1. **돈의 이동은 원장으로만 표현** — 모든 잔액 변화를 이중분개 ledger로 기록하고 대사 배치로 검증한다
2. **투자 주문의 동시성·멱등성을 정합하게 처리**하고 k6 부하 테스트로 수치를 남긴다
3. 외부 금융 연동은 **모의 은행(mockbank)의 실제 웹훅**으로 대체해 연동 경계(서명·재시도·멱등)를 실제로 구현한다
4. **OpenAPI 스키마를 계약의 소스**로 삼아 FE 타입 생성 파이프라인을 CI로 잇는다

---

## 2. 아키텍처

```
dailyfunding-be
├─ config/                 # Django settings, urls, celery app
├─ api/                    # DRF viewsets·serializers
│  ├─ accounts/            # 회원·인증·등급·본인인증(모의)
│  ├─ products/            # 투자 상품·모집·상태머신
│  ├─ investments/         # 주문·적합성·장바구니·예약투자
│  ├─ ledger/              # 예치금·포인트 원장 + 대사
│  ├─ loans/               # 대출 상품·한도조회(모의)·신청
│  ├─ contents/            # 공지·FAQ·이벤트·공시·언론·약관
│  ├─ mypage/              # 대시보드·달력·내역 집계 API
│  └─ webhooks/            # 은행 웹훅 수신(서명·멱등·재시도)
├─ mockbank/               # 모의 은행: 입금 통지 발행·이체 상태머신
├─ jobs/                   # Celery 태스크: 상환·소멸·대사·웹훅 재시도
└─ ops/                    # 시드 생성기·시간 진행 유틸·k6 시나리오
```

### 핵심 설계 원칙

- **이중분개 원장**: `LedgerEntry`에 차변·대변 계정과 금액을 분개 단위로 기록. 분개마다 차변 합 = 대변 합 CHECK. 잔액은 파생값(분개 합계)
- **투자 주문**: 단일 DB 트랜잭션 안에서 `SELECT ... FOR UPDATE`로 상품 모집 잔액을 잠그고 한도·예치금을 검증 후 차감. 멱등 키로 재전송 안전
- **상태머신**: 상품(모집예정→모집중→모집완료→대출실행→상환중→완료/연체/손실), 출금(requested→processing→completed/failed), 입금(pending→credited/held)
- **배치 멱등**: 상환·소멸·대사 태스크는 (키, 회차/일자) 유니크 제약, 재실행 안전

---

## 3. API 목록 (요약)

| 그룹 | 엔드포인트 | 기능 ID |
|---|---|---|
| 인증 | `POST /api/auth/signup` `/login` `/logout` `/pin` `/verify-identity` `/reauth` | F-AUTH-01~05 |
| 상품 | `GET /api/products` `GET /api/products/{id}` `GET /api/products/{id}/schedule-preview` | F-INV-01~03 |
| 투자 | `POST /api/investments` `GET /api/investments` `GET /api/investments/{id}` | F-INV-04 |
| 적합성 | `GET/POST /api/suitability-test` | F-INV-05 |
| 장바구니 | `GET/POST/DELETE /api/cart` | F-INV-06 |
| 예약 | `POST /api/reservations` `DELETE /api/reservations/{id}` | F-INV-07 |
| 예치금 | `GET /api/deposit/account` `POST /api/deposit/notify-intent` `POST /api/deposit/withdraw` `GET /api/deposit/history` | F-DEP-01~05 |
| 포인트 | `GET /api/points` `GET /api/points/history` `POST /api/points/convert` | F-PNT-01~02 |
| 마이페이지 | `GET /api/me/dashboard` `/api/me/calendar` `/api/me/investments` `/api/me/grade` `POST /api/me/grade-request` | F-MY-01~05 |
| 대출 | `GET /api/loans` `POST /api/loans/limit-check` `POST /api/loans/applications` | F-LOAN-01~04 |
| 콘텐츠 | `GET /api/notices` `/faqs` `/events` `/disclosures` `/news` `/terms/{id}` | F-CON-01~07 |
| 웹훅 | `POST /api/webhooks/bank/deposit` `/api/webhooks/bank/transfer` | F-DEP-02/03 |
| 운영자 | `/api/admin/*` (상품·실행·상환 배치·심사·콘텐츠) | F-ADM-01~06 |

전체 스펙은 drf-spectacular가 생성하는 `schema.json`이 정본이다.

---

## 4. 데이터 모델 핵심

```
accounts: User, IdentityVerification(ci, 모의), InvestorGrade, GradeRequest
accounts: LinkedAccount, VirtualAccount(bank_code, number, user UNIQUE)
products: Product(no, type, rate, term, amount, repay_type, tags, status), ProductDocument
investments: SuitabilityTest(user, passed_at, expires_at)
investments: Investment(idempotency_key UNIQUE, user, product, amount, status)
investments: Cart, Reservation(상태: reserved→converted/cancelled/refunded)
ledger: LedgerEntry(group_id, account, amount, kind, ref_type, ref_id, created_at)
         — 계정: deposit:user_x / investment:product_y / revenue:fee / point_liability / clearing:bank
points: PointEntry(earn/spend/expire, expires_at)
schedule: RepaymentSchedule(investment, seq, due_date, principal, interest, fee, tax, status)
loans: LoanProduct, LimitCheck(모의 결과), LoanApplication(+첨부)
webhooks: WebhookEvent(event_id UNIQUE, type, payload, signature, processed_at)
```

---

## 5. 비기능 — 정합성·동시성·멱등 (검증 계획 포함)

| 문제 | 설계 | 검증 |
|---|---|---|
| 모집 잔액 경합 | 모집 잔액 행 `SELECT FOR UPDATE` + 잔액 CHECK | k6 동시 투자 N건 → 초과 0건 |
| 중복 주문 | `Idempotency-Key` 유니크 + 저장된 결과 반환 | 동일 키 재전송 → 1건만 생성 |
| 입금 웹훅 중복/위조 | `event_id` 유니크 + HMAC 서명 검증 + 보류 큐 | 위조 서명 거부, 재수신 무시 |
| 스케줄 불일치 | 스케줄 합계 = 원리금 제약 + 대사 배치가 차이 리포트 | 배치 결과 차이 0 |
| 출금 경합/실패 | 홀드→이체→확정 상태머신, 실패 시 역분개 | 강제 실패 주입 후 잔액 복원 |
| 배치 재실행 | (투자,회차) 유니크로 중복 지급 불가 | 동일 배치 2회 실행 시 동일 결과 |

측정 산출물: k6 리포트 + 대사 배치 로그 + 위 케이스별 테스트(`pytest`).

---

## 6. 배치·비동기 (Celery)

| 태스크 | 주기 | 내용 |
|---|---|---|
| `repay.daily` | 매일 06:00 (또는 시간 진행 유틸) | 당일 지급 스케줄 → 원장 상환 분개·예치금 입금 |
| `points.expire` | 매월 1일 | 만료 포인트 소멸 분개 |
| `ledger.reconcile` | 매일 | 차대변·잔액 대사, 불일치 리포트 |
| `webhook.retry` | 실패 시 지수 백오프 | 모의 은행 → API 재전송 |
| `product.rollover` | 만기 도래 시 | 예약 투자 전환 또는 원리금 상환 |

---

## 7. 마일스톤

| Phase | 내용 | 산출물 |
|---|---|---|
| M1 | 골격 + 인증 + 스키마 파이프라인 | Django/DRF/simplejwt, drf-spectacular, CI |
| M2 | 원장 + 가상계좌 + 모의 은행 | 이중분개, 입금 웹훅, 출금 상태머신 |
| M3 | 상품 + 투자 주문 | 상태머신, 한도 규칙, 동시성·멱등 + k6 |
| M4 | 상환 + 포인트 | 스케줄 생성·상환 배치·소멸·대사 |
| M5 | 마이페이지 집계 + 대출 | 대시보드/달력/내역 API, 한도조회·신청 |
| M6 | 콘텐츠 + 운영자 | 공지/FAQ/이벤트/공시, 관리자 API |
| M7 | 시드 + 시간 제어 + 검증 리포트 | 상품 생성기, 시연 데이터, 부하/정합성 결과 문서 |

## 8. 완료 기준

- §5의 6개 정합성 시나리오가 테스트로 통과하고 수치가 리포트에 남음
- OpenAPI 스키마가 CI에서 생성되고 FE가 동일 스키마로 타입 생성
- 시드 데이터로 가입→입금→투자→상환→출금 E2E가 API만으로 완주

## 9. 기술 과제 (해결·문서화 대상)

- 모집 잔액 경합 직렬화 — `FOR UPDATE` vs 낙관적 잠금 비교 근거
- 원장 이중분개 설계와 대사 배치로 잔액 정합성 보증
- 멱등 키 저장/응답 재생 전략
- 웹훅 서명·재시도·보류 큐 — 외부 연동 경계의 자체 구현
- 상환 스케줄 생성의 반올림 처리(마지막 회차 잔액 흡수)와 세금 계산
