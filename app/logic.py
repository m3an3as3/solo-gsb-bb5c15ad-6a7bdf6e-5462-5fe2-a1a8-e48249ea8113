"""地层关系推演核心：

- 关系归一化（later_than 视为反向 earlier_than；intersects 为无向共存关系）
- “相交”单元用并查集合为共存组
- 矛盾检测：
  1. 同共存组内部出现时序关系（相交且有早晚，逻辑矛盾）
  2. 共存组之间的时序 DAG 出现有向环（早于关系闭环）
- 无矛盾时计算每个单元在全部可行拓扑序中的层位位置区间（M = 共存组总数）：
  earliest = 必须先于该组的组数（前驱传递闭包大小）+ 1
  latest   = M − 必须晚于该组的组数（后继传递闭包大小）
  （决定位置上下界的是可达顶点的“个数”，而非最长链长度）
"""
from __future__ import annotations

import sys
from itertools import combinations
from typing import Optional

sys.setrecursionlimit(20000)

EARLIER = "earlier_than"
LATER = "later_than"
INTERSECTS = "intersects"
TIME_KINDS = (EARLIER, LATER)


def normalize(relations: list[dict]) -> list[dict]:
    """later_than(a,b) 规范化为 earlier_than(b,a)，其余保持。"""
    out: list[dict] = []
    for r in relations:
        if r["kind"] == LATER:
            out.append(
                {
                    "id": r["id"],
                    "kind": EARLIER,
                    "source_id": r["target_id"],
                    "target_id": r["source_id"],
                    "note": r.get("note", ""),
                }
            )
        else:
            out.append(dict(r))
    return out


class DSU:
    def __init__(self, members: list[str]):
        self.parent = {m: m for m in members}
        self.members = {m: {m} for m in members}
        # 发生过真正 union（两个不同成员）的当前组代表集合
        self.merged: set[str] = set()

    def find(self, x: str) -> str:
        root = x
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[x] != x:
            nxt = self.parent[x]
            self.parent[x] = root
            x = nxt
        return root

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return
        # 用字典序最小的单元作为组代表，保证结果确定
        if rb < ra:
            ra, rb = rb, ra
        self.parent[rb] = ra
        # 合并后成员表必须挂在最终的根（ra）下
        self.members[ra] = self.members[ra] | self.members[rb]
        del self.members[rb]
        # 参与过合并（含并入的旧根）即标记为“相交聚合组”
        self.merged.discard(rb)
        self.merged.add(ra)


def _find_cycle(adj: dict[str, set[str]], edge_rel: dict[tuple[str, str], int],
                members: list[str]) -> list[str]:
    """在给定强连通分量内用 DFS 找一条具体有向环，返回组代表序列。"""
    allowed = set(members)
    color: dict[str, int] = {m: 0 for m in members}  # 0 未访问, 1 在路径上, 2 完成
    stack: list[str] = []

    def dfs(u: str) -> Optional[list[str]]:
        color[u] = 1
        stack.append(u)
        for v in sorted(adj[u]):
            if v not in allowed:
                continue
            if color[v] == 0:
                found = dfs(v)
                if found is not None:
                    return found
            elif color[v] == 1:  # 回到当前路径上的祖先 => 找到环
                idx = stack.index(v)
                return stack[idx:] + [v]
        stack.pop()
        color[u] = 2
        return None

    for m in sorted(members):
        if color[m] == 0:
            found = dfs(m)
            if found is not None:
                return found
    return [members[0], members[0]]


def _tarjan_scc(nodes: list[str], adj: dict[str, set[str]]) -> list[list[str]]:
    index = 0
    indices: dict[str, int] = {}
    low: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    sccs: list[list[str]] = []

    def strongconnect(v: str) -> None:
        nonlocal index
        indices[v] = low[v] = index
        index += 1
        stack.append(v)
        on_stack.add(v)
        for w in sorted(adj[v]):
            if w not in indices:
                strongconnect(w)
                low[v] = min(low[v], low[w])
            elif w in on_stack:
                low[v] = min(low[v], indices[w])
        if low[v] == indices[v]:
            comp: list[str] = []
            while True:
                w = stack.pop()
                on_stack.discard(w)
                comp.append(w)
                if w == v:
                    break
            sccs.append(sorted(comp))

    for n in sorted(nodes):
        if n not in indices:
            strongconnect(n)
    return sccs


