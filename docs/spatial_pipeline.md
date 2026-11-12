# 空间数据标准模板

此模板从完整原始库构建 RFMO 渔获/努力量空间表和 GFW 捕鱼活动表。默认演示 **2024 年、七种主要金枪鱼、五个 GFW RFMO 区域**；其他年份在配置中调整。原始历史文件仍完整保留，默认运行不会把所有历史年份都重复展开为派生表。

## 全部可用年份与筛选接口

用 [spatial_all_years.toml](../templates/spatial_all_years.toml) 构建全部已支持空间产品的可用历史年份：

```bash
tuna-data gfw-sync
tuna-data build-spatial --config templates/spatial_all_years.toml
```

`all_years=true` 自动取各来源实际年份，不用手工维护起止年。`include_partial_gfw=true` 包含当前年已公布部分，保留明确的请求起止日期；同一 RFMO/年份仅选择一个覆盖最长的报告，不叠加旧的短窗口。完整年使用 `period_status=full_calendar_year`，未完整年为 `partial_year`；这里的完整指请求涵盖完整日历年，不表示 AIS 观测覆盖全部实际捕鱼活动。缺失的 RFMO/年份报告会报错，不能静默跳过。

RFMO 原生明细和网格结果按年写成 Parquet 文件，GFW 原生记录按 RFMO/年写成文件。R 的筛选条件在 Arrow 读取阶段生效，避免先载入所有历史记录再筛选；默认校验被选中文件的 SHA-256。旧的单文件 2024 年结果仍可读取。七种金枪鱼和所选产品保持与 2024 模板相同；其他产品、替代视图及其他鱼种仍保存在完整原始库，不重复叠加。

```r
source("/你的代码目录/tuna_data/examples/r_spatial_all_years.R")

available <- tuna_spatial_catalog() # 实际可用的年份、鱼种、渔具、来源和单位
x <- tuna_spatial("global_catch_cells", year = 2000:2025,
                  species = "YFT", gear = c("longline", "purse_seine"), unit = "tonnes")
p <- tuna_spatial("pacific_catch_cells", year = c(2010, 2024),
                  species = c("YFT", "BET"), gear = "longline", unit = "tonnes")
native <- tuna_spatial("rfmo_native", year = 2024, source = "IATTC",
                      species = "YFT", gear = "longline", unit = "tonnes", measure = "catch")

# GFW 没有鱼种标签，不能设置 species；使用其原始渔具分类。
g <- tuna_spatial("gfw_native", year = 2024, rfmo = "IOTC", gear = "drifting_longlines")
g_cells <- tuna_spatial("gfw_cells", year = 2020:2026, rfmo = "IOTC", include_partial = FALSE)
head(tuna_spatial("gfw_coverage")) # available_through 与 report_end_exclusive

# 只取所需列：读取后仍保留口径和缺值数量。
small <- tuna_spatial("global_catch_cells", year = 2024, species = "YFT", unit = "tonnes",
                     columns = c("year", "source", "catch_basis", "gear_group", "map_lon", "map_lat", "observed_value", "n_missing"))
```

`year=2010:2024` 表示连续年份，`year=c(2010,2024)` 仅选两年。鱼种用 FAO 代码（如 YFT/BET/SKJ），RFMO 渔具用标准组（如 longline/purse_seine/pole_and_line）。GFW 保留其原生分类（如 drifting_longlines/tuna_purse_seines）；可先查看 `unique(subset(available, source == "gfw")$gear_group)`。GFW 的鱼种筛选会明确报错，避免把捕鱼活动误当成特定鱼种。`include_partial=FALSE` 排除已标记的 GFW 未完整年，不给 RFMO 记录推断“全年覆盖完整”。

`source_years` 列出各 RFMO 产品每年源记录数和展开记录数；`coverage` 列出标准化后的来源/年份/鱼种/渔具/单位及缺值数量；`gfw_coverage` 列出每份报告的实际日期范围。无记录的年份不补零。固定 `run=` 可锁定一次完整处理结果；复制完整运行目录（包含子目录）及 `latest.json` 后可在 Drive、本地或 NAS 用同一接口调用。直接读取所有历史 native 表可能需要大量内存，通常应先筛年份/来源。

## 运行和读取

```bash
python -m pip install -e '.[excel,parquet]'
tuna-data build-spatial --config templates/spatial_2024.toml
```

