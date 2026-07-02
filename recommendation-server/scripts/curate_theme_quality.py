"""recommendation-server/scripts/curate_theme_quality.py

keyword 큐레이션 테마 LLM 품질 게이트 — 심사(keep/kill) + 리라이트.

배경(2026-07-02, Eden 승인): 활성 954개 테마 중 874개(92%)가 library_keywords
빈도 기반 무정제 keyword 테마("그대", "마음에", "정보" 같은 형태소 조각 +
"~관련 책들" 템플릿 설명)라, 큐레이션 회전(가중 랜덤 + 7일 제외)이 정상인데도
매번 다 똑같아 보였다(perceived variety 붕괴). 홈의 큐레이션은 취향 발견
가치의 관문이므로 "정제된 소수 > 무의미한 다수".

- kill: is_active=false (가역 — 행 삭제 없음, theme_key/target_keyword 보존)
- keep: 큐레이션다운 title + 1문장 description 으로 리라이트
- 미정제 판별: description 이 생성 템플릿("~ 관련 책들") 그대로인 행만 대상
  → 자연 증분(재실행 시 이미 리라이트된 행 skip). 주간 워크플로 후속 스텝으로
  신규 keyword 만 계속 정제된다(축적→정제 반복 원칙).

Eden feedback_batch_operations 준수: 배치별 try/except continue, sleep,
dry-run(-n 배치 샘플 미리보기, DB 무기록), 결과 카운트/샘플 로깅.

사용법:
  python scripts/curate_theme_quality.py --dry-run       # 2배치 샘플 미리보기
  python scripts/curate_theme_quality.py                 # 전체 실행
"""
from __future__ import annotations

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests

from config import get_supabase, OPENAI_API_KEY

CHAT_MODEL = "gpt-4o-mini"   # scripts/lib/openai_helpers.py 관례와 동일
BATCH_SIZE = 40
SLEEP_BETWEEN_CALLS = 1.0
UNREFINED_DESC_SUFFIX = " 관련 책들"
TITLE_MIN, TITLE_MAX = 2, 24

PROMPT = """너는 도서 앱의 큐레이션 편집자다. 아래 키워드들은 도서관 키워드 데이터에서 자동 추출된 것이고, 각 키워드로 홈 화면의 책 선반(큐레이션)을 만들려 한다.

각 키워드를 심사하라:
- "kill": 선반 제목으로 무의미한 것 — 형태소 조각이나 조사가 붙은 꼴("마음에", "시가"), 기능어·무맥락 추상어("정보", "과정", "모습", "자료", "공통점"), 서지·출판 용어 등. **확신이 없으면 kill** (정제된 소수가 낫다).
- "keep": 독자가 주제/장르/정서/소재로 바로 이해하는 단어("기묘", "우주", "요리", "고양이", "불안"). keep 이면 함께 제공:
  - "title": 큐레이션다운 짧은 한국어 제목 (8~18자, 담백하게. 과장·이모지·물음표 남발 금지. 키워드의 의미를 유지. "다양한 ~", "~의 세계", "~ 모음" 같은 상투 표현을 반복하지 말고 제목마다 결을 다르게)
  - "description": 어떤 책들이 모였는지 한 문장 (40자 이내, "다양한 책들" 같은 동어반복 금지)

JSON 만 출력한다:
{"results": [{"keyword": "...", "verdict": "keep", "title": "...", "description": "..."}, {"keyword": "...", "verdict": "kill"}]}

키워드 목록:
"""


def is_unrefined(theme: dict) -> bool:
    """생성 템플릿 그대로인(아직 심사 안 된) 테마인가.

    generate_curation_themes 가 만드는 형태: title == keyword,
    description == f"{keyword} 관련 책들". 리라이트되면 둘 다 벗어나므로
    재실행 시 자연스럽게 skip 된다(증분).
    """
    kw = theme.get("target_keyword") or ""
    return bool(kw) and (theme.get("description") or "") == f"{kw}{UNREFINED_DESC_SUFFIX}"


