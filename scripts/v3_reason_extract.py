"""v3 Reason 추출 — 맥락 보존 reason (15~40자) 전량 추출

v3 설계 기준:
- 1단계 프롬프트로 "이 책을 좋아할 이유" 5~8개 추출
- source='v3_context_rich'로 저장 (기존 llm_extracted 유지)
- 이미 v3 reason이 있는 책은 스킵
- 100권 마다 체크포인트 (샘플 출력 + 자동 일시정지)

사용법:
  python3 -u scripts/v3_reason_extract.py              # 전체
  python3 -u scripts/v3_reason_extract.py --limit 100  # 100권만
  python3 -u scripts/v3_reason_extract.py --checkpoint  # 100권마다 일시정지 (기본 ON)
  python3 -u scripts/v3_reason_extract.py --no-checkpoint  # 일시정지 없이 전체 실행
"""
import argparse
import json
import json as _json
import os
import re
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.stdout.reconfigure(line_buffering=True) if hasattr(sys.stdout, 'reconfigure') else None
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

from supabase import create_client
from scripts.lib.openai_helpers import call_chat, call_embedding
from scripts.lib.retry import with_retry

# ── 설정 ──
SOURCE_TAG = "v3_context_rich"
CHUNK_SIZE = 20          # 한 번에 처리할 권수
SLEEP_BETWEEN = 2        # chunk 사이 대기 (초)
LLM_WORKERS = 10         # LLM 병렬 호출
EMBED_BATCH = 20         # 임베딩 배치 크기
INSERT_BATCH = 20        # DB insert 배치 크기
MAX_CONSECUTIVE_ERRORS = 3  # 연속 에러 N회 → 자동 중단
CHECKPOINT_INTERVAL = 100   # N권마다 품질 체크
CHECKPOINT_FILE = os.path.join(os.path.dirname(__file__), ".checkpoint_v3_reason.json")


def make_client():
    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])


# ── v3 프롬프트 (설계 문서 섹션 5) ──

def build_v3_prompt(title, genre, description):
    """v3 reason 추출 프롬프트 — 15~40자 맥락 보존 명사구."""
    return f"""이 책을 좋아할 독자 관점에서 "좋아할 이유" 5~8개를 추출해주세요.

## 규칙
- 각 이유는 15~40자의 구체적 명사구/설명구
- 이 책만의 맥락이 담겨야 함 (다른 책에 붙여넣을 수 없어야 함)
- 고유명사(캐릭터명, 저자명), 평론 표현("걸작", "몰입감"), 범용 태그("성장", "감동") 제외
- 책 제목, 소제목, 챕터명 제외
- 마케팅 문구("베스트셀러", "최초 완역") 제외

## 좋은 예시
- "범죄 피해자 가족의 고통을 통해 사법 시스템의 모순을 드러냄" (O)
- "진화심리학으로 현대인의 일상 습관을 재해석하는 과학 에세이" (O)
- "이종 간 공생 관계 속에서 정체성을 탐구하는 생물학적 SF" (O)

## 나쁜 예시
- "사회파 미스터리" (X — 범용 태그, 맥락 없음)
- "성장 소설" (X — 범용)
- "감동적인 이야기" (X — 평론)

도서: {title} ({genre or '장르 미분류'})
설명: {description}

JSON: {{"reasons": ["이유1", "이유2", ...]}}"""


# ── 후처리 ──

def filter_v3_reasons(reasons, title):
    """v3 reason 후처리: 길이/형식 검증."""
    filtered = []
    title_parts = set(re.split(r"\s*[-–—:]\s*", title))
    for r in reasons:
        if not isinstance(r, str):
            continue
        r = r.strip()
        # 너무 짧거나 너무 긴 것 제외
        if len(r) < 10 or len(r) > 60:
            continue
        # 책 제목 자체인 것 제외
        if r in title_parts or r == title:
            continue
        # 범용 태그 제외 (10자 미만이면 범용일 가능성 높음)
        if len(r) < 15:
            generic = ["성장", "감동", "몰입감", "흡입력", "걸작", "수작",
                        "재미", "감성", "힐링", "위로", "공감", "소장"]
            if any(g in r for g in generic):
                continue
        filtered.append(r)
    return filtered