```r
# 已运行 examples/r_setup.R，Python 环境已安装 tuna-data。
source("/你的代码目录/tuna_data/R/tuna_data.R")

# 可选：切换至完整迁移后的本地、Drive 或 NAS 库。
Sys.setenv(TUNA_DATA_HOME = "/Volumes/NAS/research/tuna-library")

result <- tuna_spatial_build("/你的代码目录/tuna_data/templates/spatial_2024.toml")
run <- result$run_dir
global <- tuna_spatial("global_catch_cells", run = run)
pacific <- tuna_spatial("pacific_catch_cells", run = run)
effort <- tuna_spatial("rfmo_effort_cells", run = run)
gfw <- tuna_spatial("gfw_cells", run = run)
quality <- tuna_spatial("rfmo_quality", run = run)

# 同一数据表中同时保留来源、统计口径、单位和渔具；筛选后再分析。
yft_ll <- subset(pacific, species_code == "YFT" & gear_group == "longline" & unit == "tonnes")
head(yft_ll[c("source", "year", "map_lon", "map_lat", "observed_value", "n_missing", "catch_basis")])

# 原始粒度明细。大年份范围应使用 Python/Arrow 分批读取。
native <- tuna_spatial("rfmo_native", run = run)
gfw_native <- tuna_spatial("gfw_iotc_2024_native", run = run)

# 再次运行只读取结果并出图；结果不存在时会自动建立默认 2024 年结果。
source("/你的代码目录/tuna_data/examples/r_spatial_workflow.R")
```

Python 对应接口为 `tuna_data.spatial.build_spatial()` 和 `read_spatial()`。`path=` 优先于环境变量。换电脑需要复制完整库，或至少复制完整的 `processed/spatial/` 来读取已有结果；重建还需要对应的 `manifest.json` 和原始快照。`mirror/backup` 仅管理原始库，派生结果需单独复制。不要只复制 `latest.json`。

每次构建生成独立的 `Spatial_.../` 目录：原始来源的 SHA-256、下载日期、ZIP 成员、源文件行号、转换程序校验值、输出校验值和 `recipe.lock.toml`。仅成功后才更新 `latest.json`。论文分析建议固定 `run`；要重建相同来源，传入该运行的锁定配置。换年份可以复制 TOML 修改 `start_year`、`end_year`；只做 RFMO 数据可设 `gfw_rfmos = []`。2024 示例使用已有完整年度、逐月、未加额外过滤的 0.1° 报告；全历史配置另外保留有明确日期范围的未完整年。更新 Python 接口后请重启 R，再调用新增参数。

## 输出表

| 表名 | 粒度和用途 |
|---|---|
| `rfmo_native` | 所选年份/鱼种的来源原始时间与网格粒度；测量值长表，渔获与努力量分行 |
| `coverage` | 可用的来源、年份、鱼种、渔具、单位、口径，以及记录/缺值计数 |
| `source_years` | 各 RFMO 产品逐年输入行数及展开后测量行数 |
| `gfw_native` | 跨所有报告的逻辑明细表；按 RFMO/年筛选读取对应文件，不叠加重叠区域 |
| `gfw_coverage` | 每个 RFMO/年份的实际报告日期范围、完整/未完整年标记和记录数 |
| `global_catch_cells` | 全球来源集合，年度 5° 网格，来源 × 产品 × 鱼种 × 渔具 × 单位 × 统计口径分开 |
| `pacific_catch_cells` | 同上，保留互补的 IATTC EPO、WCPFC WCPO；CCSBT 明确标为 Pacific 的 SBF 记录可加入 |
| `rfmo_effort_cells` | 努力量的 5° 网格视图；钩数、网次、作业日和各来源原始单位分开 |
| `gfw_<rfmo>_<year>_native` | 原报告坐标、月份、船旗、渔具和 apparent fishing hours；每个 RFMO 独立文件 |
| `gfw_cells` | 年度展示网格（默认 1°），按 RFMO 和 GFW 渔具分别汇总 |
| `rfmo_quality` | 缺失、保密/疑似遮蔽、坐标缺失、粗网格、海域排除等的数量及已知数值 |
| `source_inventory` | 每个来源扫描行数、所选源记录行数和展开后的测量行数；没有目标年份时明确为零 |

**全球来源集合不是一个统计方法一致的全球总渔获估计。** 渔获与年度表可能因上报、抽样、保密、地理覆盖及 raising 不同而不一致。GFW 也不是渔获量：没有黄鳍等目标鱼种标签，反映 AIS 所见的 apparent fishing activity。

原始行的完整字段仍在原文件中；标准长表保留常用维度及定位原始行所需的 `dataset_id`、`source_sha256`、`source_member`、`source_row`、`value_field`。CSV 行号按表头为第 1 行计算，CCSBT 为实际工作表行号。附带字典、其他边际视图、Vessel IDs 等未进入标准字段的内容不会从原始库删除。**测量行数不等于船次、网次或独立样本数。**

## 坐标、单位和统计口径