def validate_results(keywords: list[str], parsed: dict) -> tuple[dict, list[str]]:
    """LLM 응답 검증 — {keyword: action} 과 오류 목록을 반환.

    action = {"verdict": "kill"} | {"verdict": "keep", "title": ..., "description": ...}
    검증 실패 키워드는 결과에서 제외(미처리로 남아 다음 실행에서 재시도).
    """
    actions: dict = {}
    errors: list[str] = []
    results = parsed.get("results")
    if not isinstance(results, list):
        return {}, ["results 리스트 없음"]
    known = set(keywords)
    for r in results:
        kw = r.get("keyword")
        if kw not in known:
            errors.append(f"미요청 키워드 무시: {kw!r}")
            continue
        verdict = r.get("verdict")
        if verdict == "kill":
            actions[kw] = {"verdict": "kill"}
        elif verdict == "keep":
            title = (r.get("title") or "").strip()
            desc = (r.get("description") or "").strip()
            if not (TITLE_MIN <= len(title) <= TITLE_MAX) or not desc:
                errors.append(f"keep 인데 title/description 불량: {kw!r} title={title!r}")
                continue
            actions[kw] = {"verdict": "keep", "title": title, "description": desc}
        else:
            errors.append(f"알 수 없는 verdict: {kw!r} → {verdict!r}")
    for kw in keywords:
        if kw not in actions and not any(kw in e for e in errors):
            errors.append(f"응답 누락: {kw!r}")
    return actions, errors


def _judge_batch(keywords: list[str]) -> dict:
    """한 배치 LLM 호출 → 검증된 {keyword: action}."""
    resp = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {OPENAI_API_KEY}",
                 "Content-Type": "application/json"},
        json={
            "model": CHAT_MODEL,
            "temperature": 0.3,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "user",
                          "content": PROMPT + "\n".join(f"- {k}" for k in keywords)}],
        },
        timeout=60,
    )
    resp.raise_for_status()
    parsed = json.loads(resp.json()["choices"][0]["message"]["content"])
    actions, errors = validate_results(keywords, parsed)
    for e in errors:
        print(f"    [validate] {e}")
    return actions


def main(dry_run: bool = False):
    sb = get_supabase()
    rows = sb.table("curation_themes").select(
        "id,target_keyword,title,description"
    ).eq("theme_type", "keyword").eq("is_active", True).execute().data or []
    todo = [r for r in rows if is_unrefined(r)]
    print(f"[theme_quality] active keyword themes: {len(rows)}, 미정제: {len(todo)}, "
          f"dry_run={dry_run}")
    if not todo:
        print("  정제 대상 없음 — 종료")
        return

    batches = [todo[i:i + BATCH_SIZE] for i in range(0, len(todo), BATCH_SIZE)]
    if dry_run:
        batches = batches[:2]
        print(f"  dry-run: {len(batches)}배치만 샘플 심사(DB 무기록)")

    kept = killed = failed = 0
    keep_samples: list[str] = []
    for bi, batch in enumerate(batches):
        kws = [b["target_keyword"] for b in batch]
        by_kw = {b["target_keyword"]: b for b in batch}
        try:
            actions = _judge_batch(kws)
        except Exception as e:
            print(f"  [batch {bi+1}/{len(batches)}] LLM 실패 — skip: {e}")
            failed += len(kws)
            time.sleep(SLEEP_BETWEEN_CALLS)
            continue

        for kw, act in actions.items():
            theme = by_kw[kw]
            try:
                if act["verdict"] == "kill":
                    if not dry_run:
                        sb.table("curation_themes").update(
                            {"is_active": False}).eq("id", theme["id"]).execute()
                    killed += 1
                else:
                    if not dry_run:
                        sb.table("curation_themes").update(
                            {"title": act["title"], "description": act["description"]}
                        ).eq("id", theme["id"]).execute()
                    kept += 1
                    if len(keep_samples) < 20:
                        keep_samples.append(f"{kw} → {act['title']} | {act['description']}")
            except Exception as e:
                print(f"  [apply 실패] {kw}: {e}")
                failed += 1
        failed += len(kws) - len(actions)
        print(f"  [batch {bi+1}/{len(batches)}] keep={kept} kill={killed} fail={failed}")
        time.sleep(SLEEP_BETWEEN_CALLS)

    print(f"\n완료 — keep(리라이트) {kept} / kill(비활성) {killed} / 미처리 {failed}"
          f"{' (dry-run: DB 무기록)' if dry_run else ''}")
    print("keep 샘플:")
    for s in keep_samples:
        print(f"  {s}")


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    main(dry_run=args.dry_run)