def analyze(state: dict) -> dict:
    """对 {units, relations} 做完整推演，返回前端/导出所需的全部结果。"""
    units = state["units"]
    unit_ids = [u["id"] for u in units]
    relations = normalize(state["relations"])

    dsu = DSU(unit_ids)
    temporal: list[dict] = []
    for r in relations:
        if r["kind"] == INTERSECTS:
            dsu.union(r["source_id"], r["target_id"])
        else:
            temporal.append(r)

    # 组的成员表（含孤立单元）
    comp_members: dict[str, list[str]] = {
        rep: sorted(ms) for rep, ms in dsu.members.items()
    }
    unit_comp = {u: rep for rep, ms in comp_members.items() for u in ms}

    conflicts: list[dict] = []
    conflict_unit_ids: set[str] = set()
    conflict_rel_ids: set[int] = set()

    # 组间时序图
    adj: dict[str, set[str]] = {rep: set() for rep in comp_members}
    radj: dict[str, set[str]] = {rep: set() for rep in comp_members}
    edge_rel: dict[tuple[str, str], int] = {}
    intra_temporal: dict[str, list[dict]] = {}

    for r in temporal:
        a, b = r["source_id"], r["target_id"]
        if a not in unit_comp or b not in unit_comp:
            continue  # 单元已删除等脏数据
        ca, cb = unit_comp[a], unit_comp[b]
        if ca == cb:
            # 矛盾类型 1：相交（共存）单元之间声明了早晚关系 —— 先收集，稍后汇总
            intra_temporal.setdefault(ca, []).append(r)
            continue
        # 反向边也是矛盾（A早于B 且 B早于A），先入图由环检测统一捕获
        adj[ca].add(cb)
        radj[cb].add(ca)
        edge_rel[(ca, cb)] = r["id"]

    # 矛盾类型 1：相交（共存）组内部又声明了早晚关系
    for ca, rels_in in sorted(intra_temporal.items()):
        members = comp_members[ca]
        internal_intersect_ids = intersect_rel_ids_on_pair(relations, members)
        temporal_ids = {r["id"] for r in rels_in}
        # 仅当该组确实由“相交”关系聚合时才构成语义矛盾
        if internal_intersect_ids:
            pairs = "、".join(f"{r['source_id']}→{r['target_id']}" for r in rels_in)
            conflicts.append({
                "type": "intersect_with_order",
                "message": f"共存组（{ '、'.join(members) }）内部既声明“相交”同期，"
                           f"又存在早晚关系（{pairs}），自相矛盾。",
                "cycle": members,
                "relation_ids": sorted(internal_intersect_ids | temporal_ids),
            })
            conflict_unit_ids.update(members)
            conflict_rel_ids |= internal_intersect_ids | temporal_ids

    # 矛盾类型 2：组时序图中的有向环（含两点互指）
    if not conflicts:
        for comp in _tarjan_scc(list(comp_members), adj):
            if len(comp) > 1 or (len(comp) == 1 and comp[0] in adj[comp[0]]):
                cyc_reps = _find_cycle(adj, edge_rel, comp)
                cyc_units: list[str] = []
                rel_ids: list[int] = []
                for i, rep in enumerate(cyc_reps[:-1]):
                    cyc_units.extend(comp_members[rep])
                    nxt = cyc_reps[i + 1]
                    rid = edge_rel.get((rep, nxt))
                    if rid is not None:
                        rel_ids.append(rid)
                conflicts.append({
                    "type": "cyclic_order",
                    "message": "早晚关系形成闭环（"
                               + " → ".join(_label_of(x, comp_members) for x in cyc_reps)
                               + "），地层不可能互为先后。",
                    "cycle": sorted(set(cyc_units)),
                    "relation_ids": sorted(set(rel_ids)),
                })
                for rep in cyc_reps:
                    conflict_unit_ids.update(comp_members[rep])
                conflict_rel_ids.update(rel_ids)

    has_conflict = bool(conflicts)

    intervals: dict[str, Optional[dict]] = {uid: None for uid in unit_ids}
    comp_levels: dict[str, int] = {}
    total_levels = 0

    if not has_conflict:
        # 拓扑序长度 = 堆积单位（共存组）总数
        total_levels = len(comp_members)
        rep_list = sorted(comp_members)
        idx = {rep: i for i, rep in enumerate(rep_list)}
        m = len(rep_list)

        # Warshall 传递闭包（布尔矩阵，标准三重循环）
        reach = [[False] * m for _ in range(m)]
        for rep in rep_list:
            for nxt in adj[rep]:
                reach[idx[rep]][idx[nxt]] = True
        for mid in range(m):
            col = [reach[i][mid] for i in range(m)]
            row = reach[mid]
            for i in range(m):
                if col[i]:
                    for j in range(m):
                        if row[j]:
                            reach[i][j] = True

        earliest: dict[str, int] = {}
        latest: dict[str, int] = {}
        for rep in rep_list:
            i = idx[rep]
            preds = sum(1 for x in range(m) if reach[x][i])
            succs = sum(1 for x in range(m) if reach[i][x])
            earliest[rep] = preds + 1
            latest[rep] = total_levels - succs

        for rep, members in comp_members.items():
            comp_levels[rep] = earliest[rep]
            for uid in members:
                intervals[uid] = {
                    "earliest": earliest[rep],
                    "latest": latest[rep],
                }

    return {
        "has_conflict": has_conflict,
        "conflicts": conflicts,
        "conflict_unit_ids": sorted(conflict_unit_ids),
        "conflict_relation_ids": sorted(conflict_rel_ids),
        "components": [
            {
                "id": rep,
                "members": members,
                "level": comp_levels.get(rep),
            }
            for rep, members in sorted(comp_members.items())
        ],
        "total_levels": total_levels,
        "intervals": intervals,
    }