| 来源 | 默认空间产品与定位 | 处理规则 |
|---|---|---|
| IATTC | LL 重量优先表、PS 按船旗表、LP 按船旗表；经纬度是 5°/1° 网格中心 | 保留渔获吨数；LL 只有尾数的记录另存 `fish` 残余口径，不换算重量；不叠加重量/尾数优先两视图或 PS 船旗/作业类型两视图 |
| WCPFC | LL 月/船旗 5°，PS 与 LP 季度/船旗 1°；坐标是网格西南角 | 季度不展开成三个相同月份；HHOOKS × 100 为钩数；DAYS 不随鱼种重复。空船旗或整条测量全零的层保守标为遮蔽/不确定，保留原始零，不当作观测零 |
| IOTC | CE 中 CA 与 EF 分开；7 位 CWP 码解码 | MT 与 NO 分别为吨和尾数；EF 不与每个鱼种重复连接。CWP 用靠近赤道/本初子午线的角点及象限；不规则区域保留为未定位 |
| ICCAT | 最新已下载的 CATDIS all-species 发布，使用表中 5° 中心点 | Raised 至 nominal catches 的空间估计，包括登陆量与死亡丢弃；独立口径。季度保留；不叠加按鱼种 ZIP。锁定配置优先于最新发布 |
| CCSBT | CatchByYMGOLoLa；坐标为 5° 网格西北角 | 仅 SBF，吨数；无位置记录保留但不画入网格。只有来源明确写 Pacific 才进入太平洋表 |
| GFW | 已保存的 0.1° 月度 FLAGANDGEARTYPE 报告 | 保留报告坐标；按坐标向下取整分箱至展示网格，不推断原生裁切格子的精确边界，不做面积归一化；保存响应中的真实 RFMO GeoJSON |

坐标规则依据 [IATTC 公共数据及 ZIP 内说明](https://www.iattc.org/en-US/Data/Public-domain)、[WCPFC 公共 catch/effort 数据及随附说明](https://www.wcpfc.int/sustainability/scientific-data/wcpfc-public-domain-aggregated-catcheffort-data-download-page)、[IOTC CE](https://iotc.org/data/datasets/latest/CE/All)、[FAO CWP 象限说明](https://www.fao.org/4/w9633e/w9633e0k.htm)、[ICCAT CATDIS 方法](https://www.iccat.int/Data/Catdis/Readme_catdis.pdf) 和 [CCSBT 数据说明](https://www.ccsbt.org/en/content/sbt-data)。

原生字段 `west/south/grid_width/grid_height` 表示矩形范围，`lon/lat` 表示中心；GFW 只保留报告坐标与标称分辨率，原生 `west/south` 留空。展示网格使用 `map_west/map_south/map_size` 及中心 `map_lon/map_lat`，经纬度按 WGS84 进行绘图，角度网格不代表等面积。海岸附近格子在图上用陆地遮罩显示，不重新分配渔获。

仅当原网格完整落入某个 5° 格子时才可向上聚合。大于 5° 或跨越目标格子的记录保存在 native/quality 中，不平均拆分。IOTC 的类型 2 为经度 20° × 纬度 10°，类型 4 使用其 20° × 20° 约定。无法定位的记录不能放到 (0, 0)。

太平洋用于空间拼接的互补分区：IATTC 150°W 以东、WCPFC 150°W 以西。原始重叠范围不会删除，而是标记后不进入该拼接视图。该分区不消除两个机构的统计口径、覆盖与保密差别；其他 RFMO 的地理重叠也没有被包装为全球去重估计。GFW RFMO 报告尤其存在大面积重叠，**`region_raw` 必须保留，不得跨 RFMO 相加**，也不能仅凭汇总后格子中心去重。

## 缺值及绘图默认

`observed_value` 只加已知值；全部缺失时为 NA。`value` 仅在所有输入测量都有值时返回，否则 NA。`n_records/n_observed/n_missing` 显示参与的测量数，即使 `value` 非缺失也不意味着某个 RFMO 的整体上报覆盖完整。源单位、渔具代码、未映射类别及 `value_status` 保留；不同单位不能相加。

R 示例默认黄鳍、延绳钓和围网，按来源和统计口径分面；导出的 CSV 包含该鱼种的全部渔具，完整 Parquet 包含配置中的全部鱼种。灰色为全缺值，浅蓝为已报告零，橙色十字为存在缺失成分的已知小计；空白表示没有纳入的记录。GFW 单独显示小时数，不推定鱼种或唯一船舶数。

地图只显示 dissolve 后的海岸线，英文标题、无背景网格、cividis 对数色标；太平洋视图跨日期变更线连续绘制。输出预览 PNG、600 dpi PNG、可编辑 SVG、绘图 CSV、R 会话和来源记录，默认保存在 Git 忽略的 `reports/figures/spatial/`。未来更换库路径无需修改绘图逻辑。
