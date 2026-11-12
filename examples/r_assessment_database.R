# 公开资源评估数据库：查看、筛选、原生结果、参数、路径切换。
# source("/你的代码目录/tuna_data/examples/r_assessment_database.R")
# 首次安装：source("/你的代码目录/tuna_data/examples/r_setup.R")
# 可在运行前设置 TUNA_DATA_HOME 为本地、Drive 同步目录或挂载的 NAS。

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


# 数据目录必须包含 processed/assessments/latest.json。
# Sys.setenv(TUNA_DATA_HOME = "/你的Drive同步目录/tuna-data-library/library")
# Sys.setenv(TUNA_DATA_HOME = "/Volumes/NAS/research/tuna-library")
# Windows: Sys.setenv(TUNA_DATA_HOME = "Z:/research/tuna-library")
data_dir <- tuna_library()$root$as_posix()
cat("Assessment library: ", data_dir, "\n", sep = "")

# 1. 先查看覆盖范围，再选定评估年份和模型。
coverage <- tuna_assessment("coverage")
print(coverage[c("stock_id", "model_count", "report_model_count", "output_model_count",
                 "assessment_year_min", "assessment_year_max", "year_min", "year_max")])
models <- tuna_assessment_catalog(species = "BET", stock = "EPO-BET", assessment_year = 2024L)
print(models[c("model_id", "model_name", "model_role", "year_min", "year_max")])
chosen <- subset(models, model_name == "Fix-1-1")
if (nrow(chosen) != 1L) stop("未找到唯一的 Fix-1-1 模型；请从 models 中明确选择一个 model_id。")
model_id <- chosen$model_id[[1L]]

# 2. 只读取一个来源段落，避免同时计算 TIME_SERIES 和 DERIVED_QUANTITIES。
# 此模型的 native Yr 为季度编号，接口已经转换 year 和 season。
ssb <- tuna_assessment("series", model = model_id, metric = "spawning_quantity",
                       section = "DERIVED_QUANTITIES:SSB", year = as.list(1975:2023))
print(head(ssb[c("year", "season", "value", "std_error", "unit", "era")]))
stopifnot(nrow(ssb) > 0, all(ssb$unit == "tonnes"), all(ssb$era == "historical"))
# Q1 是明确的年度取值；不将四个季度的生物量相加。
q1 <- ssb[ssb$season == 1L & !is.na(ssb$value), ]
q1 <- q1[order(q1$year), ]
stopifnot(!anyDuplicated(q1$year))

# 3. 参考点、估计参数和原生年龄/选择性矩阵。
references <- tuna_assessment("reference_points", model = model_id)
parameters <- tuna_assessment("parameters", model = model_id)
print(head(references[c("metric", "value", "std_error", "unit")], 10L))
print(head(parameters[c("metric", "value", "std_error")], 10L))
sections <- tuna_assessment("sections", model = model_id)
print(sections[c("section_id", "section_name", "n_rows", "n_columns")])
selected_section <- sections[sections$section_name == "DERIVED_QUANTITIES", ][1L, ]
native <- tuna_assessment_native(model_id, selected_section$section_id)
print(head(native))

# 公开参数/代码资产只读调用；不会自动执行提供方脚本或运行模型。
assets <- tuna_assessment("files", model = model_id)
print(assets[c("file_id", "filename", "asset_role")])
if (nrow(assets)) cat("Parameter file: ", tuna_assessment_file(assets$file_id[[1L]]), "\n", sep = "")

# 4. 另一种来源：报告数值表。评估年份和数据年份分开筛选。
npalb <- tuna_assessment("series", stock = "PO-ALB-N", assessment_year = 2026L,
                         metric = "female_spawning_biomass_Q2", year = as.list(2010:2024))
print(npalb[c("year", "metric", "value", "unit", "source_section")])

# 5. 生成私有预览。图例、坐标用英文；白底、细线，不画网格。
out <- Sys.getenv("TUNA_ASSESSMENT_PREVIEW", file.path(repo_dir, "local", "assessment-preview"))
dir.create(out, recursive = TRUE, showWarnings = FALSE)
draw <- function() {
  par(mar = c(3.7, 5.9, 3.2, 0.8), mgp = c(2.2, 0.6, 0), tcl = -0.25,
      family = "sans", las = 1, bty = "l", cex = 0.9)
  plot(q1$year, q1$value, type = "l", lwd = 1.5, col = "#0072B2",
       xlab = "Year", ylab = "")
  mtext("Q1 female spawning biomass (t)", side = 2, line = 4.6, las = 0, cex = 0.9)
  mtext("Eastern Pacific bigeye tuna", side = 3, line = 1.8, cex = 1)
  mtext("2024 assessment | Fix-1-1 | historical estimates", side = 3, line = 0.5, cex = 0.65)
}
png(file.path(out, "EPO_BET_2024_Fix-1-1_SSB_Q1.png"), width = 1800, height = 1100, res = 300)
draw(); invisible(dev.off())
pdf(file.path(out, "EPO_BET_2024_Fix-1-1_SSB_Q1.pdf"), width = 6, height = 3.7)
draw(); invisible(dev.off())
write.csv(q1, file.path(out, "EPO_BET_2024_Fix-1-1_SSB_Q1.csv"), row.names = FALSE)
cat("Preview: ", normalizePath(out, winslash = "/"), "\n", sep = "")

# 6. 换机器/NAS：先复制完整评估快照，再改路径。
# 将 destination 改为你的实际目录后，取消下面两行的注释：
# tuna_assessment_copy(destination = "/Volumes/NAS/research/tuna-library")
# Sys.setenv(TUNA_DATA_HOME = "/Volumes/NAS/research/tuna-library")
# tuna_assessment_validate()
# tuna_assessment_catalog(species = "BET")
# 固定版本分析：给各读取函数传 run = 某个 Assessment_public_... 的完整目录。