# ── 추출 파이프라인 ──

def extract_v3_reasons(book):
    """단일 책에서 v3 reason 추출."""
    title = book.get("title", "")
    genre = book.get("genre", "")
    rich_desc = book.get("rich_description", "")
    desc = book.get("description", "")

    # rich_description 우선, HTML 제거, 1500자 제한
    if rich_desc:
        clean = re.sub(r"<[^>]+>", "", rich_desc)
        if len(clean) > len(desc or ""):
            desc = clean
    desc = (desc or "")[:1500]

    if not desc or len(desc) < 50:
        return None

    prompt = build_v3_prompt(title, genre, desc)
    raw = call_chat(prompt, temperature=0)
    reasons = raw.get("reasons", [])
    if not isinstance(reasons, list):
        return None

    reasons = filter_v3_reasons(reasons, title)
    return reasons if reasons else None


def embed_and_save(sb, book_reasons_map):
    """reason 임베딩 + DB 저장. book_reasons_map: {book_id: (book, reasons)}"""
    all_reasons = []
    reason_map = []  # book_id per reason
    for book_id, (book, reasons) in book_reasons_map.items():
        for r in reasons:
            all_reasons.append(r)
            reason_map.append(book_id)

    if not all_reasons:
        return 0, 0

    # 임베딩 (EMBED_BATCH씩, 실패 시 1회만 재시도 후 스킵)
    all_embeddings = [None] * len(all_reasons)
    for i in range(0, len(all_reasons), EMBED_BATCH):
        chunk = all_reasons[i:i + EMBED_BATCH]
        for attempt in range(2):  # 최대 2회 (원본 + 1회 재시도)
            try:
                embs = call_embedding(chunk)
                for j, emb in enumerate(embs):
                    all_embeddings[i + j] = emb
                break
            except Exception as e:
                if attempt == 0:
                    print(f"  ⚠ 임베딩 재시도 ({i}~{i+len(chunk)}): {e}", flush=True)
                    time.sleep(2)
                else:
                    print(f"  ✗ 임베딩 스킵 ({i}~{i+len(chunk)}): {e}", flush=True)

    valid = [(all_reasons[i], all_embeddings[i], reason_map[i])
             for i in range(len(all_reasons)) if all_embeddings[i] is not None]

    skipped = len(all_reasons) - len(valid)
    if skipped > 0:
        print(f"  ⚠ 임베딩 실패로 {skipped}/{len(all_reasons)}건 스킵", flush=True)

    if not valid:
        return 0, len(all_reasons)

    # DB insert (INSERT_BATCH씩)
    rows = [{
        "book_id": book_id,
        "reason": reason,
        "reason_embedding": emb,
        "source": SOURCE_TAG,
    } for reason, emb, book_id in valid]

    saved, failed = 0, 0
    for i in range(0, len(rows), INSERT_BATCH):
        chunk = rows[i:i + INSERT_BATCH]
        try:
            with_retry(lambda c=chunk: sb.table("book_love_reasons").upsert(
                           c, on_conflict="book_id,source,reason",
                           ignore_duplicates=True).execute(),
                       max_retries=2, base_delay=1.0)
            saved += len(chunk)
        except Exception:
            # 배치 실패 → 1건씩 재시도
            for row in chunk:
                try:
                    with_retry(lambda r=row: sb.table("book_love_reasons").upsert(
                                   r, on_conflict="book_id,source,reason",
                                   ignore_duplicates=True).execute(),
                               max_retries=2, base_delay=1.0)
                    saved += 1
                except Exception as e:
                    print(f"  ✗ insert 실패: {e}", flush=True)
                    failed += 1
            time.sleep(1)

    return saved, failed


# ── 체크포인트 (자동 품질 검증) ──

