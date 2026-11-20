# 数据字典

## 班次和输入

时间字段必须带时区偏移。班次、容量版本与潮汐窗口均为 `[开始, 结束)`。接口和归档统一输出 UTC，工作台显示北京时间。车辆、桩位与记录数使用整数。

| 文件 | 字段 | 含义 |
|---|---|---|
| `sample_data/shift.json` | `start`、`end`、`name` | 班次起止与显示名 |
| 同上 | `stations`、`opening`、`opening_road` | 站号到站名和期初站内车辆数。期初在骑车辆按全网单池记账 |
| 同上 | `trucks`、`load_limit`、`available_at`、`station` | 调拨车出班信息。载重以可装车辆数计 |
| 同上 | `min_travel_minutes` | 任意两站间估算的最短通行分钟数 |
| `sample_data/events.csv` | `event_id`、`station`、`kind` | 不透明事件号、站号与尝试类型 `rent` 或 `return` |
| 同上 | `occurred_at`、`received_at` | 实际发生与补传时间。收件延迟达 300 秒标为迟报 |
| 同上 | `note` | 可空，保留引号内换行 |
| `sample_data/capacity.csv` | `station`、`revision`、`start`、`end`、`slots`、`note` | 容量修订的区间、桩数与备注。`0` 表示关站 |
| `sample_data/plan.json` | `id`、`truck`、`from`、`to`、`depart`、`arrive`、`count` | 行程键、车辆、装卸地点与时点、装卸车辆数。行程时点支持毫秒 |
| `sample_data/book.csv` | `bike_id`、`home_station`、`status`、`note` | 账面车辆册：车辆号、归属站、状态与备注。逐站数量须与运行快照期末勾稽一致，否则 `BIKE_BOOK_GAP` 拒绝关账 |
| `sample_data/count_sheet.csv` | `bike_id`、`station`、`counted_at`、`note` | 现场点数单：车辆号、点数所在站、带时区的点数时点与备注。同车同站重复全部隔离；跨站重复进入冲突仲裁 |

CSV 上限为 256 KiB 和 2000 条逻辑记录。`line` 是记录起始的物理行号。`raw` 保存原始 CSV 字段，字段前后空白在归一列中清除。必填字段的空串、`-`、`N/A`、`NULL` 和 `未知` 单列原因码。同一事件号、站点、租还动作和发生时点相等时，保留收件最早的一行，同收件时点保留源行号最小的一行。一个事件号的有效候选出现业务矛盾时，全部隔离。

## 时间线、潮汐与库存

- 同一时点先切换容量，再卸车，再装车，再处理租还。各组同秒按站号、行程号或事件号的字符串顺序裁决。
- 同站同修订号的重叠记录全部隔离；不同修订号覆盖同一时段时取修订号最大的有效记录。缺失有效版本时报 `CAPACITY_GAP`。
- 缩容时多出的车辆转站内暂存。暂存车占用库存，遇到复开、借车或装车腾出的桩，先在站内补桩。车池只在成功借还时增减。
- 15 分钟潮汐槽位的占用和空桩取窗口最后状态，借还尝试和未成交是窗口内发生次数。关站标记反映窗口最后状态。点击任一槽位可定位该站和时段的时间线。失败需求留存原编号和原因，并保持库存原值。
- 调拨行程先校验载重、时间和车辆路径，再按对应该时点的桩位状态模拟装卸。每辆车在装车时扣出站库存，卸车后才进入目标站。站点容不下整趟或装车数量不足时整份试算失败。草稿最多 20 趟。
- 比较使用同一组已采信事件，差量为方案未成交数减基准未成交数。负数表示净改善，原因改变但仍未成交的尝试也进入逐笔差异。

## 晚班关账与开班基数

- 关账以运行快照的逐站期末勾稽为账面，以现场点数单为实盘。车辆册逐站数量与快照不一致时直接 `BIKE_BOOK_GAP`，防止拿错账关账。
- 每辆进入对账的车都产生一条带 `evidence` 与源行号的采信记录，处置类型见下表。隔离行进 `count_sheet.excluded` 与 `book_sheet.excluded`，原盘点字节与归档永不删除。

