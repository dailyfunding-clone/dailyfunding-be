"""라이브 E2E 스모크: 가입→인증→입금(mockbank 웹훅)→투자→실행→상환→출금.

사용: python ops/e2e_check.py [base_url]  (runserver가 떠 있어야 함)
"""
import json
import sys
import time
import urllib.request
import uuid

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"


def call(method, path, body=None, token=None, headers=None, cookies=None):
    req = urllib.request.Request(
        BASE + path,
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
    )
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    if cookies:
        req.add_header("Cookie", "; ".join(f"{k}={v}" for k, v in cookies.items()))
    try:
        with urllib.request.urlopen(req) as r:
            set_cookie = r.headers.get_all("Set-Cookie") or []
            jar = {}
            for c in set_cookie:
                kv = c.split(";")[0]
                name, _, val = kv.partition("=")
                jar[name] = val
            return r.status, json.loads(r.read() or b"{}"), jar
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}"), {}


def main():
    email = f"e2e-{uuid.uuid4().hex[:6]}@test.local"
    pw = "Demo1234!"
    agreements = [
        {"term": t, "agreed": True}
        for t in ["investment", "service", "privacy", "credit_info", "electronic_finance"]
    ]

    # 1. 가입
    st, r, _ = call("POST", "/api/auth/signup", {"email": email, "password": pw, "agreements": agreements})
    assert st == 201, r
    print("signup ok:", r)

    # 2. 로그인
    st, r, jar = call("POST", "/api/auth/login", {"email": email, "password": pw})
    assert st == 200, r
    print("login ok:", r)

    # 3. 본인인증 (모의) — CI는 전화번호에서 파생되므로 실행마다 유니크하게
    phone = "010" + uuid.uuid4().hex[:8].translate(str.maketrans("abcdef", "123456"))
    st, r, _ = call(
        "POST", "/api/auth/identity/verify",
        {"carrier": "SKT", "name": "테스터", "birth": "19900101", "phone": phone},
        cookies=jar,
    )
    assert st == 200, r
    print("identity ok:", r)

    # 4. 적합성 테스트 (문항 → 정답 제출은 실제로는 사용자가 답함; 여기선 O/X 조합 시도)
    st, r, _ = call("GET", "/api/suitability-test", cookies=jar)
    questions = r["questions"]
    # 정답을 모르는 클라이언트는 실패할 수 있으므로, 데모에서는 서버 정답을 관리자로 확인하지 않고
    # 관리자 계정으로 유효 테스트를 통과 처리한다. 대신 여기선 정답 패턴을 직접 제출.
    # (실제 FE는 사용자가 답을 선택)
    answers = [{"seq": q["seq"], "choice": c} for q, c in zip(questions, ["X", "O", "O", "X", "O", "O"])]
    st, r, _ = call("POST", "/api/suitability-test", {"answers": answers}, cookies=jar)
    assert r.get("passed") is True, r
    print("suitability ok:", r)

    # 5. 입금 알리기 → mockbank 실행 → 웹훅 → 예치금 반영
    st, r, _ = call(
        "POST", "/api/deposit/notify-intent",
        {"sender_name": "테스터", "amount": 5_000_000},
        headers={"Idempotency-Key": str(uuid.uuid4())}, cookies=jar,
    )
    assert st == 202, r
    intent_id = r["intent_id"]
    st, r, _ = call("POST", "/mockbank/deposits/execute", {"intent_id": intent_id})
    assert st == 200 and r["delivered"], r
    st, r, _ = call("GET", "/api/deposit/account", cookies=jar)
    assert r["deposit"] == 5_000_000, r
    print("deposit ok:", r)

    # 6. 상품 찾기 → 관리자로 모집중 상품 생성
    st, r, jar_admin = call("POST", "/api/auth/login", {"email": "admin@demo.local", "password": "Admin1234!"})
    assert st == 200, r
    st, r, _ = call(
        "POST", "/api/admin/products",
        {"name": "E2E 상품", "type": "scf", "annual_rate": "10.00", "term_months": 2,
         "target_amount": 5_000_000, "repay_type": "bullet", "borrower_id": "b-e2e",
         "platform_fee_rate": "0.00"},
        cookies=jar_admin,
    )
    pid = r["id"]
    call("PATCH", f"/api/admin/products/{pid}/status", {"status": "scheduled"}, cookies=jar_admin)
    call("PATCH", f"/api/admin/products/{pid}/status", {"status": "recruiting"}, cookies=jar_admin)

    # 7. 투자
    st, r, _ = call(
        "POST", "/api/investments",
        {"product_id": pid, "amount": 5_000_000},
        headers={"Idempotency-Key": str(uuid.uuid4())}, cookies=jar,
    )
    assert st == 201, r
    inv_id = r["investment_id"]
    print("invest ok:", r["investment_id"], r["status"])

    # 8. 대출 실행
    st, r, _ = call("POST", f"/api/admin/products/{pid}/execute", cookies=jar_admin)
    assert r["status"] == "repaying", r

    # 9. 상환 배치 — 스케줄 회차별 날짜 진행
    st, r, _ = call("GET", f"/api/investments/{inv_id}", cookies=jar)
    for s in r["schedule"]:
        st, rr, _ = call("POST", f"/api/admin/batch/repay?date={s['pay_date']}", cookies=jar_admin)
    st, r, _ = call("GET", "/api/deposit/account", cookies=jar)
    assert r["deposit"] > 5_000_000, r
    print("repaid deposit:", r["deposit"])

    # 10. 출금 → mockbank 이체 → 완료
    st, r, _ = call("POST", "/api/auth/reauth", {"password": pw}, cookies=jar)
    reauth = r["reauth_token"]
    st, r, _ = call(
        "POST", "/api/deposit/withdraw", {"amount": 1_000_000},
        headers={"Idempotency-Key": str(uuid.uuid4()), "X-Reauth-Token": reauth},
        cookies=jar,
    )
    assert st == 202, r
    wd_id = r["withdrawal_id"]
    st, r, _ = call("POST", "/mockbank/transfers/execute", {"withdrawal_id": wd_id})
    assert r["delivered"], r
    st, r, _ = call("GET", "/api/deposit/account", cookies=jar)
    print("withdraw ok, deposit:", r["deposit"])

    # 11. 대사
    st, r, _ = call("POST", "/api/admin/batch/reconcile", cookies=jar_admin)
    assert r["ok"], r
    print("reconcile ok:", r["ok"])
    print("E2E PASS")


if __name__ == "__main__":
    main()