# 품질 기준
QC_MIN_AVG_LENGTH = 15       # 평균 길이 최소
QC_MAX_SHORT_RATIO = 0.10    # 15자 미만 비율 최대 10%
QC_MIN_AVG_REASONS = 4.0     # 권당 평균 reason 수 최소
QC_MAX_ERROR_RATIO = 0.15    # 에러율 최대 15%

GENERIC_WORDS = [
    "성장", "감동", "몰입감", "흡입력", "걸작", "수작", "재미있",
    "좋은 책", "추천", "인상적", "명작", "소장 가치",
]


def run_checkpoint(sb, checkpoint_num, total_done, total_saved, total_errors):
    """100권 체크포인트: 자동 품질 검증. 통과=True, 실패=False."""
    print(f"\n{'='*60}", flush=True)
    print(f"  체크포인트 #{checkpoint_num} — {total_done}권 완료", flush=True)
    print(f"{'='*60}", flush=True)

    issues = []

    # 1) 에러율 체크
    error_ratio = total_errors / total_done if total_done > 0 else 0
    if error_ratio > QC_MAX_ERROR_RATIO:
        issues.append(f"에러율 {error_ratio:.0%} > {QC_MAX_ERROR_RATIO:.0%}")

    # 2) 권당 평균 reason 수
    avg_per_book = total_saved / total_done if total_done > 0 else 0
    if avg_per_book < QC_MIN_AVG_REASONS:
        issues.append(f"권당 평균 {avg_per_book:.1f}개 < {QC_MIN_AVG_REASONS}")

    # 3) 최근 200건 reason 길이/내용 분석
    try:
        res = sb.table("book_love_reasons") \
            .select("book_id, reason") \
            .eq("source", SOURCE_TAG) \
            .order("created_at", desc=True) \
            .limit(200) \
            .execute()

        if res.data:
            reasons = [r["reason"] for r in res.data]
            lengths = [len(r) for r in reasons]
            avg_len = sum(lengths) / len(lengths)
            short_count = sum(1 for l in lengths if l < 15)
            short_ratio = short_count / len(reasons)

            # 길이 체크
            if avg_len < QC_MIN_AVG_LENGTH:
                issues.append(f"평균 길이 {avg_len:.0f}자 < {QC_MIN_AVG_LENGTH}자")
            if short_ratio > QC_MAX_SHORT_RATIO:
                issues.append(f"15자 미만 {short_ratio:.0%} > {QC_MAX_SHORT_RATIO:.0%}")

            # 범용 표현 체크
            generic_count = sum(1 for r in reasons
                               if any(g in r for g in GENERIC_WORDS))
            generic_ratio = generic_count / len(reasons)
            if generic_ratio > 0.05:
                issues.append(f"범용 표현 {generic_ratio:.0%} (5% 초과)")

            # 중복 체크 (같은 reason이 3번 이상 → 프롬프트 문제)
            dupes = [r for r, c in Counter(reasons).items() if c >= 3]
            if dupes:
                issues.append(f"중복 reason {len(dupes)}개: {dupes[:3]}")

            # 샘플 3권 출력
            by_book = {}
            for r in res.data:
                by_book.setdefault(r["book_id"], []).append(r["reason"])
            shown = 0
            for book_id, book_reasons in by_book.items():
                if shown >= 3:
                    break
                try:
                    bres = sb.table("books").select("title, genre") \
                        .eq("id", book_id).limit(1).execute()
                    title = bres.data[0]["title"] if bres.data else book_id[:8]
                    genre = bres.data[0].get("genre", "") if bres.data else ""
                except Exception:
                    title, genre = book_id[:8], ""
                print(f"\n  [{title[:35]}] ({genre[:30]})", flush=True)
                for r in book_reasons[:4]:
                    print(f"    → {r}", flush=True)
                shown += 1

            print(f"\n  --- 통계 ---", flush=True)
            print(f"  평균 길이: {avg_len:.0f}자 | 15자 미만: {short_count}/{len(reasons)} "
                  f"| 범용: {generic_count}/{len(reasons)} | 권당 평균: {avg_per_book:.1f}개", flush=True)

    except Exception as e:
        print(f"  분석 실패: {e}", flush=True)
        issues.append(f"분석 실패: {e}")

    # 판정
    if issues:
        print(f"\n  ⛔ 품질 이슈 발견:", flush=True)
        for issue in issues:
            print(f"    - {issue}", flush=True)
        print(f"{'='*60}\n", flush=True)
        return False
    else:
        print(f"\n  ✅ 품질 검증 통과", flush=True)
        print(f"{'='*60}\n", flush=True)
        return True


