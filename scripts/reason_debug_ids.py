"""reason 추출 품질 디버그 — 특정 book_id로 테스트"""
import os, re, json, sys
from dotenv import load_dotenv
from supabase import create_client
load_dotenv()

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from lib.openai_helpers import call_chat
from scripts.reason_extractor import (
    build_step1_prompt, build_step2_prompt,
    filter_generic_reasons, extract_key_terms,
)

sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])

book_ids = sys.argv[1:]
if not book_ids:
    print("Usage: python3 reason_debug_ids.py <book_id1> <book_id2> ...")
    sys.exit(1)

for bid in book_ids:
    result = sb.table("books").select(
        "id, title, genre, description, rich_description"
    ).eq("id", bid).single().execute()
    book = result.data

    title = book["title"]
    genre = book.get("genre", "")
    desc = book.get("description", "")
    rich = book.get("rich_description")
    if rich:
        clean_rich = re.sub(r"<[^>]+>", "", rich)
        if len(clean_rich) > len(desc or ""):
            desc = clean_rich

    lib_kw = extract_key_terms(book.get("rich_description"))

    # Step 1
    p1 = build_step1_prompt(title, genre, desc, lib_kw)
    r1 = call_chat(p1, temperature=0)
    kw = r1.get("keywords", [])

    # Step 2
    p2 = build_step2_prompt(title, genre, kw)
    r2 = call_chat(p2, temperature=0)
    reasons = r2.get("reasons", [])
    reasons = [r for r in reasons if isinstance(r, str) and r.strip()]
    filtered = filter_generic_reasons(reasons)

    # Diff
    kw_set = set(kw)
    reason_set = set(filtered)
    removed = kw_set - reason_set
    added = reason_set - kw_set
    kept = kw_set & reason_set

    print(f"\n{'='*60}")
    print(f"📖 {title} ({genre})")
    print(f"{'='*60}")
    print(f"\n[Step1] 키워드 {len(kw)}개:")
    for k in kw:
        marker = "✓" if k in kept else "✗"
        print(f"  {marker} {k}")
    print(f"\n[Step2] Reasons {len(filtered)}개 (필터 전 {len(reasons)}개):")
    for r in filtered:
        marker = "★" if r in added else "·"
        print(f"  {marker} {r}")
    print(f"\n[Diff]")
    print(f"  유지: {len(kept)}개 | 제거: {len(removed)}개 | 새로 생성: {len(added)}개")
    if added:
        print(f"  ⚠️  Step2에서 새로 만든 것:")
        for a in added:
            print(f"     → {a}")
    merged = [r for r in filtered if any(c in r for c in ["와 ", "과 ", "을 통한", "를 통한"]) and len(r) > 15]
    if merged:
        print(f"  ⚠️  뭉침 의심:")
        for m in merged:
            print(f"     → {m}")
