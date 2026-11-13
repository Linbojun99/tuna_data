# 模型前数据准备：CPUE 标准化与 SDM

把库中已下载的**公开聚合 catch-effort 数据**（IATTC、WCPFC、IOTC、ICCAT、CCSBT）整理成统一的分层表，再按物种、种群和渔具生成可直接建模的输入：CPUE 标准化（GLM/GAM/sdmTMB/VAST）和物种分布模型（SDM）。

**不处理**：logbook、观察员逐网数据、个人 CPUE、船号、钓钩间浮子数（HBF）。这些信息公开聚合数据里没有，因此本流程得到的是“名义 CPUE 的标准化输入”，不能替代评估报告中基于逐网数据的指数。

## 1. 流程

```
原始快照 (raw/, SHA-256 固定)
   │  各来源 adapter：不补零、不补坐标、不换算单位
   ▼
标准分层 run (processed/modelprep/ModelPrep_…/)
   strata  一行 = 一个原生分层（时间 × 网格 × 船旗/船队 × 渔具 × 作业类型）
   effort  一行 = 分层 × 努力量单位（单位之间不相加）
   catch   一行 = 分层 × 物种 × 渔获单位（尾数 / 吨 / kg 不相加、不换算）
   │  零值确认：按“报告组”（船旗|渔具，或 ICCAT 数据集）判断 0 是否可信
   ▼
物种表（读取时按规则筛选，每一步都记录在 filter_log）
   cpue_table() / tuna_cpue_data()   每行一个分层：努力量、目标物种渔获、名义 CPUE
   sdm_table()  / tuna_sdm_data()    出现/未出现、丰度、log(努力量)，可汇总到统一网格，可加 GFW 小时
```

构建只读已下载的原始文件，不触发下载；每次构建是独立的不可变目录，成功后才更新 `latest.json`，并写入 `recipe.lock.toml` 固定所有来源版本。

## 2. 快速开始（R）

```r
repo <- "/你的代码目录/tuna_data"
Sys.setenv(TUNA_DATA_HOME = "/你的数据目录/library")
source(file.path(repo, "R", "tuna_data.R"))

# ① 构建一次（只读原始快照）
tuna_modelprep_build(config = file.path(repo, "templates", "modelprep.toml"))

# ② 看规则：种群边界、默认单位、是否建议
tuna_species_rules("BET")

# ③ BET 延绳钓 CPUE：WCPO，尾数/千钩
bet <- tuna_cpue_data("BET", gear = "longline", stock = "BET-WCPO", years = 1990:2024)
tuna_filter_log(bet)      # 每条规则前后的行数

# ④ SKJ 围网：漂浮物/FAD 作业，吨/网次
skj <- tuna_cpue_data("SKJ", gear = "purse_seine", stock = "SKJ-WCPO", school_types = c("DFAD", "AFAD", "LOG"))

# ⑤ YFT SDM：印度洋延绳钓，5° × 月，附 IOTC 区域 GFW 小时。
#    IOTC 只列正渔获，未出现需用推断零（with_inferred），并与默认严格口径比较。
yft <- tuna_sdm_data("YFT", gear = "longline", stock = "YFT-IO", grid = 5, period = "month",
                     years = 2013:2024, zeros = "with_inferred", gfw = TRUE, gfw_missing = "skip")
```

完整示例（含名义 delta-lognormal 示意和带版本的导出）：[examples/r_modelprep.R](../examples/r_modelprep.R)。Python 接口相同：`from tuna_data.modelprep import build_modelprep, cpue_table, sdm_table, rules_table`。命令行：`tuna-data build-modelprep --config templates/modelprep.toml`，`tuna-data species-rules --species BET`。

## 3. 产品

同一 `view_group` 的产品是**同一批渔获的不同视图**（例如 IATTC 围网按船旗 / 按作业类型），都会保存，但一次分析中每个来源只能选一个；函数会拒绝同时使用两个。