def load_reason_checkpoint():
    """체크포인트 파일에서 완료된 book_id 목록 로드."""
    if os.path.exists(CHECKPOINT_FILE):
        with open(CHECKPOINT_FILE) as f:
            data = _json.load(f)
            print(f"  체크포인트 로드: {len(data.get('done_ids', []))}건", flush=True)
            return set(data.get("done_ids", []))
    return set()


def save_reason_checkpoint(done_ids):
    """처리 완료된 book_id를 체크포인트 파일에 저장."""
    with open(CHECKPOINT_FILE, "w") as f:
        _json.dump({"done_ids": list(done_ids), "last_updated": time.strftime("%Y-%m-%dT%H:%M:%S")}, f)


# ── 메인 루프 ──

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--checkpoint", action="store_true", default=True)
    parser.add_argument("--no-checkpoint", action="store_true")
    args = parser.parse_args()
    do_checkpoint = not args.no_checkpoint

    sb = make_client()

    # 1) 전체 대상 수집 (rich_description 있는 책)
    print("1) 전체 대상 수집...", flush=True)
    all_ids = []
    offset = 0
    while True:
        for attempt in range(3):
            try:
                res = sb.table("books").select("id") \
                    .not_.is_("rich_description", "null") \
                    .range(offset, offset + 999).execute()
                break
            except Exception as e:
                print(f"  수집 재시도 {attempt+1}/3: {e}", flush=True)
                time.sleep(5)
                sb = make_client()
        else:
            break
        if not res.data:
            break
        all_ids.extend(r["id"] for r in res.data)
        if len(res.data) < 1000:
            break
        offset += 1000

    print(f"  전체: {len(all_ids)}권", flush=True)

    # 2) 이미 v3 처리된 book_id 스킵
    print("2) v3 처리 완료 확인...", flush=True)
    done_ids = set()
    offset = 0
    while True:
        for attempt in range(3):
            try:
                res = sb.table("book_love_reasons").select("book_id") \
                    .eq("source", SOURCE_TAG) \
                    .range(offset, offset + 999).execute()
                break
            except Exception as e:
                print(f"  조회 재시도 {attempt+1}/3: {e}", flush=True)
                time.sleep(5)
                sb = make_client()
        else:
            break
        if not res.data:
            break
        done_ids.update(r["book_id"] for r in res.data)
        if len(res.data) < 1000:
            break
        offset += 1000

    checkpoint_ids = load_reason_checkpoint()
    done_ids = done_ids | checkpoint_ids

    ids = [i for i in all_ids if i not in done_ids]
    if args.limit:
        ids = ids[:args.limit]

    print(f"  v3 완료: {len(done_ids)}권 스킵", flush=True)
    print(f"  대상: {len(ids)}권\n", flush=True)

    if not ids:
        print("모든 책이 이미 v3 처리되었습니다.", flush=True)
        return 0

    # 3) 처리 시작
    start = time.time()
    total_done, total_errors, total_saved = 0, 0, 0
    consecutive_errors = 0
    checkpoint_num = 0

    for i in range(0, len(ids), CHUNK_SIZE):
        chunk_ids = ids[i:i + CHUNK_SIZE]

        # 상세 조회
        try:
            res = with_retry(lambda cids=chunk_ids: sb.table("books")
                .select("id, title, genre, description, rich_description")
                .in_("id", cids).execute())
            books = res.data or []
        except Exception as e:
            print(f"  ✗ 상세조회 실패: {e}", flush=True)
            total_errors += len(chunk_ids)
            consecutive_errors += 1
            if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                print(f"\n⛔ 연속 {MAX_CONSECUTIVE_ERRORS}회 에러 — 자동 중단", flush=True)
                break
            time.sleep(10)
            sb = make_client()
            continue

        if not books:
            continue

        # LLM 추출 (병렬)
        extracted = {}
        chunk_errors = 0
        with ThreadPoolExecutor(max_workers=LLM_WORKERS) as pool:
            futures = {pool.submit(extract_v3_reasons, b): b for b in books}
            for future in as_completed(futures):
                book = futures[future]
                try:
                    reasons = future.result(timeout=60)
                    if reasons:
                        extracted[book["id"]] = (book, reasons)
                    else:
                        chunk_errors += 1
                except Exception as e:
                    chunk_errors += 1
                    print(f"  ✗ [{book.get('title','?')[:20]}] LLM 실패: {e}", flush=True)

        # 임베딩 + 저장
        if extracted:
            saved, failed = embed_and_save(sb, extracted)
            total_saved += saved
            total_errors += failed
            consecutive_errors = 0  # 성공하면 리셋

            for book_id, (book, reasons) in extracted.items():
                print(f"  ✓ [{book['title'][:25]}] {len(reasons)}개", flush=True)
        else:
            consecutive_errors += 1

        total_done += len(books)
        total_errors += chunk_errors

        # 연속 에러 체크
        if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
            print(f"\n⛔ 연속 {MAX_CONSECUTIVE_ERRORS}회 에러 — 자동 중단", flush=True)
            break

        # 진행률
        elapsed = time.time() - start
        pct = (i + len(chunk_ids)) / len(ids) * 100
        rate = total_done / elapsed * 60 if elapsed > 0 else 0
        remaining = len(ids) - i - len(chunk_ids)
        eta = remaining / rate if rate > 0 else 0
        print(f"  [{pct:5.1f}%] {total_done}/{len(ids)}권 {total_saved}reasons "
              f"{total_errors}err {elapsed/60:.1f}분 ~{eta:.0f}분남음", flush=True)

        # 체크포인트 (100권마다) — 품질 실패 시 자동 중단
        if do_checkpoint and total_done > 0 and total_done % CHECKPOINT_INTERVAL == 0:
            checkpoint_num += 1
            processed_so_far = set(ids[:i + len(chunk_ids)])
            save_reason_checkpoint(done_ids | processed_so_far)
            passed = run_checkpoint(sb, checkpoint_num, total_done, total_saved, total_errors)
            if not passed:
                print("⛔ 품질 검증 실패 — 자동 중단. 위 이슈를 확인하세요.", flush=True)
                break
            print(">> 품질 검증 통과. 계속 진행합니다...\n", flush=True)

        # IO 분산 대기
        time.sleep(SLEEP_BETWEEN)

    # 최종 리포트
    elapsed = time.time() - start
    print(f"\n{'='*60}", flush=True)
    print(f"v3 Reason 추출 완료", flush=True)
    print(f"{'='*60}", flush=True)
    print(f"  처리: {total_done}권", flush=True)
    print(f"  저장: {total_saved}건", flush=True)
    print(f"  에러: {total_errors}건", flush=True)
    print(f"  소요: {elapsed/60:.1f}분", flush=True)
    print(f"{'='*60}", flush=True)

    if total_errors == 0 and os.path.exists(CHECKPOINT_FILE):
        os.remove(CHECKPOINT_FILE)
        print("  체크포인트 파일 삭제 (정상 완료)", flush=True)

    # 실패가 있으면 caller (cron, pipeline) 가 감지할 수 있도록 exit 1.
    return 1 if total_errors > 0 else 0


if __name__ == "__main__":
    sys.exit(main() or 0)
