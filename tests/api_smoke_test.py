"""
Standalone API smoke test for DevOS.
Run with: python tests/api_smoke_test.py
"""

import uuid
import httpx

BASE_URL = "http://127.0.0.1:8000"
TASKS_URL = f"{BASE_URL}/api/v1/tasks"
AUTH_URL = f"{BASE_URL}/api/v1/auth"

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"


def check(label: str, condition: bool, details: str = "") -> bool:
    status = PASS if condition else FAIL
    print(f"[{status}] {label}")
    if details:
        print(f"       {details}")
    return condition


def run_tests() -> int:
    failures = 0
    token: str = ""
    task_id: str = ""

    email = f"smoke+{uuid.uuid4().hex[:8]}@test.example"
    password = "Smoke1234!"

    client = httpx.Client(base_url=BASE_URL, timeout=10.0)

    # ── 1. Signup ─────────────────────────────────────────────────────────────
    resp = client.post(f"{AUTH_URL}/signup", json={"email": email, "password": password})
    ok = resp.status_code == 200
    if not check("1. Signup", ok, f"status={resp.status_code}  body={resp.text[:200]}"):
        failures += 1
        print("  Cannot continue without a user — aborting.")
        return failures
    token = resp.json()["token"]
    user_id = resp.json()["user"]["id"]
    print(f"       email={email}  user_id={user_id}")

    headers = {"Authorization": f"Bearer {token}"}

    # ── 2. Login ───────────────────────────────────────────────────────────────
    resp = client.post(f"{AUTH_URL}/login", json={"email": email, "password": password})
    ok = resp.status_code == 200
    if not check("2. Login", ok, f"status={resp.status_code}  body={resp.text[:200]}"):
        failures += 1
    else:
        token = resp.json()["token"]
        headers = {"Authorization": f"Bearer {token}"}
        print(f"       new token captured")

    # ── 3. Session ─────────────────────────────────────────────────────────────
    resp = client.get(f"{AUTH_URL}/session", headers=headers)
    ok = resp.status_code == 200 and resp.json().get("user", {}).get("id") == user_id
    if not check("3. Session (token valid)", ok, f"status={resp.status_code}  body={resp.text[:200]}"):
        failures += 1

    # ── 4. Create task ─────────────────────────────────────────────────────────
    task_payload = {
        "title": "Smoke test task",
        "priority": "high",
        "status": "to-do",
        "tags": ["smoke", "automated"],
        "notes": "Created by api_smoke_test.py",
    }
    resp = client.post(TASKS_URL, json=task_payload, headers=headers)
    ok = resp.status_code == 201
    if not check("4. Create task", ok, f"status={resp.status_code}  body={resp.text[:200]}"):
        failures += 1
        print("  Cannot continue without a task — aborting task steps.")
        return failures
    task_id = resp.json()["task"]["id"]
    print(f"       task_id={task_id}")

    # ── 5. List tasks — verify created task appears ────────────────────────────
    resp = client.get(TASKS_URL, headers=headers)
    ok = resp.status_code == 200
    if ok:
        tasks = resp.json().get("tasks", [])
        found = any(t["id"] == task_id for t in tasks)
        ok = found
        detail = f"status={resp.status_code}  total={resp.json().get('total')}  found={found}"
    else:
        detail = f"status={resp.status_code}  body={resp.text[:200]}"
    if not check("5. List tasks (created task present)", ok, detail):
        failures += 1

    # ── 6. Get task by ID ──────────────────────────────────────────────────────
    resp = client.get(f"{TASKS_URL}/{task_id}", headers=headers)
    ok = resp.status_code == 200 and resp.json().get("task", {}).get("id") == task_id
    if not check("6. Get task by ID", ok, f"status={resp.status_code}  body={resp.text[:200]}"):
        failures += 1

    # ── 7. Update task title ───────────────────────────────────────────────────
    resp = client.patch(f"{TASKS_URL}/{task_id}", json={"title": "Updated smoke task"}, headers=headers)
    ok = resp.status_code == 200 and resp.json().get("task", {}).get("title") == "Updated smoke task"
    if not check("7. Update task title", ok, f"status={resp.status_code}  body={resp.text[:200]}"):
        failures += 1

    # ── 8. Update task status ──────────────────────────────────────────────────
    resp = client.patch(f"{TASKS_URL}/{task_id}/status", json={"status": "in-progress"}, headers=headers)
    ok = resp.status_code == 200 and resp.json().get("task", {}).get("status") == "in-progress"
    if not check("8. Update task status", ok, f"status={resp.status_code}  body={resp.text[:200]}"):
        failures += 1

    # ── 9. Delete task ─────────────────────────────────────────────────────────
    resp = client.delete(f"{TASKS_URL}/{task_id}", headers=headers)
    ok = resp.status_code == 204
    if not check("9. Delete task", ok, f"status={resp.status_code}  body={resp.text[:200]}"):
        failures += 1

    # ── 10. Verify task is gone ────────────────────────────────────────────────
    resp = client.get(TASKS_URL, headers=headers)
    if resp.status_code == 200:
        tasks = resp.json().get("tasks", [])
        still_present = any(t["id"] == task_id for t in tasks)
        ok = not still_present
        detail = f"status={resp.status_code}  total={resp.json().get('total')}  still_present={still_present}"
    else:
        ok = False
        detail = f"status={resp.status_code}  body={resp.text[:200]}"
    if not check("10. Task gone from list after delete", ok, detail):
        failures += 1

    # ── Also verify GET by ID returns 404 ────────────────────────────────────
    resp = client.get(f"{TASKS_URL}/{task_id}", headers=headers)
    ok = resp.status_code == 404
    if not check("10b. Get deleted task → 404", ok, f"status={resp.status_code}  body={resp.text[:200]}"):
        failures += 1

    client.close()
    return failures


if __name__ == "__main__":
    print(f"\n{'='*55}")
    print("  DevOS API Smoke Test")
    print(f"  Target: {BASE_URL}")
    print(f"{'='*55}\n")

    failures = run_tests()

    print(f"\n{'='*55}")
    if failures == 0:
        print(f"  \033[32mAll tests passed.\033[0m")
    else:
        print(f"  \033[31m{failures} test(s) failed.\033[0m")
    print(f"{'='*55}\n")

    raise SystemExit(1 if failures else 0)