| 产品 | 来源数据集 | view_group | 内容 |
|---|---|---|---|
| `iattc_ll` | iattc/publiclltunabillfish（Num + Mt 视图合并） | iattc_ll | 延绳钓 5°×月×船旗；钩数；尾数与吨分别给完整性 |
| `iattc_ps_settype` | iattc/publicpstuna（SetType） | iattc_ps | 围网 1°×月×作业类型（DEL/NOA/OBJ）；网次 |
| `iattc_ps_flag` | iattc/publicpstuna（Flag） | iattc_ps | 围网 1°×月×船旗；网次 |
| `iattc_lp` | iattc/publiclptunaflag | iattc_lp | 竿钓 1978–2015，仅历史用途 |
| `wcpfc_ll` | wcpfc/827 | wcpfc_ll | 延绳钓 5°×月×船旗；HHOOKS×100；三船规则遮蔽层为 masked |
| `wcpfc_ps` | wcpfc/1005 | wcpfc_ps | 围网 1°×季×船旗，按作业类型拆成子分层（UNA/LOG/DFAD/AFAD/OTH）；网次 |
| `wcpfc_ps_days` | wcpfc/1005 | wcpfc_ps | 同上，不拆作业类型；DAYS（捕捞+搜寻天数） |
| `wcpfc_lp` | wcpfc/830 | wcpfc_lp | 竿钓 1°×季×船旗；约 98% 遮蔽 |
| `iotc_ce` | iotc/iotc_datasets_ce（CA+EF 连接） | iotc_ce | 非围网渔具；只列正渔获，零值需推断 |
| `iotc_ce_ps_school` | iotc/iotc_datasets_ce | iotc_ps | 围网按作业类型（FS/LS…）；约三分之二分层只有渔获（努力量只按 UNCL 汇总报告），按作业类型的网次主要在 2012 年后 |
| `iotc_ce_ps_pooled` | iotc/iotc_datasets_ce | iotc_ps | 围网合并作业类型；努力量取 UNCL 汇总行 |
| `iccat_t2ce` | iccat/t2ce（Access 数据库） | iccat_t2ce | Task 2 catch-effort；kg 与尾数分开；两种努力量分开 |
| `ccsbt_ll` | ccsbt/cedata_longline | ccsbt_ll | SBF 延绳钓 5°×月×国家×统计区；尾数 |
| `ccsbt_surface` | ccsbt/cedata_surface | ccsbt_surface | SBF 表层（围网/竿钓）1°；搜寻小时；kg（目测估计） |

各产品的行数、年份范围和状态计数见运行目录中的 `products.csv`、`status_counts.csv` 和 `adapter_qc.csv`（`tuna_modelprep("products")`）。

## 4. 零值与缺失：必须区分

| catch_status | 含义 | 默认用作 |
|---|---|---|
| `positive` | 渔获 > 0 | 出现 |
| `zero_reported` | 显式 0，且同一报告组（船旗/船队 × 渔具、作业类型或 ICCAT 数据集）在该产品的整个序列中报告过该物种（同单位） | 未出现（真零） |
| `zero_unconfirmed` | 显式 0，但该报告组从未报告该物种（例如 IATTC 的 ESP 只报旗鱼） | 不用 |
| `inferred_zero_candidate` | 没有值，但报告组报告该物种：IOTC 只列正渔获；很少写 0 的报告组留空（如 CCSBT JP/TW） | 敏感性分析（`zeros = "with_inferred"`） |
| `not_reported` | 空值/NULL，未知；常写 0 的报告组（非正值中 ≥ 50% 写 0）留空视为缺失 | 不用 |
| `masked` | 保密遮蔽层（WCPFC 三船规则：渔获和努力量都置 0） | 不用，绝不当零 |
| `unit_incomplete` | 该单位不完整：部分渔获只用另一单位报告（IATTC Num/Mt），或同一分层另一单位为正而本单位写 0（WCPFC、ICCAT） | 不用 |
| `no_catch_info` | 保留给只有努力量的记录；当前 ICCAT 只有努力量的分层不生成渔获行 | 不用 |

零值确认按报告组的**整个序列**判断，避免“当年没捕到就丢掉真零”；留空单元按报告组在所有年份写 0 的比例一次判定，不随年份跳变。`zeros` 参数：`"reported"`（默认，严格）、`"positives_only"`、`"with_inferred"`（加推断零，标记 `inferred_zero`）、`"with_unconfirmed"`、`"with_candidates"`（两者都加）。存在被排除的推断零时，过滤日志写入 `W01` 警告。努力量同样区分 `reported`、`missing`（含“0 编码的缺失”）、`masked`、`invalid`；只有 `reported`（> 0）进入 CPUE，按作业类型的 CPUE 只用作业类型专属的努力量（`effort_scope = "school"`）。

## 5. 物种、种群与默认单位

