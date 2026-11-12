# tuna-data

供实验室研究使用的 **Python 数据接口 + R 调用层**。从五家金枪鱼 RFMO 的官网发现并下载公开数据，读取已有文件，并通过 Google Drive 私有同步目录或 NAS 保存和跨机器使用。

**本 Git 仓库只保存代码、测试和说明。数据、个人路径、私有云目录 ID 与 token 不提交到 GitHub。**

## 存储方式

```text
RFMO 官网 / GFW API / 已有下载文件
                  ↓
             tuna-data 接口
                  ↓
       数据库目录（独立于代码仓库）
       ├── manifest.json       来源、版本、日期、SHA-256
       ├── raw/                完整原始文件，可读名称与版本校验值
       ├── processed/annual/   标准化、合并结果与可复现版本锁
       ├── processed/spatial/  原始粒度空间明细、网格渔获/努力量、GFW 活动层
       ├── gfw_dataset.json    GFW 版本及可用时间范围
       └── gfw_sync_status.json
                  ↓
      本地硬盘 / Drive 同步目录 / 挂载的 NAS
```

Google Drive 保存私有数据，GitHub 保存可安装的接口。新电脑登录自己的 Drive，下载或同步 `library` 目录后设置路径即可。NAS 只需先挂载为系统目录；代码不依赖特定用户名、盘符或操作系统。

更新保留旧版本，文件写完并校验后才登记到目录。读取时默认检查 SHA-256。原始 ZIP、附带字典和说明均保留；读取 ZIP 不要求手工解压。

## 安装

需要 Python 3.11 或更新版本。

```bash
git clone https://github.com/Linbojun99/tuna_data.git
cd tuna_data
python3 -m venv .venv
# macOS/Linux:
source .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -e '.[excel,parquet]'
```

读取 ICCAT Task 2 的 Access 数据库还需系统工具 `mdbtools`：macOS 使用 `brew install mdbtools`，Debian/Ubuntu 使用 `sudo apt-get install mdbtools`。原始数据库下载和保存不需要此工具。

## 设置路径

优先级：**调用参数 > `TUNA_DATA_HOME` 环境变量 > 配置文件 > 默认目录**。

```python
from tuna_data import Library

db = Library("/Volumes/NAS/research/tuna-library")
# Windows: Library("Z:/research/tuna-library")
# Linux:   Library("/mnt/lab-nas/tuna-library")
# Drive:   Library("/你的Drive同步目录/tuna-data-library/library")
print(db.catalog()[["id", "filename", "bytes", "retrieved_at"]])
```

或设置环境变量后在所有项目中调用同一目录：

```bash
export TUNA_DATA_HOME="/Volumes/NAS/research/tuna-library"
tuna-data where
tuna-data catalog
```

长期设置：把 `config.example.toml` 复制到 `~/.config/tuna-data/config.toml`，修改 `[storage].root`。可用 `TUNA_DATA_CONFIG` 指定其他配置文件。配置不依赖当前工作目录。

## 下载 RFMO 最新发布文件

```bash
# 先查看当前官网文件目录（不下载大文件）
tuna-data discover all

# 自动发现、下载、校验、保留历史版本
tuna-data update
# 指定来源
tuna-data update --sources iotc iccat
```

