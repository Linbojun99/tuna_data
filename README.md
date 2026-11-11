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
       ├── raw/                完整原始文件，按内容哈希保存
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

CSV 默认按字符串保留原始代码，包括前导零、`NA`、保密标志和实际零值。用 `numeric_columns` 对明确的数值列转换；未知标志会报错。当前版本不把不同来源强行拼成一张“全球总渔获表”，也不自动统一未知努力量单位、展开季度数据或重复加总跨物种努力量。数据库格式和具体分析口径是两层工作。

完整表读取需要足够内存。大表可用 `db.path()` 获取原文件，再使用 pandas 分块读取或 DuckDB；`read_table()` 返回一个已读取的表，不返回生命周期已结束的 ZIP 流。

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

报告按次序下载，满足一个账号一次报告的 API 限制。若服务器仍在运行或返回超时，命令停止并记录进度，避免连续提交冲突请求；稍后继续同一命令即可复用已完成分区。

## R 调用

```r
install.packages("reticulate")  # 只需一次
# 指向已经安装 tuna-data 的 Python，不要求所有电脑路径相同
Sys.setenv(RETICULATE_PYTHON = "/你的虚拟环境/bin/python")
source("R/tuna_data.R")

Sys.setenv(TUNA_DATA_HOME = "/Volumes/NAS/research/tuna-library")
catalog <- tuna_catalog()
catch <- tuna_read("iattc/catchbyflaggear")
ll <- tuna_read("wcpfc/820", member = "WCPFC_L_PUBLIC_BY_YY_MM.csv")
# 也可显式传 path=，覆盖环境变量
```

不使用 reticulate 时，先运行 `tuna-data export` 生成 CSV，再用 R 的 `read.csv(..., colClasses="character", na.strings=NULL)` 读取。

## 私有云、NAS、迁移与完整备份

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
python -m pip install -e '.[test]'
pytest -q
```

测试覆盖版本固定、损坏检测、ZIP 多表选择、目录迁移、路径优先级、缺值与零值区别、官网链接解析和路径越界防护。GitHub Actions 仅运行合成测试数据，不接触个人数据目录或 token。