def _label_of(rep: str, comp_members: dict[str, list[str]]) -> str:
    members = comp_members[rep]
    if len(members) == 1:
        return members[0]
    return "{" + "/".join(members) + "}"


def intersect_rel_ids_on_pair(relations: list[dict], members: list[str]) -> set[int]:
    """组内真正连接了两个不同成员的相交关系 id（自相交 U~U 忽略）。"""
    mset = set(members)
    return {
        r["id"]
        for r in relations
        if r["kind"] == INTERSECTS
        and r["source_id"] != r["target_id"]
        and r["source_id"] in mset
        and r["target_id"] in mset
    }


# ------------------------------------------------------------------
# 受影响节点追踪
# ------------------------------------------------------------------

def unit_signature(state: dict) -> dict[str, tuple]:
    """每个单元的推演结果指纹（共存组成员 + 层位区间 + 是否在冲突中）。"""
    result = analyze(state)
    comp_of = {}
    for c in result["components"]:
        for m in c["members"]:
            comp_of[m] = tuple(c["members"])
    return {
        u["id"]: (
            comp_of.get(u["id"], (u["id"],)),
            None if result["intervals"][u["id"]] is None
            else (result["intervals"][u["id"]]["earliest"],
                  result["intervals"][u["id"]]["latest"]),
            u["id"] in set(result["conflict_unit_ids"]),
        )
        for u in state["units"]
    }


def affected_units(before: dict[str, tuple], after_state: dict) -> list[str]:
    after = unit_signature(after_state)
    changed = [
        uid
        for uid, sig in after.items()
        if uid not in before or before.get(uid) != sig
    ]
    # 被删除的单元不算“受影响节点”（已不存在）
    return sorted(changed)


