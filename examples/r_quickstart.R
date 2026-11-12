# R / RStudio 可直接运行的读取示例（不下载、不更新、不修改原始数据）。
# 在全新 R 会话中执行：source("/你的代码目录/tuna_data/examples/r_quickstart.R")
# 也可以在终端执行：Rscript /你的代码目录/tuna_data/examples/r_quickstart.R
# 新电脑先运行同目录的 r_setup.R，再设置 TUNA_DATA_HOME。

# 1. 找到本脚本所在的代码仓库，不依赖 setwd()。
source_files <- Filter(function(x) is.character(x) && length(x) == 1L && !is.na(x),
                       lapply(sys.frames(), function(frame) frame$ofile))
script_file <- if (length(source_files)) tail(source_files, 1L)[[1L]] else NULL
if (is.null(script_file)) {
  file_arg <- grep("^--file=", commandArgs(), value = TRUE)
  if (length(file_arg)) script_file <- sub("^--file=", "", file_arg[[1L]])
}
if (is.null(script_file) || !file.exists(script_file)) {
  stop("请用 source(本脚本的完整路径) 或 Rscript 运行整个脚本。")
}
repo_dir <- normalizePath(file.path(dirname(script_file), ".."), winslash = "/")
source(file.path(repo_dir, "R", "tuna_data.R"), local = TRUE)

# 2. 选择安装过 tuna-data 的 Python。
# 默认复用 ~/.venvs/tuna-data；也识别代码仓库中的 .venv。
# 如需手动指定，请先在 R 中运行：
# Sys.setenv(RETICULATE_PYTHON = "/你的虚拟环境/bin/python")
# Windows 示例：C:/Users/你的用户名/.venvs/tuna-data/Scripts/python.exe
if (!requireNamespace("reticulate", quietly = TRUE)) {
  stop("请先运行 examples/r_setup.R，或 install.packages('reticulate')。")
}
python_rel <- if (.Platform$OS.type == "windows") "Scripts/python.exe" else "bin/python"
python_bin <- Sys.getenv("RETICULATE_PYTHON", unset = "")
if (!nzchar(python_bin)) {
  candidates <- c(file.path(path.expand("~/.venvs/tuna-data"), python_rel),
                  file.path(repo_dir, ".venv", python_rel))
  available <- candidates[file.exists(candidates)]
  if (!length(available)) stop("没有找到 Python 环境；请先运行 examples/r_setup.R。")
  python_bin <- available[[1L]]
}
python_bin <- path.expand(python_bin)
if (!file.exists(python_bin)) stop("RETICULATE_PYTHON 指向的 Python 不存在：", python_bin)
python_key <- function(p) file.path(normalizePath(dirname(p), winslash = "/"), basename(p))
if (reticulate::py_available(initialize = FALSE) &&
    python_key(reticulate::py_config()$python) != python_key(python_bin)) {
  stop("当前 R 会话已经加载另一个 Python。请重启 R，再运行本示例。")
}
Sys.setenv(RETICULATE_PYTHON = python_bin)
reticulate::use_python(python_bin, required = TRUE)
if (!reticulate::py_module_available("tuna_data")) {
  stop("所选 Python 中没有 tuna-data；请用 r_setup.R 安装后重启 R。")
}

# 3. 设置数据位置。留空时使用本机配置文件中的默认路径。
# 换电脑/使用 NAS 时，在运行本脚本前设置环境变量即可：
# Sys.setenv(TUNA_DATA_HOME = "/你的Drive同步目录/tuna-data-library/library")
# Sys.setenv(TUNA_DATA_HOME = "/Volumes/NAS/research/tuna-library")
# Windows: Sys.setenv(TUNA_DATA_HOME = "Z:/research/tuna-library")
# Linux:   Sys.setenv(TUNA_DATA_HOME = "/mnt/lab-nas/tuna-library")
# 这里必须是包含 manifest.json 和 raw/ 的目录，不是 GitHub 代码目录。
db <- tuna_library()
data_dir <- db$root$as_posix()
if (!file.exists(file.path(data_dir, "manifest.json"))) {
  stop("该路径下没有 manifest.json：", data_dir,
       "\n请同步/复制完整的 library 目录，并设置 TUNA_DATA_HOME。")
}
cat("Python：", python_bin, "\n数据目录：", data_dir, "\n", sep = "")

