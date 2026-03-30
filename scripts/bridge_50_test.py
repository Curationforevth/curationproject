"""브릿지 매칭 50권 테스트
다양한 장르/취향의 시나리오 5개, 각 시나리오별 top 10 = 50권
"""
import os, sys, numpy as np, time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

from lib.openai_helpers import call_chat, call_embedding
from supabase import create_client

sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])

def cosim(a, b):
    a, b = np.array(a, dtype=np.float32), np.array(b, dtype=np.float32)
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-10))

SELECT_PROMPT = """사용자가 아래 책에 대해 피드백을 남겼습니다.
이 피드백을 보고, 아래 reason 목록에서 피드백과 관련 있는 것을 골라주세요.

책: {title}
피드백: {feedback}

reason 목록:
{reasons}

규칙:
- 목록에 있는 것만 고를 것. 새로 만들지 말 것.
- 피드백이 언급하거나 암시하는 것과 관련 있는 reason만.
- 1~4개 선택.

JSON: {{"selected": ["reason1", "reason2", ...]}}"""

# 5개 시나리오 — 다양한 취향
scenarios = [
    {
        "name": "문학 성찰 팬 (박완서+한강)",
        "books": [
            ("못 가본 길이 더 아름답다", "작가의 인간적인 면모들이 좋았고, 삶을 먼저 살아간 선배에게서 달고 쓴 인생을 어떻게 대해야할지를 배운 것 같다"),
            ("작별하지 않는다", "슬프고 애닳지만 잊지 말아야할 역사를 가장 현실적으로 그린 책 같다"),
        ],
    },
    {
        "name": "미스터리/스릴러 팬",
        "books": [
            ("용의자 X의 헌신", "수학적 논리로 완벽한 범죄를 설계하는 과정이 소름 돋았다"),
            ("불빛 없는 밤의 도시", "사회의 어두운 면을 파고드는 묵직한 미스터리였다"),
        ],
    },
    {
        "name": "투자/경제 팬",
        "books": [
            ("돈의 가격", "돈이 어떻게 작동하는지 구조적으로 이해하게 됐다"),
            ("불변의 법칙", "시대가 바뀌어도 변하지 않는 경제 원칙이 와닿았다"),
        ],
    },
    {
        "name": "SF/판타지 팬",
        "books": [
            ("새벽", "이종 간의 공생이라는 설정이 독특했고 정체성에 대한 질문이 깊었다"),
            ("클라라와 태양", "인공지능 시점에서 바라본 인간의 사랑이 아름다웠다"),
        ],
    },
    {
        "name": "자기계발/심리 팬",
        "books": [
            ("몸은 기억한다", "트라우마가 몸에 어떻게 저장되는지 과학적으로 설명해줘서 좋았다"),
            ("데미안", "자아를 찾아가는 과정이 내 인생과 겹쳐서 울컥했다"),
        ],
    },
]

# DB 로딩
print("DB 로딩...")
all_db = []
offset = 0
while True:
    try:
        res = sb.table("book_love_reasons").select(
            "book_id, reason, reason_embedding"
        ).range(offset, offset + 499).execute()
    except:
        time.sleep(3)
        sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])
        continue
    if not res.data:
        break
    all_db.extend(res.data)
    if len(res.data) < 500:
        break
    offset += 500
    time.sleep(0.3)
print(f"  {len(all_db)}개 reason")

bids = list(set(r["book_id"] for r in all_db))
tmap, gmap = {}, {}
for i in range(0, len(bids), 100):
    res = sb.table("books").select("id, title, genre").in_("id", bids[i:i + 100]).execute()
    for b in res.data:
        tmap[b["id"]] = b["title"]
        gmap[b["id"]] = b.get("genre", "")

EXCLUDE = ["유아", "어린이", "좋은부모"]

# 시나리오별 실행
for sc in scenarios:
    print(f'\n{"=" * 70}')
    print(f'시나리오: {sc["name"]}')
    print(f'{"=" * 70}')

    all_selected = []
    read_bids = set()
    user_genres = set()

    # Step 1: 각 피드백 → LLM 선택
    for book_title, feedback in sc["books"]:
        # DB에서 책 찾기
        res = sb.table("books").select("id, title, genre").ilike("title", f"%{book_title}%").limit(1).execute()
        if not res.data:
            print(f"  ✗ {book_title} DB에 없음")
            continue
        book = res.data[0]
        read_bids.add(book["id"])
        for p in book.get("genre", "").split(">")[1:]:
            user_genres.add(p)

        # 책의 reason 목록
        reasons_res = sb.table("book_love_reasons").select("reason").eq("book_id", book["id"]).execute()
        reasons = [r["reason"] for r in reasons_res.data]
        if not reasons:
            print(f"  ✗ {book_title} reason 없음")
            continue

        # LLM 선택
        prompt = SELECT_PROMPT.format(
            title=book["title"],
            feedback=feedback,
            reasons="\n".join(f"- {r}" for r in reasons),
        )
        try:
            r = call_chat(prompt, temperature=0)
        except:
            time.sleep(5)
            r = call_chat(prompt, temperature=0)
        selected = r.get("selected", [])
        # 목록에 있는 것만 필터
        selected = [s for s in selected if s in reasons]
        all_selected.extend(selected)
        print(f'  📖 {book["title"][:30]} → {selected}')
        time.sleep(1)

    if not all_selected:
        print("  ⚠️ 마커 0개, 스킵")
        continue

    # Step 2: 태그↔태그 매칭 + 장르 부스트
    marker_embs = call_embedding(all_selected)
    n = len(all_selected)

    book_scores = {}
    for mi in range(n):
        for row in all_db:
            bid = row["book_id"]
            if bid in read_bids:
                continue
            if any(exc in gmap.get(bid, "") for exc in EXCLUDE):
                continue
            e = row.get("reason_embedding")
            if not e:
                continue
            if isinstance(e, str):
                e = [float(x) for x in e.strip("[]").split(",")]
            sim = cosim(marker_embs[mi], e)
            if bid not in book_scores:
                book_scores[bid] = {}
            if mi not in book_scores[bid] or sim > book_scores[bid][mi]:
                book_scores[bid][mi] = sim

    results = []
    for bid, sims in book_scores.items():
        avg_sim = sum(sims.values()) / n
        genre_parts = set(gmap.get(bid, "").split(">")[1:])
        overlap = len(user_genres & genre_parts)
        gb = min(overlap * 0.1, 0.3)
        score = avg_sim * (1 + gb)
        results.append((score, bid, gb))

    results.sort(key=lambda x: -x[0])

    print(f'\n  Top 10:')
    for rank, (score, bid, gb) in enumerate(results[:10], 1):
        title = tmap.get(bid, "?")[:45]
        genre = gmap.get(bid, "").split(">")[-1][:15]
        print(f'    {rank:2d}. {score:.3f} (gb+{gb:.1f}) | {title} [{genre}]')
