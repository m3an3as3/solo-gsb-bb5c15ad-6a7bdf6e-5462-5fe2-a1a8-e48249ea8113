"""矛盾修复向导的逻辑测试（纯 Python，无第三方依赖）。

运行：python tests/test_repair.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app import logic, repair


def make_state(unit_ids, relations):
    """unit_ids: ["U1", ...]；relations: [(id, kind, source, target), ...]"""
    return {
        "units": [{"id": u, "name": u, "note": ""} for u in unit_ids],
        "relations": [
            {"id": rid, "kind": kind, "source_id": s, "target_id": t, "note": ""}
            for rid, kind, s, t in relations
        ],
    }


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print("  ✔", msg)


def apply(state, relation_ids):
    removed = set(relation_ids)
    return {
        "units": state["units"],
        "relations": [r for r in state["relations"] if r["id"] not in removed],
    }


def expect_all_repaired(state, candidates):
    """每组候选应用后都必须让 analyze 判定无冲突。"""
    for cand in candidates:
        result = logic.analyze(apply(state, cand["relation_ids"]))
        check(
            not result["has_conflict"],
            f"候选 {cand['relation_ids']} 应用后冲突全部解除",
        )


# ------------------------------------------------------------------
print("场景 1：无冲突 → 不给出候选（前端不显示向导）")
state = make_state(
    ["U1", "U2", "U3"],
    [(1, "earlier_than", "U1", "U2"), (2, "intersects", "U2", "U3")],
)
out = repair.find_candidates(state)
check(out["has_conflict"] is False, "has_conflict=False")
check(out["candidates"] == [], "candidates 为空")

# ------------------------------------------------------------------
print("场景 2：三单元早晚环 → 3 组候选，各删 1 条")
state = make_state(
    ["A", "B", "C"],
    [
        (1, "earlier_than", "A", "B"),
        (2, "earlier_than", "B", "C"),
        (3, "earlier_than", "C", "A"),
    ],
)
out = repair.find_candidates(state)
check(out["has_conflict"] is True, "检测到冲突")
check(len(out["candidates"]) == 3, f"给出 3 组候选（实际 {len(out['candidates'])}）")
check(all(c["removes"] == 1 for c in out["candidates"]), "每组只删 1 条（最少删除）")
check(
    sorted(c["relation_ids"][0] for c in out["candidates"]) == [1, 2, 3],
    "删环上任意一条边均可修复",
)
check(all(c["resolves"] for c in out["candidates"]), "每组候选都说明解除的冲突")
expect_all_repaired(state, out["candidates"])

# ------------------------------------------------------------------
print("场景 3：相交 + 早晚冲突 → 候选含“删早晚”与“删相交”")
state = make_state(
    ["A", "B"],
    [(1, "intersects", "A", "B"), (2, "earlier_than", "A", "B")],
)
out = repair.find_candidates(state)
id_sets = sorted(tuple(c["relation_ids"]) for c in out["candidates"])
check(id_sets == [(1,), (2,)], f"候选为删相交或删早晚（实际 {id_sets}）")
expect_all_repaired(state, out["candidates"])

# ------------------------------------------------------------------
print("场景 4：两个独立环 → 最少删除 2 条")
state = make_state(
    ["A", "B", "C", "D", "E", "F"],
    [
        (1, "earlier_than", "A", "B"),
        (2, "earlier_than", "B", "C"),
        (3, "earlier_than", "C", "A"),
        (4, "earlier_than", "D", "E"),
        (5, "earlier_than", "E", "F"),
        (6, "earlier_than", "F", "D"),
    ],
)
out = repair.find_candidates(state)
check(len(out["candidates"]) == 3, "候选数封顶为 3")
check(all(c["removes"] == 2 for c in out["candidates"]), "每组删 2 条（两环各一）")
expect_all_repaired(state, out["candidates"])

# ------------------------------------------------------------------
print("场景 5：共享边的双环 → 删 1 条共享边即可全部解除")
state = make_state(
    ["A", "B", "C", "D"],
    [
        (1, "earlier_than", "A", "B"),
        (2, "earlier_than", "B", "C"),
        (3, "earlier_than", "C", "A"),
        (4, "earlier_than", "B", "D"),
        (5, "earlier_than", "D", "A"),
    ],
)
out = repair.find_candidates(state)
check(out["candidates"][0]["removes"] == 1, "最少删除数为 1")
check(
    out["candidates"][0]["relation_ids"] == [1],
    "首选方案删除共享边 #1（A→B）",
)
expect_all_repaired(state, out["candidates"])

# ------------------------------------------------------------------
print("场景 6：later_than 归一化后成环 → 同样可修复")
state = make_state(
    ["A", "B", "C"],
    [
        (1, "earlier_than", "A", "B"),
        (2, "later_than", "C", "B"),   # 等价于 B 早于 C
        (3, "earlier_than", "C", "A"),
    ],
)
out = repair.find_candidates(state)
check(len(out["candidates"]) == 3, "归一化后的环给出 3 组候选")
expect_all_repaired(state, out["candidates"])

# ------------------------------------------------------------------
print("场景 7：相交组拆分暴露隐性环 → 候选能联动解除")
# {A,B} 相交且 A 早于 B（类型 1 冲突）；同时 B→C→A 构成隐性组间环
state = make_state(
    ["A", "B", "C"],
    [
        (1, "intersects", "A", "B"),
        (2, "earlier_than", "A", "B"),
        (3, "earlier_than", "B", "C"),
        (4, "earlier_than", "C", "A"),
    ],
)
out = repair.find_candidates(state)
check(len(out["candidates"]) >= 1, "给出至少 1 组候选")
expect_all_repaired(state, out["candidates"])

# ------------------------------------------------------------------
print("场景 8：候选数永不超过 3，且候选内关系信息完整")
state = make_state(
    ["A", "B", "C", "D"],
    [
        (1, "earlier_than", "A", "B"),
        (2, "earlier_than", "B", "C"),
        (3, "earlier_than", "C", "D"),
        (4, "earlier_than", "D", "A"),
    ],
)
out = repair.find_candidates(state)
check(len(out["candidates"]) <= repair.MAX_CANDIDATES, "候选数 ≤ 3")
for cand in out["candidates"]:
    check(
        [r["id"] for r in cand["relations"]] == cand["relation_ids"],
        f"候选 {cand['id']} 的 relations 与 relation_ids 一致",
    )
    check(
        all({"id", "kind", "source_id", "target_id", "note"} <= set(r) for r in cand["relations"]),
        f"候选 {cand['id']} 的关系字段完整",
    )
expect_all_repaired(state, out["candidates"])

print("\n矛盾修复向导逻辑测试全部通过 ✔")