# ------------------------------------------------------------------
# 矛盾修复向导：最少删除关系候选
# ------------------------------------------------------------------
# 规模保护：只对矛盾关系本身做组合枚举；课堂/单机规模下数量很小，
# 这里给出硬上限防止异常数据下组合爆炸。
REPAIR_REL_LIMIT = 40
REPAIR_TEST_BUDGET = 10000

_RELATION_LABEL = {EARLIER: "早于", LATER: "晚于", INTERSECTS: "相交"}


def relation_label(r: dict) -> str:
    """关系的人类可读描述，按录入方向展示（不做 later→earlier 归一化）。"""
    return f"{r['source_id']} {_RELATION_LABEL.get(r['kind'], r['kind'])} {r['target_id']}"


def _build_repair_model(relations: list[dict], unit_ids: list[str]) -> dict:
    """构造修复用的图模型（与 analyze 同源逻辑，独立实现以免扰动推演路径）。

    - intersects 经 DSU 并为共存组；
    - 组内早晚关系 + 聚合该组的相交关系 => intersect_with_order 冲突；
    - 组间早晚关系图中的 SCC（>1 个组，或自环）=> cyclic_order 冲突。

    与 analyze 不同：这里即使已经发现“相交且有早晚”，也继续跑 SCC，
    因为删除候选必须同时解除数据中可能并存的全部冲突。
    """
    dsu = DSU(unit_ids)
    temporal: list[dict] = []
    for r in relations:
        if r["kind"] == INTERSECTS:
            dsu.union(r["source_id"], r["target_id"])
        elif r["kind"] in TIME_KINDS:
            temporal.append(r)

    comp_members: dict[str, list[str]] = {
        rep: sorted(ms) for rep, ms in dsu.members.items()
    }
    unit_comp = {u: rep for rep, ms in comp_members.items() for u in ms}

    adj: dict[str, set[str]] = {rep: set() for rep in comp_members}
    edge_rels: dict[tuple[str, str], list[int]] = {}
    intra: dict[str, list[dict]] = {}

    for r in temporal:
        a, b = r["source_id"], r["target_id"]
        if a not in unit_comp or b not in unit_comp:
            continue
        ca, cb = unit_comp[a], unit_comp[b]
        if ca == cb:
            intra.setdefault(ca, []).append(r)
        else:
            adj[ca].add(cb)
            edge_rels.setdefault((ca, cb), []).append(r["id"])

    # 与 analyze 一致：仅由相交关系聚合起来的组，组内早晚关系才算语义冲突
    intra_conflicts: list[dict] = []
    for ca in sorted(intra):
        if ca in dsu.merged:
            members = comp_members[ca]
            inter_ids = intersect_rel_ids_on_pair(relations, members)
            intra_conflicts.append({
                "members": list(members),
                "temporal_ids": sorted(r["id"] for r in intra[ca]),
                "intersect_ids": sorted(inter_ids),
            })

    cyc_conflicts: list[dict] = []
    for comp in _tarjan_scc(list(comp_members), adj):
        self_loop = len(comp) == 1 and comp[0] in adj[comp[0]]
        if len(comp) > 1 or self_loop:
            cyc_reps = _find_cycle(adj, {k: v[0] for k, v in edge_rels.items()}, comp)
            path = " → ".join(_label_of(x, comp_members) for x in cyc_reps)
            comp_set = set(comp)
            member_set = {m for rep in comp for m in comp_members[rep]}
            members = sorted(member_set)
            path_edge_ids: set[int] = set()
            for i, rep in enumerate(cyc_reps[:-1]):
                path_edge_ids.update(edge_rels.get((rep, cyc_reps[i + 1]), ()))
            # 候选池：SCC 内全部组间早晚边（含弦/并行边，不限于报告路径），
            # 以及 SCC 成员集合内部的全部相交关系（删一条相交可拆散共存组从而破环）
            internal_temporal_ids: set[int] = set()
            for r in temporal:
                if (r["source_id"] in member_set and r["target_id"] in member_set
                        and unit_comp[r["source_id"]] != unit_comp[r["target_id"]]
                        and unit_comp[r["source_id"]] in comp_set):
                    internal_temporal_ids.add(r["id"])
            internal_intersect_ids = intersect_rel_ids_on_pair(relations, members)
            cyc_conflicts.append({
                "reps": list(cyc_reps),
                "members": members,
                "path": path,
                "path_edge_ids": sorted(path_edge_ids),
                "edge_ids": sorted(internal_temporal_ids | internal_intersect_ids),
            })

    return {
        "comp_members": comp_members,
        "unit_comp": unit_comp,
        "adj": adj,
        "edge_rels": edge_rels,
        "intra_conflicts": intra_conflicts,
        "cyc_conflicts": cyc_conflicts,
    }


