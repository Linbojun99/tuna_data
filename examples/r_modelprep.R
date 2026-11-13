# CPUE 标准化 / SDM 前的数据清洗：按物种读取已标准化的 catch-effort 分层。
# 前提：已运行 examples/r_setup.R，TUNA_DATA_HOME 指向 library。
# source("/你的代码目录/tuna_data/examples/r_modelprep.R")
# Sys.setenv(TUNA_MODELPREP_BUILD = "1")   # 原始文件更新后明确重新构建
# Sys.setenv(TUNA_MODELPREP_OUT = "/你的输出目录")
# 只用公开聚合数据；不读取 logbook、观察员或个人 CPUE。

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
db <- tuna_library()

# 1. 构建（一次）：从已下载的原始快照生成 strata / effort / catch 三张标准表。
parent <- file.path(db$root$as_posix(), "processed", "modelprep")
if (!file.exists(file.path(parent, "latest.json")) || identical(Sys.getenv("TUNA_MODELPREP_BUILD"), "1")) {
  tuna_modelprep_build(config = file.path(repo_dir, "templates", "modelprep.toml"))
}
out_dir <- path.expand(Sys.getenv("TUNA_MODELPREP_OUT", unset = file.path(repo_dir, "reports", "modelprep")))
dir.create(out_dir, recursive = TRUE, showWarnings = FALSE)

# 2. 先看物种规则：种群边界、默认单位、是否建议做 CPUE。
rules <- tuna_species_rules("BET")
print(rules[c("stock_id", "gear_group", "catch_unit", "effort_unit", "recommended", "note")])

# 3. BET 延绳钓 CPUE：WCPO，尾数 / 千钩，月 x 5x5 x 船旗。保留已确认零值。
bet <- tuna_cpue_data("BET", gear = "longline", stock = "BET-WCPO", years = 1990:2024)
print(tuna_filter_log(bet))   # 每条规则前后的行数
print(table(bet$fleet, bet$positive))
head(bet[c("year", "month_start", "fleet", "lon", "lat", "effort", "catch", "cpue", "catch_status", "qc_flags")])

# 4. SKJ 围网 CPUE：漂浮物/FAD 作业，吨 / 网次。不同作业类型分别建模。
skj <- tuna_cpue_data("SKJ", gear = "purse_seine", stock = "SKJ-WCPO",
                      school_types = c("DFAD", "AFAD", "LOG"), years = 2000:2024)
print(tuna_filter_log(skj))

# 5. YFT SDM 输入：印度洋延绳钓，5x5 x 月的出现/未出现与努力量；GFW 小时作协变量。
#    IOTC 只列正渔获：没有记录的物种需用 zeros = "with_inferred" 作为推断零（qc 中标记为 inferred_zero），
#    并与严格口径（默认 "reported"）做敏感性比较。GFW 只用 IOTC 自己的报告区域，不与其他区域相加；
#    gfw_status 区分有活动 / 报告覆盖但无活动（0 小时）/ 无报告（NA）。
yft <- tuna_sdm_data("YFT", gear = "longline", stock = "YFT-IO", years = 2013:2024, zeros = "with_inferred",
                     grid = 5, period = "month", gfw = TRUE, gfw_missing = "skip")
print(table(yft$presence, yft$gfw_status))
print(tuna_filter_log(yft))
print(summary(yft[c("presence", "effort", "gfw_hours", "n_inferred_zero")]))

# 6. 示例（仅演示数据结构，不是评估用指数）：delta-lognormal 名义模型。
bet$year_f <- factor(bet$year); bet$month_f <- factor(bet$month_start); bet$fleet_f <- factor(bet$fleet)
bet$cell <- factor(paste(bet$lon, bet$lat))
enc <- glm(positive ~ year_f + month_f + fleet_f, family = binomial(), data = bet)
pos <- glm(log(cpue) ~ year_f + month_f + fleet_f + cell, data = subset(bet, positive))
yrs <- levels(bet$year_f)
grid_new <- data.frame(year_f = factor(yrs, levels = yrs), month_f = factor(names(which.max(table(bet$month_f))), levels = levels(bet$month_f)),
                       fleet_f = factor(names(which.max(table(bet$fleet_f))), levels = levels(bet$fleet_f)),
                       cell = factor(names(which.max(table(bet$cell))), levels = levels(bet$cell)))
index <- data.frame(year = as.integer(yrs),
                    index = predict(enc, grid_new, type = "response") * exp(predict(pos, grid_new)))
index$relative <- index$index / mean(index$index)
print(head(index))

# 7. 保存分析表、过滤日志和版本信息，便于论文复现。
write.csv(bet, file.path(out_dir, "bet_wcpo_longline_cpue_input.csv"), row.names = FALSE, na = "")
write.csv(tuna_filter_log(bet), file.path(out_dir, "bet_wcpo_longline_filter_log.csv"), row.names = FALSE)
write.csv(index, file.path(out_dir, "bet_wcpo_longline_nominal_delta_index.csv"), row.names = FALSE)
jsonlite::write_json(list(run = tuna_modelprep("products")[c("product", "dataset_id", "sha256")],
                          species = "BET", stock = "BET-WCPO", gear = "longline", units = "fish per 1000 hooks",
                          zero_policy = "reported"),
                     file.path(out_dir, "bet_wcpo_longline_provenance.json"), pretty = TRUE, auto_unbox = TRUE)
cat("结果目录：", normalizePath(out_dir, winslash = "/"), "\n")
