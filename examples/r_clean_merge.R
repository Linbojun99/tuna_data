# 标准流程：原始快照 → 字段/渔具统一 → 地域去重选表 → 全球/太平洋 → 质量检查。
# 直接运行：source("/你的代码目录/tuna_data/examples/r_clean_merge.R")
# 新电脑先运行 examples/r_setup.R。无需重新下载已在 library 中的数据。
source_files <- Filter(function(x) is.character(x) && length(x) == 1L && !is.na(x),
                       lapply(sys.frames(), function(frame) frame$ofile))
script_file <- if (length(source_files)) tail(source_files, 1L)[[1L]] else NULL
if (is.null(script_file)) {
  file_arg <- grep("^--file=", commandArgs(), value = TRUE)
  if (length(file_arg)) script_file <- sub("^--file=", "", file_arg[[1L]])
}
if (is.null(script_file)) stop("请用 source(完整路径) 或 Rscript 运行整个脚本。")
repo_dir <- normalizePath(file.path(dirname(script_file), ".."), winslash = "/")
if (!requireNamespace("reticulate", quietly = TRUE)) stop("请先运行 examples/r_setup.R。")
python_rel <- if (.Platform$OS.type == "windows") "Scripts/python.exe" else "bin/python"
python_bin <- Sys.getenv("RETICULATE_PYTHON", unset = "")
if (!nzchar(python_bin)) {
  candidates <- c(file.path(path.expand("~/.venvs/tuna-data"), python_rel),
                  file.path(repo_dir, ".venv", python_rel))
  available <- candidates[file.exists(candidates)]
  if (!length(available)) stop("没有 Python 环境；请先运行 examples/r_setup.R。")
  python_bin <- available[[1L]]
}
Sys.setenv(RETICULATE_PYTHON = path.expand(python_bin))
reticulate::use_python(path.expand(python_bin), required = TRUE)
source(file.path(repo_dir, "R", "tuna_data.R"), local = TRUE)

# 1. 换电脑或 NAS 时，只需先设置实际路径。必须包含 manifest.json 和 raw/。
# Sys.setenv(TUNA_DATA_HOME = "/你的Drive同步目录/tuna-data-library/library")
# Sys.setenv(TUNA_DATA_HOME = "/Volumes/NAS/research/tuna-library")
# Windows: Sys.setenv(TUNA_DATA_HOME = "Z:/research/tuna-library")
# Linux:   Sys.setenv(TUNA_DATA_HOME = "/mnt/lab-nas/tuna-library")
db <- tuna_library()
stopifnot(file.exists(file.path(db$root$as_posix(), "manifest.json")))

# 2. 每次建立一个独立结果目录，默认放在当前 library/processed/annual/。
# 默认模板同时输出全球和太平洋；仅太平洋可改为 pacific_catch.toml。
recipe <- file.path(repo_dir, "templates", "annual_catch.toml")
result <- tuna_build(config = recipe)
run_dir <- result$run_dir
cat("本次结果：", run_dir, "\n", sep = "")

# 3. 读取完整明细与年度分渔具汇总。均保留 catch_basis（渔获口径）。
global_catch <- tuna_processed("global_catch", run = run_dir)
pacific_catch <- tuna_processed("pacific_catch", run = run_dir)
global_by_gear <- tuna_processed("global_by_gear", run = run_dir)
pacific_by_gear <- tuna_processed("pacific_by_gear", run = run_dir)
coverage <- tuna_processed("global_coverage", run = run_dir)
gear_crosswalk <- tuna_processed("gear_crosswalk", run = run_dir)
print(head(global_by_gear, 10L))
print(table(global_catch$source, global_catch$species_code))

# 4. 常用研究子集：黄鳍、延绳钓、围网；按需要保留船旗/船队、海域与来源。
pacific_yft <- subset(pacific_catch, species_code == "YFT")
longline <- subset(global_catch, gear_group == "longline")
purse_seine <- subset(global_catch, gear_group == "purse_seine")
yft_annual <- tuna_summarise(pacific_yft)
flag_gear <- tuna_summarise(pacific_yft, by = c(
  "year", "species_code", "source", "region", "flag_key", "fleet_raw", "gear_group", "catch_basis"
))
print(tail(yft_annual, 10L))

# 5. 检查缺失来源、未知渔具；不要用 na.rm=TRUE 把不完整数据当成完整总量。
missing_views <- subset(coverage, !source_view_present)
unknown_gears <- subset(gear_crosswalk, gear_mapping_status == "unmapped")
print(tail(missing_views, 10L))
print(unknown_gears)
# all_source_views_present 仅表示该物种当年预期来源表都有记录，
# 不能证明每种渔具/船队记录完整；观测到的部分和在 observed_sum_tonnes。
# CCSBT 只有全球“分渔具”边际表，不能据此推算船旗×渔具或太平洋 SBF。

# 6. 固定分析版本：保存 run_dir；版本锁还记录了输入文件 SHA-256。
lock_file <- file.path(run_dir, "recipe.lock.toml")
# reproduced <- tuna_build(config = lock_file)  # 同一输入版本、选择规则和期间重建
# 换机器同步/复制完整 library（包括 processed/）后可直接读取最新结果：
# global_by_gear <- tuna_processed("global_by_gear")
# 单次指定 NAS，不改变默认配置：
# tuna_processed("pacific_by_gear", path = "/Volumes/NAS/research/tuna-library")
# 如需单独保存结果：tuna_build(config = recipe, output = "/你的结果目录")
cat("完成。R 中保留 global_catch、pacific_catch、global_by_gear、pacific_by_gear、\n",
    "longline、purse_seine、pacific_yft、flag_gear、coverage、gear_crosswalk。\n", sep = "")