def _has_conflict_light(relations: list[dict], unit_ids: list[str]) -> bool:
    """修复组合搜索专用的快速冲突判定（只判断有无，不产出说明）。

    与 analyze 等价的两类判据，但跳过 Warshall 闭包与区间计算：
    1) 由相交关系聚合的共存组内部存在早晚关系；
    2) 共存组间早晚图有环（Kahn 入度消点，含自环与互指）。
    """
    dsu = DSU(unit_ids)
    temporal: list[dict] = []
    for r in relations:
        if r["kind"] == INTERSECTS:
            dsu.union(r["source_id"], r["target_id"])
        elif r["kind"] in TIME_KINDS:
            temporal.append(r)

    comp_ids = set(dsu.members)
    unit_comp = {u: rep for rep, ms in dsu.members.items() for u in ms}

    adj: dict[str, set[str]] = {rep: set() for rep in comp_ids}
    indeg: dict[str, int] = {rep: 0 for rep in comp_ids}
    intra: dict[str, bool] = {}
    for r in temporal:
        a, b = r["source_id"], r["target_id"]
        if a not in unit_comp or b not in unit_comp:
            continue
        ca, cb = unit_comp[a], unit_comp[b]
        if ca == cb:
            intra[ca] = True
        elif cb not in adj[ca]:
            adj[ca].add(cb)
            indeg[cb] += 1

    for ca in intra:
        if ca in dsu.merged:
            return True

    # Kahn：无法消掉全部顶点即有环
    queue = [rep for rep in comp_ids if indeg[rep] == 0]
    seen = 0
    while queue:
        u = queue.pop()
        seen += 1
        for v in adj[u]:
            indeg[v] -= 1
            if indeg[v] == 0:
                queue.append(v)
    return seen != len(comp_ids)