# 4. 查看全部数据集；id 是读取时使用的名称。
catalogue <- tuna_catalog()
print(head(catalogue[c("id", "source", "filename", "bytes")], 12L))
print(table(catalogue$source))
iotc_catalogue <- subset(catalogue, source == "iotc")
print(iotc_catalogue[c("id", "filename")])
# RStudio 中可用 View(catalogue) 打开完整目录。

# 5. 完整读取 IATTC 小表（约 1.4 万行），保留字符型代码。
catch_raw <- tuna_read("iattc/catchbyflaggear")
print(head(catch_raw))
str(catch_raw)
# 复用已有清洗函数，只转换明确的数值列；未知代码报错，不填为零。
cleaner <- reticulate::import("tuna_data.cleaning", convert = TRUE)
catch <- cleaner$numeric_columns(catch_raw, list("AnoYear", "t"))
yft <- catch[catch$EspeciesSpecies == "YFT", , drop = FALSE]
print(head(yft))

# 6. 多表 ZIP：先列出成员，再明确选择数据表。
print(tuna_members("wcpfc/820"))
ll_preview <- tuna_read("wcpfc/820", member = "WCPFC_L_PUBLIC_BY_YY_MM.csv", nrows = 1000L)
iotc_preview <- tuna_read("iotc/iotc_datasets_rc_sci", nrows = 1000L)
print(head(ll_preview))
print(head(iotc_preview))
# 这两个对象仅是前 1000 行的预览。完整读取时删除 nrows 参数。
# R 向 Python 传递行数时使用整数，例如 1000L。

# 7. 读取 IOTC 区域 2024 年 GFW 数据，并复用时数/坐标清洗。
# 数据 ID 含请求哈希，用目录查找；不把整段 ID 写死在研究脚本中。
gfw_candidates <- catalogue[startsWith(catalogue$id, "gfw/iotc_2024-"), , drop = FALSE]
profile_matches <- vapply(gfw_candidates$id, function(id) {
  p <- db$record(id)$provenance$request
  identical(p[["temporal-resolution"]], "MONTHLY") &&
    identical(p[["spatial-resolution"]], "LOW") &&
    identical(p[["spatial-aggregation"]], "false") &&
    identical(p[["group-by"]], "FLAGANDGEARTYPE")
}, logical(1L))
gfw_candidates <- gfw_candidates[profile_matches, , drop = FALSE]
if (!nrow(gfw_candidates)) stop("当前库缺少 IOTC 2024 年的月度 0.1° GFW 报告。")
# 多份匹配时选择最近入库的报告，并显示其实际请求参数。
gfw_candidates <- gfw_candidates[order(gfw_candidates$retrieved_at, decreasing = TRUE), , drop = FALSE]
gfw_id <- gfw_candidates$id[[1L]]
print(db$record(gfw_id)$provenance$request)
gfw_members <- unlist(tuna_members(gfw_id), use.names = FALSE)
gfw_csv <- grep("[.]csv$", gfw_members, value = TRUE, ignore.case = TRUE)
if (length(gfw_csv) != 1L) stop("GFW ZIP 中有多份 CSV，请明确选择成员。")
gfw_raw <- tuna_read(gfw_id, member = gfw_csv[[1L]], nrows = 1000L)
gfw_preview <- cleaner$gfw_hours(gfw_raw)
print(head(gfw_preview))
# fishing_hours 是捕鱼活动时数，不是渔获重量；本对象仅是 1000 行预览。
# 读取已有 GFW 文件无需 token。

# 8. 记录原始文件的 SHA-256，后续可用 version= 固定分析版本。
catch_sha256 <- db$record("iattc/catchbyflaggear")$sha256
catch_pinned_preview <- tuna_read("iattc/catchbyflaggear", version = catch_sha256, nrows = 5L)
cat("IATTC 文件版本：", catch_sha256, "\n", sep = "")
cat("完成。R 中已生成 catalogue、catch_raw、catch、yft、ll_preview、iotc_preview、gfw_preview。\n")

# 9. 单次调用也可指定另一位置，不改变默认路径：
# tuna_catalog(path = "/Volumes/NAS/research/tuna-library")
# tuna_read("iattc/catchbyflaggear", path = "/Volumes/NAS/research/tuna-library")
# 若修改了 TUNA_DATA_HOME，重新执行 db <- tuna_library() 才会更换已有 db 对象。