种群按**整格包含**判断：网格完全在边界内且来源对该区域有权威才归入；跨界网格只标记，不拆分。太平洋以 150°W 分开：IATTC 负责以东，WCPFC 负责以西，同一网格只取一个来源。SBF 只用 CCSBT。

| 物种 | 种群（`stock`） | 默认 CPUE | 说明 |
|---|---|---|---|
| YFT | YFT-EPO, YFT-WCPO, YFT-IO, YFT-AT | 延绳钓 尾/千钩；围网 吨/网次（按作业类型） | 竿钓仅探索 |
| BET | BET-EPO（指数域排除夏威夷角与 25°S 以南，标记）, BET-WCPO, BET-IO, BET-AT | 延绳钓 尾/千钩 | 围网 BET 不建议做指数（评估未使用） |
| SKJ | SKJ-EPO, SKJ-WCPO, SKJ-IO, SKJ-ATE, SKJ-ATW | 围网 吨/网次（按作业类型） | 延绳钓不建议；ATE/ATW 为 CATDIS 阶梯线 |
| ALB | ALB-NPO（赤道以北至 55°N）, ALB-SPO, ALB-IO, ALB-ATN（5°N 以北）, ALB-ATS, ALB-MED | 延绳钓 尾/千钩 | 季度指数（NPALB 使用 Q2） |
| PBF | PBF-PO | 不建议 | 公开数据无可用指数；只用作出现记录 |
| BFT | BFT-W（45°W 阶梯管理线）, BFT-E（含地中海） | 延绳钓，默认仅 JPN（尾数） | 管理区不等于种群来源 |
| SBF | SBF | 延绳钓 尾/千钩（CCSBT） | 表层数据为目测重量，不建议 |

地中海采用近似网格规则（中心在 5.6°W 以东、30–47.5°N、42°E 以西，排除比斯开湾），不是 ICCAT 的多边形；跨直布罗陀的网格被排除。完整规则与引用：`tuna_species_rules()`。

## 6. 默认排除与标记规则

| 编号 | 规则 | 处理 |
|---|---|---|
| X01 | IATTC 延绳钓 ESP 只报旗鱼，金枪鱼 0 不可信 | 排除 |
| X02 | IATTC 延绳钓 KOR、CHN 2023 努力量可疑 | 排除 |
| X03 | IOTC ELL（旗鱼）、LLCO（沿岸）不是金枪鱼延绳钓 | 排除 |
| X03b | IOTC PSS（小型围网） | 排除 |
| X04 | ICCAT LLSWO、LL-Shrk、LLHB | 排除 |
| X05 / X05b | WCPFC 围网 25°N 以北（日本温带围网）/ 25°S 以南（NZ/AU） | 排除（热带种） |
| X06 | CCSBT 台湾 1996 年前 SBF 由混合蓝鳍数据估算 | 排除 |
| X07 | IOTC 台湾延绳钓 2005 年前数据质量问题 | 标记 |
| X08 | 日本 EPO 延绳钓 1993–1995 HBF 变化 | 标记 |
| X09 | 日本 CCSBT 1995 年起为 RTMP 数据（序列断点，建议设时期项） | 标记 |
| X10 | ICCAT 工作组估计（WG-EST） | 排除 |
| X11 | ICCAT kg 与尾数记录的努力量不一致 | 排除 |
| X12 | ICCAT 象限可疑（解码到大西洋/地中海以外） | 排除 |
| X13 | ICCAT 同一分层被多个数据集重复报告：只保留一个（官方报告优先，其次 DSetID 最小） | 去重 |
| X14 | ICCAT 非官方报告文件类型 | 标记 |
| X15a / X15b | BET-EPO 延绳钓指数域：排除夏威夷角（10°N 以北、105°W 以西）与 25°S 以南（IATTC SAC-15-02） | 排除 |
| X16 | IOTC 秘书处估算的渔获（NEICE/NEIFR） | 排除 |

`apply_exclusions = FALSE` 可关闭全部默认排除；标记写在结果的 `qc_flags` 列，来源层面的问题写在 `notes` 列。

## 7. 各来源要点