def repair_candidates(state: dict, limit: int = 3) -> dict:
    """计算至多 ``limit`` 组“最少删除关系”候选。

    在所有参与冲突的关系中，按删除数量从小到大枚举组合，
    保留恰好达到最小规模且能让推演完全无冲突的组合（不同关系组成的
    最小删除集给出多种等价修复思路）。无冲突时返回空候选。
    """
    unit_ids = [u["id"] for u in state["units"]]
    # 以录入形态的关系构造标签（normalize 仅用于图算法）
    relations = [
        r for r in state["relations"]
        if r["source_id"] in unit_ids and r["target_id"] in unit_ids
    ]
    rel_by_id = {r["id"]: r for r in relations}
    norm_by_id = {r["id"]: r for r in normalize(relations)}

    def evaluate(rels: list[dict]) -> tuple[bool, dict]:
        model = _build_repair_model(normalize(rels), unit_ids)
        has = bool(model["intra_conflicts"]) or bool(model["cyc_conflicts"])
        return (not has), model

    currently_ok, _ = evaluate(relations)
    if currently_ok:
        return {"has_conflict": False, "candidates": []}

    base = _build_repair_model(normalize(relations), unit_ids)

    # 候选关系池：每一条“删了才可能解开冲突”的关系
    pool: set[int] = set()
    for ic in base["intra_conflicts"]:
        pool.update(ic["temporal_ids"])
        pool.update(ic["intersect_ids"])
    for cc in base["cyc_conflicts"]:
        pool.update(cc["edge_ids"])

    pool_ids = sorted(pool)
    if len(pool_ids) > REPAIR_REL_LIMIT:
        pool_ids = pool_ids[:REPAIR_REL_LIMIT]

    def resolves(deleted: set[int]) -> bool:
        kept = [r for r in relations if r["id"] not in deleted]
        return not _has_conflict_light(normalize(kept), unit_ids)

    minimal_sets: list[frozenset[int]] = []
    tested = 0
    exhausted = True
    size = 1
    while size <= len(pool_ids) and len(minimal_sets) < limit:
        found_at_size = False
        for combo in combinations(pool_ids, size):
            tested += 1
            if tested > REPAIR_TEST_BUDGET:
                exhausted = False
                break
            deleted = frozenset(combo)
            if resolves(deleted):
                minimal_sets.append(deleted)
                found_at_size = True
                if len(minimal_sets) >= limit:
                    break
        if found_at_size:
            break  # 更小的规模已经找到解，更大的删除集不再是“最少删除”
        if not exhausted:
            break
        size += 1

    total_conflicts = len(base["intra_conflicts"]) + len(base["cyc_conflicts"])
    candidates: list[dict] = []
    for deleted in minimal_sets:
        deleted_sorted = sorted(deleted)
        # 最小解：整个删除集使推演无冲突；而放回任意一条都会复现冲突，
        # 下面的反事实归因恰好复用这一性质生成说明。
        kept_norm = [norm_by_id[rid] for rid in norm_by_id if rid not in deleted]

        # 反事实归因：把每条被删关系单独“放回”，用复现的冲突说明它为何必须删除
        # （删除“相交”会重组共存组，可能连带打断环——静态记账无法体现这一点）
        edge_reasons: dict[int, list[str]] = {rid: [] for rid in deleted_sorted}
        for rid in deleted_sorted:
            cf_model = _build_repair_model(kept_norm + [norm_by_id[rid]], unit_ids)
            sentences = [
                _intra_reason(ic) for ic in cf_model["intra_conflicts"]
            ] + [
                f"早晚闭环仍存在（{cc['path']}）。" for cc in cf_model["cyc_conflicts"]
            ]
            label = relation_label(rel_by_id[rid])
            edge_reasons[rid] = [
                f"若保留该关系（{label}），{s}" for s in _dedup(sentences)
            ]

        candidates.append({
            "delete_relation_ids": deleted_sorted,
            "delete_relations": [
                {
                    "id": rid,
                    "kind": rel_by_id[rid]["kind"],
                    "source_id": rel_by_id[rid]["source_id"],
                    "target_id": rel_by_id[rid]["target_id"],
                    "note": rel_by_id[rid].get("note", ""),
                    "label": relation_label(rel_by_id[rid]),
                    "reasons": edge_reasons[rid],
                }
                for rid in deleted_sorted
            ],
            "resolved_conflict_count": total_conflicts,
            "resolves_all": True,
            "summary": f"删除 {len(deleted_sorted)} 条关系后，"
                       f"全部 {total_conflicts} 处矛盾解除，可恢复层位推演。",
        })

    return {
        "has_conflict": True,
        "candidates": candidates,
        "conflict_count": total_conflicts,
        "candidate_relation_pool": pool_ids,
        "search_exhausted": exhausted,
    }


def _intra_reason(ic: dict) -> str:
    members_txt = "、".join(ic["members"])
    pairs = "、".join(
        f"#{rid}" for rid in ic["temporal_ids"]
    )
    return (f"共存组（{members_txt}）内“相交同期”与早晚关系（{pairs}）"
            "的矛盾仍存在。")


def _dedup(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for it in items:
        if it not in seen:
            seen.add(it)
            out.append(it)
    return out
