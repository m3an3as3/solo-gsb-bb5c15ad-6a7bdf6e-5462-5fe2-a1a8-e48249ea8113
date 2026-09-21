"""矛盾修复向导：为冲突状态计算“最少删除关系”候选方案。

思路：
- 候选删除池 = 冲突标注的关系 ∪ 各非平凡强连通分量（SCC）内的全部时序关系。
  任何有向环的边都落在某个 SCC 内；analyze 只汇报每个 SCC 的一条环，
  这里把整个 SCC 的时序边都纳入池中，保证“最少删除”的最优解不会被漏掉。
- 按删除条数 k = 1,2,… 递增枚举池内组合，首个能让 analyze 判定无冲突的 k
  即最少删除数；同一 k 下按关系编号字典序取前 MAX_CANDIDATES 组。
- 评估次数设上限（MAX_EVALUATIONS），超出则标记 truncated，
  把剩余情况交还用户手动处理，避免极端数据下接口耗时失控。
"""
from __future__ import annotations

import itertools

from . import logic

MAX_CANDIDATES = 3      # 最多返回的候选组数
MAX_REMOVE = 4          # 单组候选最多删除的关系数
MAX_EVALUATIONS = 5000  # 组合评估上限


def _involved_relation_ids(state: dict) -> set[int]:
    """候选删除池：冲突标注关系 + 非平凡 SCC 内的全部时序关系。"""
    relations = logic.normalize(state["relations"])
    unit_ids = [u["id"] for u in state["units"]]

    dsu = logic.DSU(unit_ids)
    temporal: list[dict] = []
    for r in relations:
        if r["kind"] == logic.INTERSECTS:
            dsu.union(r["source_id"], r["target_id"])
        else:
            temporal.append(r)

    comp_members = {rep: sorted(ms) for rep, ms in dsu.members.items()}
    unit_comp = {u: rep for rep, ms in comp_members.items() for u in ms}

    adj: dict[str, set[str]] = {rep: set() for rep in comp_members}
    edge_rels: dict[tuple[str, str], list[int]] = {}
    pool: set[int] = set()
    for r in temporal:
        a, b = r["source_id"], r["target_id"]
        if a not in unit_comp or b not in unit_comp:
            continue  # 单元已删除等脏数据
        ca, cb = unit_comp[a], unit_comp[b]
        if ca == cb:
            # 共存组内部的时序关系：相交/早晚冲突的当事边
            pool.add(r["id"])
            continue
        adj[ca].add(cb)
        edge_rels.setdefault((ca, cb), []).append(r["id"])

    # 环上的边必在某个非平凡 SCC 内（含自环）
    for comp in logic._tarjan_scc(list(comp_members), adj):
        if len(comp) > 1 or (len(comp) == 1 and comp[0] in adj[comp[0]]):
            members = set(comp)
            for (ca, cb), ids in edge_rels.items():
                if ca in members and cb in members:
                    pool.update(ids)

    # 冲突标注的相交关系：删除可拆分共存组，同样可能解除冲突
    pool.update(logic.analyze(state)["conflict_relation_ids"])
    return pool


def find_candidates(state: dict, max_candidates: int = MAX_CANDIDATES) -> dict:
    """计算最少删除候选。无冲突时返回空列表（前端不显示向导）。"""
    result = logic.analyze(state)
    if not result["has_conflict"]:
        return {"has_conflict": False, "candidates": [], "truncated": False}

    relations = state["relations"]
    rel_by_id = {r["id"]: r for r in relations}
    resolves = [c["message"] for c in result["conflicts"]]

    def is_repaired(removal: tuple[int, ...]) -> bool:
        removed = set(removal)
        new_state = {
            "units": state["units"],
            "relations": [r for r in relations if r["id"] not in removed],
        }
        return not logic.analyze(new_state)["has_conflict"]

    found: list[tuple[int, ...]] = []
    truncated = False
    evaluations = 0

    pools = [sorted(_involved_relation_ids(state))]
    # 兜底：池内找不到时用全部关系再试一遍（理论上极少触发）
    full_pool = sorted(rel_by_id)
    if pools[0] != full_pool:
        pools.append(full_pool)

    for pool in pools:
        for k in range(1, MAX_REMOVE + 1):
            for combo in itertools.combinations(pool, k):
                evaluations += 1
                if evaluations > MAX_EVALUATIONS:
                    truncated = True
                    break
                if is_repaired(combo):
                    found.append(combo)
                    if len(found) >= max_candidates:
                        break
            if found or truncated:
                break
        if found or truncated:
            break

    candidates = []
    for idx, combo in enumerate(found[:max_candidates], start=1):
        candidates.append({
            "id": idx,
            "relation_ids": list(combo),
            "relations": [dict(rel_by_id[rid]) for rid in combo],
            "removes": len(combo),
            "resolves": resolves,
        })
    return {"has_conflict": True, "candidates": candidates, "truncated": truncated}
