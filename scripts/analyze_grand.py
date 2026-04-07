"""기존 grand_results 결과를 페르소나 그룹별로 재집계.

그룹:
- omnivore: 잡식형 (수아, 도현, 지은)
- maniac: 마니아 (태원, 건우, 소율)
- minimal: 데이터 부족 (현우, 미소, 민서)
- negative: 부정 위주 (예린, 재훈, 하늘)
- normal: 일반 (서연, 민호, 준혁, 유진, 지훈, 아름)
"""
import re, sys
from collections import defaultdict

PATH = sys.argv[1] if len(sys.argv) > 1 else \
    'scripts/test_data/grand_results_20260407_104941.md'

GROUPS = {
    'omnivore': ['수아', '도현', '지은'],
    'maniac': ['태원', '건우', '소율'],
    'minimal': ['현우', '미소', '민서'],
    'negative': ['예린', '재훈', '하늘'],
    'normal': ['서연', '민호', '준혁', '유진', '지훈', '아름'],
}
PERSONA_TO_GROUP = {p: g for g, ps in GROUPS.items() for p in ps}

with open(PATH) as f:
    content = f.read()

# 각 페르소나 섹션 파싱
sections = re.split(r'\n## ([가-힣]+)\n', content)
# sections[0] = 헤더, [1]=name1, [2]=table1, [3]=name2, [4]=table2 ...

# {persona: {hypothesis: {metric: value}}}
data = {}
for i in range(1, len(sections), 2):
    name = sections[i]
    table = sections[i+1]
    if name not in PERSONA_TO_GROUP:
        continue
    rows = re.findall(r'\| (H\d+_\w+) \| ([\d.]+) \| ([\d.]+) \| ([\d.]+) \| ([\d.]+) \| ([\d.]+) \| ([\d.NA/]+) \|',
                       table)
    persona_data = {}
    for hid, cov, div, auth, t5, ad, av in rows:
        persona_data[hid] = {
            'cov': float(cov), 'div': float(div), 'auth': float(auth),
            't5': float(t5), 'ad': float(ad),
        }
    data[name] = persona_data

# 그룹별 가설별 평균
def composite(m):
    return m['cov']*0.25 + m['div']*0.20 + m['auth']*0.20 + m['t5']*0.20 + m['ad']*0.15

print("# 페르소나 그룹별 가설 비교\n")
print("composite = cov×0.25 + div×0.20 + auth×0.20 + t5×0.20 + ad×0.15\n")

all_h = sorted({h for pd in data.values() for h in pd.keys()})

for group, personas in GROUPS.items():
    group_data = [data[p] for p in personas if p in data]
    if not group_data:
        continue
    print(f"\n## {group.upper()} ({', '.join(personas)})\n")
    print("| 가설 | Cov | Div | Auth | T5 | AD | Composite |")
    print("|---|---|---|---|---|---|---|")
    rows = []
    for h in all_h:
        ms = [pd.get(h) for pd in group_data if h in pd]
        if not ms:
            continue
        avg = {k: sum(m[k] for m in ms)/len(ms) for k in ['cov','div','auth','t5','ad']}
        comp = composite(avg)
        rows.append((h, avg, comp))
    rows.sort(key=lambda x: x[2], reverse=True)
    for h, avg, comp in rows:
        print(f"| {h} | {avg['cov']:.2f} | {avg['div']:.2f} | {avg['auth']:.2f} | "
              f"{avg['t5']:.2f} | {avg['ad']:.2f} | **{comp:.3f}** |")

# 전체 그룹 가중 평균 (그룹 균등)
print("\n## 전체 (그룹 균등 가중)\n")
print("| 가설 | Composite | maniac | minimal | omnivore | negative | normal |")
print("|---|---|---|---|---|---|---|")
all_rows = []
for h in all_h:
    group_scores = {}
    for group, personas in GROUPS.items():
        gd = [data[p] for p in personas if p in data]
        if not gd:
            continue
        ms = [pd.get(h) for pd in gd if h in pd]
        ms = [m for m in ms if m]
        if not ms:
            continue
        avg = {k: sum(m[k] for m in ms)/len(ms) for k in ['cov','div','auth','t5','ad']}
        group_scores[group] = composite(avg)
    if group_scores:
        overall = sum(group_scores.values()) / len(group_scores)
        all_rows.append((h, overall, group_scores))

all_rows.sort(key=lambda x: x[1], reverse=True)
for h, overall, gs in all_rows:
    cells = [f"{gs.get(g, 0):.3f}" for g in ['maniac','minimal','omnivore','negative','normal']]
    print(f"| {h} | **{overall:.3f}** | {' | '.join(cells)} |")
