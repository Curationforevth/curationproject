# Fix parse_reasons Test Regression (KI-001) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `tests/test_reason_extractor.py::test_parse_reasons_valid` 와 `test_parse_reasons_filters_empty` 가 항상 빨강으로 나오는 CI noise 제거.

**Architecture:** `parse_reasons()` 가 평탄 string list 대신 `[{"reason": "...", "evidence": "..."}]` dict list 를 반환하도록 진화했지만 단위 테스트가 옛 형태를 기대하는 상태. 실제 caller 가 어떤 shape 을 쓰는지 확인 후 테스트를 새 구조에 맞춰 업데이트.

**Tech Stack:** Python 3 + pytest

**참고:**
- `docs/superpowers/known-issues.md` KI-001
- `scripts/reason_extractor.py::parse_reasons` (실제 구현)
- `tests/test_reason_extractor.py` (깨진 테스트)

**검증된 사실 (2026-04-09):**
- 실행: `python3 -m pytest tests/test_reason_extractor.py -v`
- 에러: `AssertionError: [{'evidence': '', 'reason': '이유 하나'}, ...] == ['이유 하나', ...]`
- `parse_reasons` 가 dict list 를 반환 중

---

## File Structure

**Modify:**
- `tests/test_reason_extractor.py` — 2개 테스트 (`test_parse_reasons_valid`, `test_parse_reasons_filters_empty`) 를 새 dict 형태에 맞춤

**Do NOT modify:**
- `scripts/reason_extractor.py::parse_reasons` (dict 반환이 올바른 동작)

---

## Task 1: parse_reasons 실제 shape 확인

**Files:**
- Read: `scripts/reason_extractor.py::parse_reasons`
- Read: 호출부 (`scripts/reason_extractor.py` 내 caller)

**Why:** 테스트 수정 전에 실제 caller 가 "reason" 필드만 쓰는지, evidence 도 쓰는지, 필터 규칙은 어떤지 확인. 추측 금지.

- [ ] **Step 1: parse_reasons 구현 읽기**

`scripts/reason_extractor.py` 에서 `def parse_reasons(` 함수를 찾고 전체 body 를 읽는다. 반환 shape 을 정확히 기록.

- [ ] **Step 2: parse_reasons 의 caller 찾기**

```bash
cd "/Users/eden.huh/Library/Mobile Documents/iCloud~md~obsidian/Documents/Second Brain/00 Inbox/curation"
grep -n "parse_reasons(" scripts/reason_extractor.py
```
모든 caller 를 읽고, 반환값의 어떤 필드를 실제로 사용하는지 확인.

- [ ] **Step 3: 현재 필터 규칙 확인**

`parse_reasons` 가 어떤 입력을 걸러내는지 — 빈 문자열? `None`? 공백만 있는 reason? dict 구조에선 어떤 키 누락을 걸러내는지 확인.

---

## Task 2: test_parse_reasons_valid 수정 (TDD)

**Files:**
- Modify: `tests/test_reason_extractor.py::test_parse_reasons_valid`

**Why:** 평탄 string 비교 대신 dict 구조 비교로 변경.

- [ ] **Step 1: 기존 테스트 확인**

```bash
python3 -m pytest tests/test_reason_extractor.py::test_parse_reasons_valid -v
```
Expected: FAIL with `[{'evidence': ..., 'reason': ...}] != ['이유 하나', ...]`

- [ ] **Step 2: 테스트 수정**

`test_parse_reasons_valid` 를 **Task 1 에서 확인한 실제 shape** 으로 수정. 예시 (Task 1 결과 반영 필요):

```python
def test_parse_reasons_valid():
    raw = {"reasons": [
        {"reason": "이유 하나", "evidence": "본문 근거 1"},
        {"reason": "이유 둘", "evidence": "본문 근거 2"},
        {"reason": "이유 셋", "evidence": ""},
    ]}
    result = parse_reasons(raw)
    assert len(result) == 3
    assert result[0]["reason"] == "이유 하나"
    assert result[0]["evidence"] == "본문 근거 1"
    assert result[2]["reason"] == "이유 셋"
```