| 处置码 | 触发条件 | 基数效果 |
|---|---|---|
| `MATCHED` | 现场站与账面归属站一致 | 不调整 |
| `STAGED_CLOSED` | 归属站在点数时点处于容量表关站区间（`slots=0`），车在相邻站盘到，附关站区间证据 | 归属站调出、盘到站调入，计入基数 |
| `CONFIRMED_TRANSFER` | 同一辆车被多站上报且归属站未关，按「归属站在开优先 → 点数时点早 → 站号小」仲裁，胜方采信 | 计入基数 |
| `CONFLICT_REJECT` | 双站冲突的败方上报 | 隔离，不调账 |
| `SHORTAGE_SUSPENSE` | 账面在册但现场未盘到，差异无出处 | 挂起，不进基数 |
| `SURPLUS_SUSPENSE` | 现场盘到账面未登记车辆，无来历凭证 | 挂起，不进基数 |

- 开班基数 = 账面期末 ± 有出处调整（仅 `STAGED_CLOSED`、`CONFIRMED_TRANSFER`）。挂起车单列 `suspense_held`，不凭空调平，避免账面虚增或开班数偏紧。在骑池 `road_end` 与站内基数分开列示。
- 关账结论编号由点数单与车辆册的 SHA-256、规则版本计算。同一份盘点重试得到同一编号，返回 `created=false` 且不重复调整。
- 关账绑定生成它的班次 `run_id`。方案发布产生新班次后，旧班次上仍生效的关账置 `superseded` 并清空生效指针，记录与来源全部保留；用新班次重算产生新版本，历史可逐版查看。

## API 写入与证据

| 入口 | 请求字段 | 结果 |
|---|---|---|
| `POST /api/intake` | `multipart/form-data` 的 `expected` 和 `events` | 以替换事件和当前方案生成新班次 |
| `POST /api/dispatch/preview` | JSON `run`、`legs` | 返回试算差量、调拨动作、勾稽与槽位 |
| `POST /api/dispatch/publish` | JSON `expected`、`legs` | 校验并发布方案 |
| `POST /api/runs/rerun` | JSON `expected` | 按封存原文件与已发布方案复算 |
| `POST /api/runs/restore` | JSON `expected`、`target` | 原子回切到归档版本 |
| `POST /api/closing/sheets` | `multipart/form-data` 的 `expected_run`、`count_sheet`、可选 `book_sheet` | 登记盘点来源（同字节幂等）并返回试算结论 |
| `POST /api/closing/preview` | JSON `run`、`source_id` | 按已登记来源重算关账结论但不落库 |
| `POST /api/closing/close` | JSON `expected_run`、`expected_closing`、`source_id` | 乐观并发写入关账；同盘点重试幂等 |
| `GET /api/closing`、`GET /api/closing/<id>` | 查询参数 `run` | 生效关账、版本留档与单版采信明细 |

`expected` 为写入时所见当前班次完整编号。查询七个阶段时可通过 `?run=<编号>` 固定证据版本。写入响应的 `created` 表示新增归档，`changed` 表示响应编号与所提交的 `expected` 不同。无效请求返回 `code`、`message`、`details`。

运行编号由原始文件的 SHA-256 与字节数、规范化方案和规则版本计算。相同输入重跑会比对已存结果，原生成时间、原耗时与证据文件保持一致。证据 SHA-256 针对 `evidence` 对象，编码为 UTF-8，键名排序、紧凑 JSON，中文直接写入。只有下载包外层另附证据摘要。

## 模拟边界

期初在骑车辆为匿名池，结果按尝试分配成功与失败。单次尝试只计一辆，缺车或满桩后的自行绕行以独立尝试出现。调拨车按已填装卸站顺序行驶，行程之间暂不模拟空车换站、道路拥堵或司机班表。工作台用随项目发布的班次与容量数据，事件 CSV 可替换。
