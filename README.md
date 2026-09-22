# 考古地层关系推演台

一个用于考古地层（stratigraphy）关系录入与逻辑推演的小型 Web 应用。
基于 **Python FastAPI + SQLite + 原生前端（HTML/CSS/SVG/JS）**，无需 Node、无需构建步骤，
一条 `docker compose up` 即可在浏览器中使用。

![stack](https://img.shields.io/badge/stack-FastAPI%20%7C%20SQLite%20%7C%20vanilla%20JS-8a5a2b)

## 功能

- **地层单元录入**：编号、名称、备注；支持编辑、删除（删除单元级联清理其关系）。
- **三种关系**：
  - `早于`（A 的形成早于 B，对应 Harris 矩阵中的 A → B）
  - `晚于`（自动等价于反向“早于”）
  - `相交`（同期共存/打破而不可定先后，作为无向关系处理）
- **矛盾环校验**，自动检出两类逻辑冲突并在图中标红：
  1. 两个被声明为“相交（同期）”的单元之间又存在早晚关系；
  2. 早晚关系在共存组之间形成有向环（A 早于 B、B 早于 C、C 又早于 A……），
     基于 Tarjan 强连通分量算法定位，并给出具体环路径。
- **层位区间推演**：无冲突时，把“相交”单元并查集（DSU）合并为共存组（共 M 个堆积单位），
  对共存组时序 DAG 求传递闭包，得到该组在**全部可行拓扑序中的位置区间**：
  - 每个单元的 **最早可能层位** = 必须先于它的组数（前驱闭包大小）+ 1；
  - **最晚可能层位** = M − 必须晚于它的组数（后继闭包大小）。

  因此输出的 `[最早–最晚]` 区间覆盖该单元在所有可行拓扑序中可能占据的位置；
  区间固定（最早=最晚）表示该单元在任何合法顺序中层位唯一。
  注意位置上下界由**必须先/后于它的堆积单位个数**决定，而不是最长链长度：
  一个单元若领先两个互不可比的后继，二者都会把它的最晚位置向前压。
- **受影响节点标记**：新增/删除关系或删除单元后，凡是“共存组成员、层位区间、冲突状态”
  发生变化的单元都会以橙色描边标出，关系两端必标。
- **关系图**：SVG 分层绘制（最早层位在底部，契合地层剖面习惯），相交组以虚线框聚合，
  矛盾关系红色加粗、普通早晚关系带方向箭头；冲突时切换为网格布局以便看清环。
- **冲突详情面板**：逐条说明矛盾原因与涉及单元，点击可定位节点。
- **矛盾修复向导**：检测到早晚环或“相交/早晚”冲突时，后端在矛盾关系集合上
  按删除数量从小到大枚举（规模受限，默认最多考察 40 条关系、1 万次组合验证），
  计算**不超过 3 组“最少删除关系”候选**；每组逐关系给出“若保留它，何种冲突仍在”
  的反事实说明。候选展示在冲突面板下方，列出将被删除的关系；用户确认后一次性
  删除并整体刷新节点、层位区间与关系图（受影响节点照常橙色标记）。
  **没有冲突时向导不显示。**
- **JSON 导出**：一键下载包含全部单元、关系与推演结果（区间、冲突、共存组）的 JSON。

## 目录结构

```
.
├── app/
│   ├── main.py          # FastAPI 路由（单元/关系 CRUD、导出、示例数据）
│   ├── logic.py         # 关系归一化、DSU、Tarjan 环检测、层位区间推演、受影响比对、修复候选求解
│   ├── db.py            # SQLite 连接与建表
│   ├── schemas.py       # Pydantic 请求模型与校验
│   ├── seed.py          # 内置示例（7 个单元的 Harris 式地层）
│   └── static/          # 轻量前端 index.html / styles.css / app.js（含矛盾修复向导面板）
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
```

## 快速开始（Docker）

```bash
docker compose up --build
```

启动后浏览器打开：

- 应用界面：<http://localhost:8000/>
- OpenAPI 文档：<http://localhost:8000/docs>
- 健康检查：<http://localhost:8000/health>

SQLite 数据文件持久化在命名卷 `strata-data`（容器内 `/data/strata.db`），
容器重建不丢数据；也可在 compose 中改为绑定本地目录。

## 本地运行（不使用 Docker）

需要 Python 3.11+：

```bash
python -m venv .venv
source .venv/bin/          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
export STRATA_DB_PATH=./strata.db   # Windows PowerShell: $env:STRATA_DB_PATH="./strata.db"
uvicorn app.main:app --reload --port 8000
```

打开 <http://localhost:8000/>，点击右上角 **“载入示例”** 即可看到一份
包含“生土层—房基/垫土（相交同期）—灰坑—文化层—墓葬—耕土”的演示数据。

## 使用说明

1. 在左栏添加地层单元（编号唯一，如 `U1`、`H2`）。
2. 在右栏选择两个单元与关系类型（A **早于/晚于/相交** B）并添加。
3. 中间图实时重绘；无冲突时每个节点下方显示其可行层位区间，
   底部文字给出总层位数与共存组统计。
4. 故意制造矛盾（例如再添加“U7 早于 U1”）即可看到冲突详情、红色环边
   与受影响节点高亮；冲突面板下方会出现**矛盾修复向导**，列出至多 3 组
   最少删除方案及将被删除的关系，点击“应用此方案”确认后一次性删除，
   节点、区间与关系图随即恢复；手工删除标红关系后推演同样自动恢复。
5. 点击单元可在列表与图中联动定位；点击冲突条目可跳转到涉及节点。
6. 点击 **导出 JSON** 下载完整快照。

## REST API 摘要

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/state` | 全部单元、关系、推演结果与受影响列表 |
| POST | `/api/units` | 新增单元 `{id,name,note}` |
| PATCH | `/api/units/{id}` | 修改单元名称/备注 |
| DELETE | `/api/units/{id}` | 删除单元（级联删除关联关系） |
| POST | `/api/relations` | 新增关系 `{kind,source_id,target_id,note}` |
| DELETE | `/api/relations/{id}` | 删除关系 |
| GET | `/api/repair/candidates` | 矛盾修复向导候选（至多 3 组最少删除方案；无冲突时候选为空） |
| POST | `/api/repair/apply` | 确认应用某组候选 `{relation_ids:[…]}`，一次性删除并返回最新状态 |
| POST | `/api/sample` | 清空并载入示例数据 |
| POST | `/api/reset` | 清空全部数据 |
| GET | `/api/export` | 下载 JSON 快照 |

`kind` 取值：`earlier_than` / `later_than` / `intersects`。

### 导出 JSON 结构（节选）

```json
{
  "exported_at": "2026-09-21T08:00:00+00:00",
  "units": [{ "id": "U4", "name": "灰坑 H1", "note": "..." }],
  "relations": [{ "id": 4, "kind": "earlier_than", "source_id": "U2", "target_id": "U4" }],
  "analysis": {
    "has_conflict": false,
    "conflicts": [],
    "components": [{ "id": "U2", "members": ["U2", "U3"], "level": 2 }],
    "total_levels": 6,
    "intervals": { "U4": { "earliest": 3, "latest": 3 } }
  }
}
```

### 矛盾修复向导接口

**`GET /api/repair/candidates`**（无请求体）

只读计算当前数据的最少删除候选，不修改任何数据。无冲突时：

```json
{ "has_conflict": false, "candidates": [] }
```

有冲突时返回至多 3 组候选，每组都是规模相同的**最小**删除集
（少删任意一条都仍有冲突），例如三环 `A早于B、B早于C、C早于A`：

```json
{
  "has_conflict": true,
  "conflict_count": 1,
  "candidate_relation_pool": [1, 2, 3],
  "search_exhausted": true,
  "candidates": [
    {
      "delete_relation_ids": [1],
      "delete_relations": [
        {
          "id": 1,
          "kind": "earlier_than",
          "source_id": "A",
          "target_id": "B",
          "note": "",
          "label": "A 早于 B",
          "reasons": ["若保留该关系（A 早于 B），早晚闭环仍存在（A → B → C → A）。"]
        }
      ],
      "resolved_conflict_count": 1,
      "resolves_all": true,
      "summary": "删除 1 条关系后，全部 1 处矛盾解除，可恢复层位推演。"
    }
  ]
}
```

字段说明：

- `delete_relations[].reasons`：反事实归因——把该关系单独放回后复现的
  冲突说明（删除一条“相交”关系会重组共存组，可能顺带打断早晚环，
  因此归因基于放回后的重新推演，而非静态记账）。
- `candidate_relation_pool`：参与冲突、可作为删除对象的关系 id 全集。
- `search_exhausted`：组合搜索是否在预算内穷举完毕；若为 `false`
  且候选不足 3 组，说明冲突关系数量超过求解上限（默认池 40 条 /
  1 万次验证，见 `app/logic.py` 的 `REPAIR_REL_LIMIT`、
  `REPAIR_TEST_BUDGET`），前端会提示手工处理。

**`POST /api/repair/apply`**

请求体：`{"relation_ids": [1, 4]}`（1–100 个互不重复的关系 id）。

在同一把写锁内校验 id 均存在（任一不存在返回 `404`，提示重新获取候选），
一次性删除后重新推演，返回与 `/api/state` 同构的最新状态，并额外携带：

```json
{
  "repaired_relation_ids": [1, 4],
  "repair_resolved": true,
  "units": [/* … */],
  "relations": [/* … */],
  "analysis": { /* 删除后的完整推演结果 */ },
  "affected": ["A", "B", "D", "E"]
}
```

`repair_resolved` 为 `false` 表示应用后仍有冲突（例如客户端提交了
并非完整解的 id 集合），前端会保留向导继续引导。该接口也可独立于
候选使用：提交任意关系 id 集合等价于一次批量删除。

## 测试

```bash
# 前端渲染冒烟测试（仅需 Node，无浏览器依赖）
node tests/smoke_frontend.js
```

后端推演逻辑可用一段内置的暴力校验脚本验证（枚举全部拓扑序比对区间），
核心入口为 `app.logic.analyze(state)`，输入 `{"units": [...], "relations": [...]}`；
矛盾修复候选入口为 `app.logic.repair_candidates(state)`，返回的每组候选
都可用 `analyze()` 验证“删除后无冲突、且少删任意一条仍冲突”的最小性。

## 设计说明与边界

- **相交**按 Harris 矩阵惯例解释为“同期/无法定序”的等价关系：
  相交关系传递并合并（U2 相交 U3、U3 相交 U4 视为三者同组），
  组内成员共享同一可行层位区间。若实际材料中“相交”仅表示局部打破、
  不应传递，可在 `app/logic.py` 中调整 DSU 合并策略。
- 矛盾状态下系统**保留全部数据**，不会拒绝录入——关系照常保存并标红，
  待用户删除冲突关系后区间推演自动恢复，便于对比和教学演示。
- **修复向导只建议删除，绝不自动改动数据**：候选是对“参与冲突的关系”
  按删除数升序枚举得到的最小删除集（与“最少反馈弧集”同一思路，但仅在
  矛盾关系小集合内穷举，保证课堂规模下秒级返回）；必须由用户确认后才经
  `POST /api/repair/apply` 一次性删除。
- 层位区间针对的是“全部可行拓扑序”的精确上下界（DAG 最长路径，Kahn 算法），
  而非单一任意排序。
- 并发写入由进程内锁串行化；SQLite 开启 WAL，适合单机/课堂规模使用。