**주의:** Task 1 에서 `parse_reasons` 가 string 리스트를 입력으로 받는지 dict 리스트를 받는지 확인해서 맞춘다.

- [ ] **Step 3: 테스트 pass 확인**

```bash
python3 -m pytest tests/test_reason_extractor.py::test_parse_reasons_valid -v
```
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add tests/test_reason_extractor.py
git commit -m "fix: test_parse_reasons_valid 를 dict 반환 형태에 맞춤"
```

---

## Task 3: test_parse_reasons_filters_empty 수정 (TDD)

**Files:**
- Modify: `tests/test_reason_extractor.py::test_parse_reasons_filters_empty`

**Why:** 빈 reason 필터 규칙도 dict 구조에 맞춰야 함.

- [ ] **Step 1: 현재 실패 확인**

```bash
python3 -m pytest tests/test_reason_extractor.py::test_parse_reasons_filters_empty -v
```
Expected: FAIL

- [ ] **Step 2: 테스트 수정**

`parse_reasons` 의 필터 규칙 (Task 1 에서 확인) 에 맞게 재작성. 예시:

```python
def test_parse_reasons_filters_empty():
    raw = {"reasons": [
        {"reason": "이유 하나", "evidence": ""},
        {"reason": "", "evidence": "근거만 있음"},  # 빈 reason 은 필터
        {"reason": "  ", "evidence": "공백 reason 도 필터"},
        {"reason": "이유 둘", "evidence": "근거"},
    ]}
    result = parse_reasons(raw)
    assert len(result) == 2
    assert [r["reason"] for r in result] == ["이유 하나", "이유 둘"]
```

**주의:** Task 1 에서 필터 규칙이 reason 기준인지 evidence 포함인지 확인해야 함.

- [ ] **Step 3: 테스트 pass 확인**

```bash
python3 -m pytest tests/test_reason_extractor.py -v
```
Expected: 14/14 PASS (이전 12 pass + 2 fix)

- [ ] **Step 4: Commit**

```bash
git add tests/test_reason_extractor.py
git commit -m "fix: test_parse_reasons_filters_empty 를 dict 반환 형태에 맞춤"
```

---

## Task 4: known-issues.md KI-001 항목 제거

**Files:**
- Modify: `docs/superpowers/known-issues.md`

- [ ] **Step 1: KI-001 블록 삭제**

`docs/superpowers/known-issues.md` 에서 KI-001 섹션 전체 (Critical 버킷의 첫 항목) 를 삭제. Critical 버킷이 비면 `## 🔴 Critical` 헤더도 함께 삭제하거나 "(현재 없음)" 표기.

- [ ] **Step 2: Commit**

```bash
git add docs/superpowers/known-issues.md
git commit -m "docs: KI-001 해결 (parse_reasons 테스트 dict 형태로 수정)"
```

---

## Task 5: PR 생성

- [ ] **Step 1: Push**

```bash
# 활성 GitHub 계정 hyhuh0910 확인 (memory feedback_git_push.md)
gh auth switch -u hyhuh0910
git push -u origin fix/parse-reasons-test
```

- [ ] **Step 2: PR**

```bash
gh pr create --title "fix: parse_reasons 테스트 dict 반환 형태에 맞춤 (KI-001)" --body "..."
```

---

## Self-Review

**Spec coverage:**
- parse_reasons 구현 읽기 ✅ Task 1
- test_parse_reasons_valid 수정 ✅ Task 2
- test_parse_reasons_filters_empty 수정 ✅ Task 3
- KI-001 known-issues 정리 ✅ Task 4
- PR ✅ Task 5

**주의 사항:**
- Task 1 에서 실제 shape 을 확인 후 Task 2/3 의 예시 코드를 그 shape 에 맞춰야 함 — plan 안의 예시는 "dict list" 가정이지만 실제 구현이 다를 수 있다 (예: `reason` 필드명이 다르거나 중첩 구조일 수도).
- `parse_reasons` 자체는 수정하지 않는다 — 테스트만 맞춘다.
- 예상 소요: 30분 이하. XS task.
