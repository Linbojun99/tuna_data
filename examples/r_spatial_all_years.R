# 全部可用年份：构建一次，R 按年/鱼种/渔具筛选；不一次加载所有历史明细。
# source("/你的代码目录/tuna_data/examples/r_spatial_all_years.R")
# 可在运行前切换库路径；目标库需完整复制 processed/spatial 目录。
# Sys.setenv(TUNA_DATA_HOME = "/Volumes/NAS/research/tuna-library")
# 已有完整历史结果时仅读取；需要重新构建时设 TUNA_SPATIAL_BUILD="1"。
# 首次准备原始数据：tuna-data update；tuna-data gfw-sync。

source_files <- Filter(function(x) is.character(x) && length(x) == 1L && !is.na(x),
                       lapply(sys.frames(), function(frame) frame$ofile))
script_file <- if (length(source_files)) tail(source_files, 1L)[[1L]] else NULL
if (is.null(script_file)) {
  file_arg <- grep("^--file=", commandArgs(), value = TRUE)
  if (length(file_arg)) script_file <- sub("^--file=", "", file_arg[[1L]])
}
if (is.null(script_file) || !file.exists(script_file)) stop("请用 source(完整路径) 或 Rscript 运行整个脚本。")
repo_dir <- normalizePath(file.path(dirname(script_file), ".."), winslash = "/")
source(file.path(repo_dir, "R", "tuna_data.R"), local = TRUE)
required <- c("reticulate", "jsonlite")
missing <- required[!vapply(required, requireNamespace, logical(1L), quietly = TRUE)]
if (length(missing)) stop("缺少绘图依赖：install.packages(c(", paste(sprintf('"%s"', missing), collapse = ", "), "))")

python_rel <- if (.Platform$OS.type == "windows") "Scripts/python.exe" else "bin/python"
python_bin <- Sys.getenv("RETICULATE_PYTHON", unset = "")
if (!nzchar(python_bin)) {
  candidates <- c(file.path(path.expand("~/.venvs/tuna-data"), python_rel), file.path(repo_dir, ".venv", python_rel))
  candidates <- candidates[file.exists(candidates)]
  if (!length(candidates)) stop("请先运行 examples/r_setup.R。")
  python_bin <- candidates[[1L]]
}
python_bin <- path.expand(python_bin)
if (!file.exists(python_bin)) stop("Python 路径不存在：", python_bin)
python_key <- function(p) file.path(normalizePath(dirname(p), winslash = "/"), basename(p))
if (reticulate::py_available(initialize = FALSE) &&
    python_key(reticulate::py_config()$python) != python_key(python_bin)) stop("请重启 R 后选择安装了 tuna-data 的 Python。")
Sys.setenv(RETICULATE_PYTHON = python_bin)
reticulate::use_python(python_bin, required = TRUE)
db <- tuna_library()

run <- path.expand(Sys.getenv("TUNA_SPATIAL_RUN", unset = ""))
if (!nzchar(run)) {
  parent <- file.path(db$root$as_posix(), "processed", "spatial")
  latest_path <- file.path(parent, "latest.json")
  if (file.exists(latest_path)) {
    candidate <- file.path(parent, jsonlite::read_json(latest_path)$run_id)
    meta <- jsonlite::read_json(file.path(candidate, "provenance.json"))
    if (isTRUE(meta$recipe$all_years)) run <- candidate
  }
  if (!nzchar(run) || identical(Sys.getenv("TUNA_SPATIAL_BUILD"), "1")) {
    result <- tuna_spatial_build(file.path(repo_dir, "templates", "spatial_all_years.toml"))
    run <- result$run_dir
  }
}

# 每种鱼、每种渔具各来源实际有什么年份；无记录年份不会填零。
available <- tuna_spatial_catalog(run = run)
source_years <- tuna_spatial("source_years", run = run)
gfw_coverage <- tuna_spatial("gfw_coverage", run = run)
print(unique(available[c("source", "year", "species_code", "gear_group", "unit")])[1:20, ])
print(gfw_coverage[gfw_coverage$period_status == "partial_year",
                   c("rfmo", "year", "available_through", "period_status")])

# 1. 全球来源集合，2020–2024 黄鳍、延绳钓与围网；单位选吨数。
# 数据保留 source / catch_basis，不能把不同口径直接当成统一全球总渔获。
global_yft <- tuna_spatial("global_catch_cells", run = run,
  year = 2020:2024, species = "YFT", gear = c("longline", "purse_seine"), unit = "tonnes")

# 2. 太平洋范围，可以同时筛多鱼种；year 向量是所选年份，冒号表示连续区间。
pacific_tuna <- tuna_spatial("pacific_catch_cells", run = run,
  year = 2010:2024, species = c("YFT", "BET"), gear = "longline", unit = "tonnes")

# 3. 需要原始月份/季度、船旗、原生网格时读取 native；先筛选再转为 R 数据框。
native_yft <- tuna_spatial("rfmo_native", run = run,
  year = 2024, species = "YFT", gear = "longline", source = "IATTC", unit = "tonnes", measure = "catch")

# 4. GFW 使用自己的渔具分类，不含鱼种标签。保留 RFMO，重叠区域不能跨区求和。
gfw_longline <- tuna_spatial("gfw_cells", run = run, year = 2020:2024,
  rfmo = "IOTC", gear = "drifting_longlines")
latest_gfw_year <- max(gfw_coverage$year)
gfw_recent <- tuna_spatial("gfw_cells", run = run, year = latest_gfw_year, rfmo = "IOTC")
# 排除未结束年度：include_partial=FALSE。report_end_exclusive 是不含当天的右端点。
gfw_full_years <- tuna_spatial("gfw_cells", run = run,
  year = (latest_gfw_year - 1):latest_gfw_year, rfmo = "IOTC", include_partial = FALSE)

# 5. 用 columns 减少传入 R 的字段；所选来源、口径与缺失数量仍保留。
small_table <- tuna_spatial("global_catch_cells", run = run,
  year = 2024, species = "YFT", gear = "purse_seine", unit = "tonnes",
  columns = c("year", "source", "catch_basis", "species_code", "gear_group", "map_lon", "map_lat", "observed_value", "n_missing"))
print(head(small_table))
cat("固定历史结果目录：", run, "\n", sep = "")
cat("后续读取继续传 run=run；换 Drive/本地/NAS 库时设置 TUNA_DATA_HOME 或 path=。\n")