- **IATTC**：延绳钓 Num（尾数优先）与 Mt（重量优先）是同一批分层的两个视图，已合并为一个分层并分别给出完整性；尾数指数用 Num。钩数为 0 实为缺失。KOR 1987–2001 两个单位记录同一批鱼。围网按船旗与按作业类型是两个边际划分，不能相加。
- **WCPFC**：努力量为 0 的行就是三船规则遮蔽层（围网/竿钓遮蔽行常保留船旗代码），一律 masked。HHOOKS 是百钩。围网 DAYS 覆盖整个分层（捕捞+搜寻），不能按作业类型拆或累加；网次为估计值（含小数）。150°W 以东网格归 IATTC。
- **IOTC**：2026 版 CE 分为 CA（只列正渔获）和 EF（努力量），按 K6/S7 键连接；同一分层可能有多个努力量单位，不相加。部分规则网格代码其实代表不规则区域（如 2120060 = 阿曼，伊朗 6125045 等，马尔代夫环礁），标为 `surrogate_area`，空间分析时排除。尺寸码 1 = 30°×30°（IOTC 自己的定义）。
- **ICCAT**：T2CE 的 kg 行与尾数行（“nw”数据集）合并为一个分层，努力量只取一次；只有努力量的记录单独成层；两种努力量类型分开。NULL 与 0 不同；数据集从不报告的物种写 0，靠报告组确认。坐标按象限与“靠近原点的角”解码。kg 可能是加工后重量（GG/DR），本版本未从详细目录逐数据集标记，按重量建模时需注意。
- **CCSBT**：坐标为网格**西北角**。延绳钓 SBF 空单元不是 0；JP/TW 很少写显式 0，空单元按报告组判为候选零。网格吨数文件（CatchByYMGOLoLa）没有努力量，不用于 CPUE。

## 8. 输出字段

`cpue_table()` 每行一个原生分层：`species_code, stock_id, source, product, gear_group, gear_raw, fleet, school_type, year, month_start, month_end, quarter, time_resolution, west, south, grid_width, grid_height, lon, lat, effort, effort_unit, catch, catch_unit, catch_status, cpue, cpue_scale, positive, qc_flags, stock_flag, notes, stratum_id, source_rows, dataset_id, source_sha256`。`composition = TRUE` 时加入同单位其他物种渔获（`comp_*`），可用于目标种聚类。

`sdm_table()` 另有 `presence`（1/0）与 `offset_log_effort`。不指定 `gear` 时只用建议的渔具（`allow_unrecommended = TRUE` 才加入其他渔具）；零值不能当作该物种未出现的渔具（SKJ 延绳钓、PBF）只提供出现记录。CPUE 专用的船队限制（如 BFT 仅 JPN）不用于 SDM。设定 `grid` 和 `period` 后只汇总**完全嵌套**于目标网格、且保留下来的分层（不拆分粗网格）：出现 = 任一为正，未出现 = 保留分层全部为允许的零；努力量与渔获只在同一单位内求和，并保留 `n_inferred_zero`、`n_flagged`、`qc_flags` 等 QC 计数。`period = "month"` 会去掉季度产品（如 WCPFC 围网），日志 `A02w` 会提示。`gfw = TRUE`（须同时给出 `grid` 与 `period`）加入该来源自己 RFMO 区域的 GFW 表观捕鱼小时（`gfw_hours`），不同 RFMO 区域重叠，绝不相加；`gfw_status`：`covered`（> 0 小时）、`covered_zero`（报告覆盖但无活动，记为 0）、`not_covered`（无报告：2012 年前或最新日期之后，NA）、`skipped`（文件校验失败且 `gfw_missing = "skip"`，NA）。

## 9. 公开数据无法复现的部分

逐网/逐船信息（船号、HBF、投钩深度、目标种）、观察员物种组成、评估使用的区域多边形和聚类、空间外推网格、渔获的年龄/体长换算都不在公开聚合数据中。建议：把船旗、作业类型、产品作为可捕性项；用 `composition` 构造目标种代理；对已知断点（X07–X09）设时期项；做努力量增长（effort creep）敏感性分析；只在有采样的范围内预测。

## 10. 参考

IATTC SAC-15-02、SAC-15-04、SAC-16-03；WCPFC 公共 catch/effort 数据说明（Longline/Purse_seine/Pole_and_line.pdf）与 SPC 评估；IOTC CE 数据页、IOTC 秘书处 GIS 库 iotc-lib-core-gis-cwp、IOTC 联合 CPUE 工作文件；ICCAT T2CE、CATDIS、ABFT MSE 试验规范与 Rec. 22-09；ISC ALBWG 与 PBFWG 评估；CCSBT SBT 数据页与 MP CPUE 文件。各来源数据画像中的核对数字和注意事项保存在每次构建的 `adapter_qc.csv`。