| 来源 | 当前适配范围 | 官方入口 |
|---|---|---|
| IATTC | 总渔获、围网、延绳钓、竿钓、兼捕和体长文件 | [Public domain](https://www.iattc.org/en-US/Data/Public-domain) |
| ICCAT | Task 1、Task 2、CATDIS、EFFDIS、体长/年龄相关产品、目录和标志数据下载链接 | [Statistical databases](https://www.iccat.int/en/accesingdb.html) |
| IOTC | 年度保留渔获、catch/effort、体长、参考表及索引页其他数据文件 | [Datasets](https://iotc.org/data/datasets) |
| WCPFC | 12 类聚合 C&E、年鉴原始文件、ACE 页面可选年份/船队/渔具文件、兼捕页下载文件 | [Scientific data](https://www.wcpfc.int/sustainability/scientific-data) |
| CCSBT | 总渔获、分洋区/网格渔获、延绳钓和表层作业、体长、ERS、CDS/TIS | [SBT data](https://www.ccsbt.org/en/content/sbt-data) |

这里的“完整”是**已支持官网入口当前列出的可下载文件完整保存**。它不代表各 RFMO 全部内部记录、未公开数据或每份历史评估报告的输入数据。官网也可能存在断链或尚未更新的栏目；失败会写入 `last_update.json`，命令返回非零状态，不会当作成功。

## 读取和复用清洗

```python
from tuna_data import Library
from tuna_data.cleaning import clean_columns, numeric_columns

db = Library()  # 使用已配置路径
catch = db.read_table("iattc/catchbyflaggear")
catch = clean_columns(catch)
catch = numeric_columns(catch, ["anoyear", "t"])
yft = catch.loc[catch["especiesspecies"] == "YFT"]

# 多表 ZIP 必须明确选择，避免混入 coverage 表或另一种单位的表
print(db.members("wcpfc/820"))
ll = db.read_table("wcpfc/820", member="WCPFC_L_PUBLIC_BY_YY_MM.csv")

# ICCAT Access：表名未指定时列出可用表
iccat = db.read_table("iccat/t2ce", table="t2ce", nrows=100)

# IOTC retained catch
iotc = db.read_table("iotc/iotc_datasets_rc_sci")

# 固定论文分析版本，不受未来更新影响
version = db.record("iattc/catchbyflaggear")["sha256"]
frozen = db.read_table("iattc/catchbyflaggear", version=version)

# 本地 CSV 或 Parquet，便于 R/其他软件继续分析
db.export_table("iattc/catchbyflaggear", "output/catch.parquet")
```

CSV 默认按字符串保留原始代码，包括前导零、`NA`、保密标志和实际零值。用 `numeric_columns` 对明确的数值列转换；未知标志会报错。年度渔获可用下述标准模板统一字段并按物种选择互补海域；网格采样表、不同努力量单位和 GFW 活动时数保持独立。

完整表读取需要足够内存。大表可用 `db.path()` 获取原文件，再使用 pandas 分块读取或 DuckDB；`read_table()` 返回一个已读取的表，不返回生命周期已结束的 ZIP 流。

## 标准清洗模板：全球、太平洋与分渔具数据

[完整方法与字段说明](docs/cleaning_pipeline.md) · [全球及太平洋配置](templates/annual_catch.toml) · [仅太平洋配置](templates/pacific_catch.toml) · [可直接运行的 R 示例](examples/r_clean_merge.R) · [Python 示例](examples/python_clean_merge.py)

```r
source("/你的代码目录/tuna_data/examples/r_clean_merge.R")
```

或使用命令行：

```bash
python -m pip install -e '.[excel,parquet]'
tuna-data build --config templates/annual_catch.toml
```

流程从已有库读取五家 RFMO 的年度表，保留原始字段和出处，统一渔具分类，输出七种主要金枪鱼的全球/太平洋明细、分渔具年度汇总、来源覆盖与缺值检查。热带种选 IATTC EPO + WCPFC WCPO；ALB/PBF 选南北太平洋视图，避免重复叠加。CCSBT 全球分渔具表只用于全球 SBF，不推算其船旗或太平洋分量。

结果默认存于私有 `library/processed/annual/`，每次生成独立目录和 `recipe.lock.toml`。R 中用 `tuna_processed("global_by_gear")` 读取最近一次成功结果，或传 `run = result$run_dir` 固定某一次。`TUNA_DATA_HOME` 和 `path=` 的 Drive/NAS 路径规则不变。迁移派生结果时复制完整 `processed/` 或在目标库重新构建；原有 `mirror/backup` 管理的是原始库。

**合并表保留 `catch_basis`**：保留渔获、登陆量、丢弃量和 WCPFC 年鉴口径不能默认混加为同口径全球总量。空白不会变成零，未识别渔具代码不会被删掉。最新年份的来源不足会明确标记，详见方法文档。

## 空间分布数据与地图

[空间模板与字段说明](docs/spatial_pipeline.md) · [空间配置](templates/spatial_2024.toml) · [可直接运行的 R 清洗及绘图示例](examples/r_spatial_workflow.R)

```r
source("/你的代码目录/tuna_data/examples/r_spatial_workflow.R")
# 或先 source("/你的代码目录/tuna_data/R/tuna_data.R") 后单独读取：
global_cells <- tuna_spatial("global_catch_cells")
pacific_cells <- tuna_spatial("pacific_catch_cells")
effort_cells <- tuna_spatial("rfmo_effort_cells")
gfw_cells <- tuna_spatial("gfw_cells")
```

默认从完整原始库构建 2024 年、七种金枪鱼的空间明细和网格表；年份可在 TOML 调整。支持 `tuna-data build-spatial --config templates/spatial_2024.toml` 和 R 的 `tuna_spatial_build()`。保留原始时间/网格分辨率、鱼种、渔具、单位、缺失/保密标记和来源行；捕捞吨数、尾数、努力量与 GFW 小时分开。空间来源不等同于年度总量，GFW RFMO 区域也不能重叠相加。地图提供全球来源面板、太平洋分区面板和独立 GFW 面板，只显示海岸线。

## GFW 历史数据和最新补下载

现有文件不需要 token；通过 `tuna-data import` 登记后即可读取。新增 API 下载从环境变量 `GFW_TOKEN` 或本机 `~/.config/tuna-data/gfw-token` 读取凭据，可用 `GFW_TOKEN_FILE` 指定另一路径。不要把凭据放入 Git、云端数据目录或 R 脚本。

```bash
# 五家 RFMO，2012 年至 API 当前日期；自动解析并记录实际数据版本
# 0.1°、月度、船旗×渔具，不限制为某一种渔具
tuna-data gfw-sync

# 单一区域/年份起点；已完成且校验通过的分区自动复用
tuna-data gfw-sync --rfmos IOTC --start-year 2024

# 同一版本可能修订历史数据；显式刷新保留旧文件
tuna-data gfw-sync --refresh

# 单独请求一个时间段
tuna-data gfw WCPFC 2024-01-01 2024-02-01
```

GFW 数据是 **AIS 推断的 apparent fishing hours**，不是分物种渔获吨数；RFMO 区域内的所有捕鱼活动也不全是金枪鱼作业。原始响应 ZIP、区域几何和 GFW 附带说明一起保存。空间重叠的 RFMO 报告不能直接相加。官方文档：[4Wings reports](https://api-doc.globalfishingwatch.org/our-apis/documentation/docs/v3/4wings/report)。

报告按次序下载，满足一个账号一次报告的 API 限制。网关超时后通过 `last-report` 查询并取回同一份报告，不重复提交。进度保存在本地；中断后继续同一命令即可恢复。此 token 不应同时在其他程序或网页提交报告。

每份响应还会核对 ZIP 内的日期、数据版本、分辨率与 RFMO 几何标识，避免把另一份报告登记为本次结果。读取和清洗已有文件不需要 token：

```python
from tuna_data.cleaning import gfw_hours

catalog = db.catalog()
gfw_id = catalog.loc[catalog["id"].str.startswith("gfw/iotc_2024-"), "id"].iloc[-1]
member = next(name for name in db.members(gfw_id) if name.endswith(".csv"))
hours = gfw_hours(db.read_table(gfw_id, member=member))
# lat / lon / fishing_hours 已转换为数值，其余原始代码保留
```

## R 调用

仓库附有两个可直接运行的脚本：

- [examples/r_quickstart.R](examples/r_quickstart.R)：查看目录，完整读取 IATTC 小表，预览 WCPFC、IOTC 和 GFW 大表，并演示清洗和固定文件版本。
- [examples/r_setup.R](examples/r_setup.R)：在新电脑创建独立 Python 环境，安装本接口及 Excel/Parquet 依赖。

### 已配置的电脑：直接读取

在一个新的 R 会话中运行（将代码目录改成自己的实际位置）：

```r
source("/你的代码目录/tuna_data/examples/r_quickstart.R")
```

脚本自动寻找 `~/.venvs/tuna-data` 或仓库内的 `.venv`，使用已配置的数据路径，不依赖当前工作目录。也可先设置 `RETICULATE_PYTHON` 指定已有环境。读取示例不会触发数据更新；读取 GFW 存量文件不需要 token。

运行后，R 中会留下 `catalogue`（目录）、`catch_raw`（完整 IATTC 原表）、`catch`（转换年份及数值列）、`yft`（黄鳍金枪鱼子集），以及 `ll_preview`、`iotc_preview`、`gfw_preview`。后三个对象是前 1000 行的预览；完整读取需去掉 `nrows = 1000L`。

也可以在自己的研究脚本中使用简短接口：

```r
# macOS/Linux；Windows 将后半段改为 /Scripts/python.exe
Sys.setenv(RETICULATE_PYTHON = path.expand("~/.venvs/tuna-data/bin/python"))
source("/你的代码目录/tuna_data/R/tuna_data.R")

catalogue <- tuna_catalog()
head(catalogue[c("id", "source", "filename")])
subset(catalogue, source == "iotc")
catch <- tuna_read("iattc/catchbyflaggear")
head(catch)

tuna_members("wcpfc/820")
ll <- tuna_read("wcpfc/820", member = "WCPFC_L_PUBLIC_BY_YY_MM.csv", nrows = 1000L)
```

原始 CSV 的代码和缺值标志保留为字符；示例复用 Python 的保守清洗函数，只转换指定的数值列。GFW 输出中的 `fishing_hours` 是活动时数，不能当作渔获吨数。

### 换电脑：安装代码并指向已有数据

1. 安装 R 与 Python 3.12（安装脚本自动寻找已安装的 Python 3.11–3.13），下载整个代码仓库。
2. 同步或复制整个 `library` 数据目录，保留其中的 `manifest.json`、`raw/` 及其他文件。代码目录和数据目录可以分开存放。
3. 在 R 中运行一次安装，再指定新电脑的数据位置：

```r
source("/新电脑代码目录/tuna_data/examples/r_setup.R")

Sys.setenv(TUNA_DATA_HOME = "/新电脑Drive同步目录/tuna-data-library/library")
stopifnot(file.exists(file.path(Sys.getenv("TUNA_DATA_HOME"), "manifest.json")))
source("/新电脑代码目录/tuna_data/examples/r_quickstart.R")
```

Windows 使用正斜杠路径，例如 `C:/research/tuna_data/examples/r_setup.R` 和 `G:/My Drive/tuna-data-library/library`。安装脚本会自动选用环境内的 `Scripts/python.exe`。

`Sys.setenv()` 只影响当前 R 会话。长期使用可把路径设置放在自己的研究脚本开头，或将 `TUNA_DATA_HOME="/你的数据目录/library"` 写入用户的 `~/.Renviron` 后重启 R。已有的 `~/.config/tuna-data/config.toml` 也继续有效；环境变量优先于该配置文件。安装脚本不会自动改写这些配置文件。

Python 路径须在首次加载 Python 前设置；若 R 已加载另一个 Python，先重启 R 再运行。参见 [reticulate 环境选择说明](https://rstudio.github.io/reticulate/articles/versions.html)。仅更换数据路径不需要重启 R，但已有的 `db` 对象需重新创建。

R 封装会在每次调用时同步 `TUNA_DATA_HOME` 与 `TUNA_DATA_CONFIG`，因此启动 Python 后再修改或取消 R 中的路径设置也能生效。

### 使用 NAS：先复制完整库，再切换路径

先在操作系统中挂载 NAS。以下代码由用户选择实际挂载点后运行；默认数据源应仍指向原来的完整 Drive/本地库：

```r
source("/你的代码目录/tuna_data/R/tuna_data.R")
# 首次使用时也要先设置 RETICULATE_PYTHON，见上文。
nas_mount <- "/Volumes/NAS"    # Windows: "Z:/"；Linux: "/mnt/lab-nas"
stopifnot(dir.exists(nas_mount))
nas_dir <- file.path(nas_mount, "research", "tuna-library")

db <- tuna_library()          # 原来的完整数据源
stopifnot(file.exists(file.path(db$root$as_posix(), "manifest.json")))
invisible(db$mirror(nas_dir))  # 首次复制；以后增量复制，校验文件并保留历史

Sys.setenv(TUNA_DATA_HOME = nas_dir)
db <- tuna_library()          # 重新创建后指向 NAS
print(db$root$as_posix())
catch <- tuna_read("iattc/catchbyflaggear")

# NAS 已有完整库时，跳过 mirror，直接设路径。
# 单次调用也能指定 path=，优先于环境变量，不改变默认设置：
catch_nas <- tuna_read("iattc/catchbyflaggear", path = nas_dir)
```

路径必须指向含有 `manifest.json` 的目录，不能只复制一个 CSV，也不能填 Google Drive 网页链接。选择 NAS 路径本身不会自动搬移原有数据；先完成上述复制或同步步骤。

不使用 reticulate 时，先运行 `tuna-data export` 生成 CSV，再用 R 的 `read.csv(..., colClasses="character", na.strings=NULL)` 读取。

## R 绘图示例：年度渔获与空间分布

[examples/r_plot_catch_and_gfw.R](examples/r_plot_catch_and_gfw.R) 使用完整原始表生成两张图及其汇总 CSV：

- IATTC 黄鳍金枪鱼（YFT）：按年合计各船旗、渔具的保留渔获吨数，展示源表中的全部年份。
- GFW：默认选择 IOTC 区域 2024 年完整报告，将各月、船旗、渔具的活动时数按报告给出的坐标中心归并到 1° 网格，用对数色标画空间分布。

```r
# 首次缺少绘图依赖时安装
install.packages(c("ggplot2", "sf", "rnaturalearth", "rnaturalearthdata",
                   "ragg", "svglite", "systemfonts", "jsonlite"))

source("/你的代码目录/tuna_data/examples/r_plot_catch_and_gfw.R")
# R 中保留 p_yft、p_gfw、annual_yft 和 gfw_grid，可继续修改图形或分析。
```

默认输出到代码仓库的 `reports/figures/`，包括两张 600 dpi PNG、两张可编辑文字的 SVG、两个汇总 CSV、`plot_provenance.json`（数据 ID、SHA-256、请求参数、汇总检查、绘图样式）及 R 环境记录。输出目录已被 Git 忽略。原始数据库文件保持不变。

绘图默认采用简洁的 Nature/Science 风格：英文图题、坐标轴及图例，白底、细线、统一无衬线字体，图宽 183 mm；地图先合并陆地多边形，仅绘制海岸线，不显示国境线。年度曲线使用蓝色，活动地图使用色觉友好的 cividis 连续色标。项目约定记录在 [AGENTS.md](AGENTS.md)，设计参考 [Nature 图形指南](https://research-figure-guide.nature.com/figures/preparing-figures-our-specifications/)；具体投稿时再按目标期刊调整尺寸与字号。

可在运行前修改区域、完整年份、展示网格和输出路径：

```r
Sys.setenv(TUNA_PLOT_RFMO = "IATTC", TUNA_PLOT_YEAR = "2024",
           TUNA_PLOT_GRID = "1", TUNA_PLOT_OUT = "~/tuna-plots")
source("/你的代码目录/tuna_data/examples/r_plot_catch_and_gfw.R")
```

展示网格可选 0.5、1、2 或 5 度。脚本只匹配完整年度、无额外过滤的月度 0.1° 报告，读取时不设置 `nrows`；没有匹配报告会明确报错，不自动下载或使用部分年度。数据目录仍由 `TUNA_DATA_HOME` 或配置文件指定，已有 GFW 文件不需要 token。

IATTC 源文件中的 0 表示低于 0.5 吨，年度图未对历史统计口径变化作校正。GFW 图展示所有渔具的 AIS 推断捕鱼活动，不是金枪鱼渔获量；色标为每个展示网格的时数总和，未按面积标准化。未记录网格留空，不能据此认定没有捕鱼。方法依据随数据保存的说明、[GFW 活动数据说明](https://globalfishingwatch.org/dataset-and-code-fishing-activity/)及 [4Wings 报告文档](https://api-doc.globalfishingwatch.org/our-apis/documentation/docs/v3/4wings/report)。

## R 绘图示例：合并后的全球与太平洋黄鳍渔获

[examples/r_plot_merged_yft.R](examples/r_plot_merged_yft.R) 读取同一次清洗结果的 `global_catch` 和 `pacific_catch`，生成两张按捕捞方式区分、不同统计口径分开展示的年度趋势图。

```r
# 已完成 examples/r_clean_merge.R 后运行；不重新下载或构建数据。
install.packages(c("ggplot2", "patchwork", "ragg", "svglite", "systemfonts", "jsonlite", "scales")) # 首次需要
source("/你的代码目录/tuna_data/examples/r_plot_merged_yft.R")
```

默认只画两个范围的预期来源都有记录的共同期间：当前为 **1950–2024 年**。2025 年仍保存在导出数据中，因为 ICCAT/WCPFC 尚无该年记录，默认不画出其不完整总量。脚本固定一个输入运行目录、核对输入校验值，跨年份缺值时断线，不补零、不平滑，也不把不同统计口径加在一起。

- 全球图：保留渔获（EPO + Indian）、WCPFC 年鉴值（WCPO）、ICCAT 登陆量（Atlantic）、ICCAT 原生 LF 类型及报告的死亡丢弃量分别展示。面板标明实际海域；它们不是各自覆盖全球的总量。
- 太平洋图：东太平洋保留渔获与西中太平洋年鉴值分开展示。WCPFC 曲线在两张图中相同。
- 六种主要渔具单列；其他已报告类别（包括混合渔具）归为 `Other reported gears`，`unknown` 和 `unmapped:*` 归为 `Unclassified codes`。展示分组只影响图，完整细分类另存 CSV。
- 所有面板都是线性纵轴，单位为千吨，各面板纵轴范围独立。使用英文标注、白底细线和固定色觉友好配色。

输出默认放在私有 `reports/figures/merged_yft/`，包括 600 dpi PNG、180 dpi 预览 PNG、可编辑 SVG、完整分渔具年度表、展示分组表、分组对照、来源覆盖和绘图版本记录。R 中保留 `p_global_yft`、`p_pacific_yft`、`annual_full_gears`、`annual_display` 和 `plot_data`，可继续修改。

```r
# 可选：固定某次合并结果、改输出位置或展示期间。
Sys.setenv(TUNA_YFT_RUN = "/完整运行目录/Annual_catch_...",
           TUNA_YFT_OUT = "/你的图片目录",
           TUNA_YFT_START = "1980", TUNA_YFT_END = "2024")
source("/你的代码目录/tuna_data/examples/r_plot_merged_yft.R")
# 不设置 TUNA_YFT_RUN 时，按 TUNA_DATA_HOME / 本机配置定位最新成功结果。
# 选择更晚年份也不会补全缺失来源；来源不足的年份留空。
```

## 私有云、NAS、迁移与完整备份

`raw/` 使用可读名称，例如 `raw/gfw/GFW_IOTC_Fishing_hours_2024_MONTHLY_0.1deg_v5.0__<版本校验值>/GFW_IOTC_Fishing_hours_2024_MONTHLY_0.1deg_v5.0.zip`。目录末尾保留 12 位校验值以区分数据版本；完整 SHA-256、来源文件名和请求参数仍保存在 `manifest.json`。新下载或导入的文件自动沿用此命名方式，已有数据集 ID 及 R/Python 调用方式保持稳定。

原始文件名位于目录表的 `filename` 字段，实际存储位置位于 `path` 字段。通过接口访问时由索引解析位置；迁移记录中的 `path_aliases` 可兼容 Drive 同步时新旧名称到达顺序不同的情况。手动批量改名需要同时更新索引，并保留名称对照表。

```bash
# 导入已有数据；复制并登记来源，不修改原文件
tuna-data import /路径/原始.zip --id iotc/my_archive

# 把整个数据目录增量复制到 Drive 桌面同步目录或已挂载 NAS
# 保留历史和目标目录其他数据，不删除任何已有文件
tuna-data mirror "/Drive同步目录/tuna-data-library/library"
tuna-data mirror "/Volumes/NAS/research/tuna-library"

# 可搬迁的完整版本包；包含当前与历史版本
tuna-data backup /备份位置/tuna-library-2026-11-11.zip
# 换电脑后恢复；所有文件校验通过才登记
tuna-data --root /新电脑/tuna-library restore /下载位置/tuna-library-2026-11-11.zip
```

Drive 桌面同步目录可直接作为 `TUNA_DATA_HOME`。同步完成后把需要离线使用的目录设为“可离线使用”；新机器登录有权限的账号即可下载，**不需要公开链接**。没有 Drive 桌面客户端的 Linux 机器可另行配置 rclone 的私有 Drive OAuth，然后使用 `rclone copy` 下载到本地目录。

**同一份 Drive 同步库只由一台机器更新**，其他机器读取；Drive 文件同步不提供跨机器事务锁。NAS 的本地文件锁能协调同一挂载上的更新，但不替代存储系统自身的并发保证。更新失败后，旧版本仍可读取。

自动化入口是 `scripts/update_private.py`：一次执行完成 RFMO 更新、可选 GFW 更新及目录备份，失败返回非零状态。可交给实验室已有的 cron、NAS 任务计划或其他作业调度器；默认不启用定时作业。

## 验证

```bash
python -m pip install -e '.[test,excel,parquet]'
pytest -q
```

测试覆盖版本固定、损坏检测、ZIP 多表选择、目录迁移、路径优先级、缺值与零值区别、官网链接解析和路径越界防护。年度模板另测试地域视图去重、渔获口径分组、缺失来源、新表结构检查、版本锁重放及渔具自定义对照。GitHub Actions 仅运行合成测试数据，不接触个人数据目录或 token。

R 的路径切换回归测试也在 GitHub Actions 中运行。已配置 R/Python 的本机可在 R 中执行 `source("tests/test_r_paths.R")`；该测试使用临时生成的小表，检查 Python 启动后更改路径、取消覆盖、配置文件切换、含空格路径和迁移后的数据保留。
