# 全球和太平洋黄鳍金枪鱼年度渔获：不同口径独立面板，按捕捞方式区分。
# source("/你的代码目录/tuna_data/examples/r_plot_merged_yft.R")
# 首次绘图依赖：
# install.packages(c("reticulate", "ggplot2", "patchwork", "ragg", "svglite",
#                    "systemfonts", "jsonlite", "scales"))
# 先运行 r_clean_merge.R 建立合并数据；本脚本读取结果，不重新下载或清洗。
# 可在 source() 前设置：
# Sys.setenv(TUNA_DATA_HOME = "/Volumes/NAS/research/tuna-library")
# Sys.setenv(TUNA_YFT_RUN = "/固定结果目录/Annual_catch_...")
# Sys.setenv(TUNA_YFT_OUT = "/你的图片目录")
# Sys.setenv(TUNA_YFT_START = "1950", TUNA_YFT_END = "2024")

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
required <- c("reticulate", "ggplot2", "patchwork", "ragg", "svglite", "systemfonts", "jsonlite", "scales")
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
suppressPackageStartupMessages(library(ggplot2))
db <- tuna_library()

# 1. 只解析一次 latest，固定同一运行目录，防止两个图读到不同更新版本。
run_dir <- path.expand(Sys.getenv("TUNA_YFT_RUN", unset = ""))
if (!nzchar(run_dir)) {
  parent <- file.path(db$root$as_posix(), "processed", "annual")
  latest_file <- file.path(parent, "latest.json")
  if (!file.exists(latest_file)) stop("没有合并结果；请先运行 examples/r_clean_merge.R。")
  latest <- jsonlite::read_json(latest_file, simplifyVector = TRUE)
  run_dir <- file.path(parent, latest$run_id)
}
run_dir <- normalizePath(run_dir, winslash = "/", mustWork = TRUE)
input_meta <- jsonlite::read_json(file.path(run_dir, "provenance.json"), simplifyVector = FALSE)
needed <- c("global_catch", "pacific_catch", "global_coverage", "pacific_coverage")
if (!all(needed %in% names(input_meta$outputs))) stop("所选运行必须同时含全球和太平洋结果；请用 annual_catch.toml 构建。")

raw_by_scope <- lapply(c("global", "pacific"), function(scope) {
  x <- tuna_processed(paste0(scope, "_catch"), run = run_dir)
  x <- x[x$species_code == "YFT", , drop = FALSE]
  if (!nrow(x) || anyDuplicated(x$observation_id)) stop("没有黄鳍记录，或合并表存在重复观测标识。")
  if (any(x$catch_tonnes < 0, na.rm = TRUE) || any(is.infinite(x$catch_tonnes))) stop("存在无效重量。")
  x$scope <- scope
  x
})
names(raw_by_scope) <- c("global", "pacific")
yft_records <- do.call(rbind, raw_by_scope)
row.names(yft_records) <- NULL
coverage_rows <- do.call(rbind, lapply(names(raw_by_scope), function(scope) {
  x <- tuna_processed(paste0(scope, "_coverage"), run = run_dir)
  x <- x[x$species_code == "YFT", , drop = FALSE]
  x$scope <- scope
  x
}))
coverage_years <- aggregate(source_view_present ~ scope + year, coverage_rows, all)
names(coverage_years)[3L] <- "all_source_views_present"
common_years <- Reduce(intersect, lapply(names(raw_by_scope), function(s) {
  with(coverage_years, year[scope == s & all_source_views_present])
}))
if (!length(common_years)) stop("没有两种范围的预期来源都出现的共同年份。")
integer_setting <- function(name, default) {
  value <- Sys.getenv(name, unset = as.character(default))
  if (!grepl("^[0-9]{4}$", value)) stop(name, " 必须为四位整数年份。")
  as.integer(value)
}
start_year <- integer_setting("TUNA_YFT_START", min(common_years))
end_year <- integer_setting("TUNA_YFT_END", max(common_years))
if (start_year > end_year || start_year < min(yft_records$year) || end_year > max(yft_records$year)) {
  stop("绘图期间必须在已有黄鳍数据范围内。")
}
output_dir <- path.expand(Sys.getenv("TUNA_YFT_OUT", unset = file.path(repo_dir, "reports", "figures", "merged_yft")))
dir.create(output_dir, recursive = TRUE, showWarnings = FALSE)

# 2. 为预览保留六类主要捕捞方式。其余已报告类别与未分类代码各自汇总。
# 这只是展示分组；完整的 gear_group、原始 gear_code、混合渔具仍保存在合并库。
# 原来的 unknown 与 unmapped:* 均放在 Unclassified codes，不赋予具体渔具含义。
main_gears <- c(purse_seine = "Purse seine", longline = "Longline", pole_and_line = "Pole-and-line",
                handline = "Handline", gillnet = "Gillnet", troll = "Troll")
display_group <- function(x) {
  answer <- unname(main_gears[x])
  answer[is.na(answer)] <- "Other reported gears"
  answer[x == "unknown" | startsWith(x, "unmapped:")] <- "Unclassified codes"
  answer
}
palette <- c("Purse seine" = "#0072B2", "Longline" = "#D55E00", "Pole-and-line" = "#009E73",
             "Handline" = "#CC79A7", "Gillnet" = "#E69F00", "Troll" = "#56B4E9",
             "Other reported gears" = "#666666", "Unclassified codes" = "#111111")
line_types <- stats::setNames(c(rep("solid", 6L), "dotted", "longdash"), names(palette))
gear_display_crosswalk <- unique(yft_records[c("source", "gear_code_raw", "gear_group")])
gear_display_crosswalk$display_group <- display_group(gear_display_crosswalk$gear_group)
gear_display_crosswalk <- gear_display_crosswalk[order(gear_display_crosswalk$source, gear_display_crosswalk$gear_group,
                                                     gear_display_crosswalk$gear_code_raw), ]
group_by <- c("scope", "year", "species_code", "gear_group", "catch_basis")
annual_full_gears <- tuna_summarise(yft_records, by = group_by)
display_records <- yft_records
display_records$gear_group <- display_group(display_records$gear_group)
annual_display <- tuna_summarise(display_records, by = group_by)
names(annual_display)[names(annual_display) == "gear_group"] <- "display_group"

# 校验展示合并没有漏掉任何记录、已观测重量或缺值。只在校验部分和时使用 na.rm。
observed_sum <- function(x) if (all(is.na(x))) NA_real_ else sum(x, na.rm = TRUE)
raw_check <- aggregate(catch_tonnes ~ scope + year + catch_basis, yft_records,
                       FUN = observed_sum, na.action = na.pass)
shown_check <- aggregate(observed_sum_tonnes ~ scope + year + catch_basis, annual_display,
                         FUN = observed_sum, na.action = na.pass)
check <- merge(raw_check, shown_check, by = c("scope", "year", "catch_basis"), all = TRUE)
stopifnot(isTRUE(all.equal(check$catch_tonnes, check$observed_sum_tonnes, tolerance = 1e-10)),
          sum(annual_display$n_records) == nrow(yft_records),
          sum(annual_display$n_missing) == sum(is.na(yft_records$catch_tonnes)),
          !anyDuplicated(annual_display[c("scope", "year", "catch_basis", "display_group")]))

# 3. 绘图补齐年份索引但不补零；没有记录或来源不足时留空。
# 显式建立连续片段，不能跨过缺失年份连线。完整年度数值（含 2025 等未绘年份）另存 CSV。
panel_keys <- unique(annual_display[c("scope", "catch_basis")])
plot_grid <- do.call(rbind, lapply(seq_len(nrow(panel_keys)), function(i) {
  x <- expand.grid(year = seq.int(start_year, end_year), display_group = names(palette), stringsAsFactors = FALSE)
  x$scope <- panel_keys$scope[[i]]
  x$catch_basis <- panel_keys$catch_basis[[i]]
  x
}))
plot_data <- merge(plot_grid, annual_display, by = c("scope", "catch_basis", "year", "display_group"), all.x = TRUE)
plot_data <- merge(plot_data, coverage_years, by = c("scope", "year"), all.x = TRUE)
plot_data$plot_reason <- ifelse(is.na(plot_data$all_source_views_present) | !plot_data$all_source_views_present,
                               "source_view_missing", ifelse(is.na(plot_data$n_records), "no_record",
                                ifelse(is.na(plot_data$catch_tonnes), "missing_source_cell", "plotted")))
plot_data$catch_thousand_tonnes <- ifelse(plot_data$plot_reason == "plotted", plot_data$catch_tonnes / 1000, NA_real_)
plot_data <- do.call(rbind, lapply(split(plot_data, interaction(plot_data$scope, plot_data$catch_basis,
                                                              plot_data$display_group, drop = TRUE)), function(x) {
  x <- x[order(x$year), ]
  x$segment <- cumsum(is.na(x$catch_thousand_tonnes))
  x
}))
plot_data$series_id <- interaction(plot_data$scope, plot_data$catch_basis, plot_data$display_group,
                                   plot_data$segment, drop = TRUE)
plot_data$display_group <- factor(plot_data$display_group, levels = names(palette))
valid_points <- plot_data[is.finite(plot_data$catch_thousand_tonnes), , drop = FALSE]
if (!nrow(valid_points)) stop("所选期间没有可绘记录。")
valid_points$segment_n <- ave(valid_points$year, valid_points$series_id, FUN = length)
line_data <- valid_points[valid_points$segment_n > 1L, , drop = FALSE]
singletons <- valid_points[valid_points$segment_n == 1L, , drop = FALSE]

# 4. 英文标注、白底、细线、色觉友好配色；不平滑、不堆叠、不跨口径求和。
font_candidates <- c("Arial", "Helvetica", "Liberation Sans", "DejaVu Sans")
available_fonts <- unique(systemfonts::system_fonts()$family)
font_family <- font_candidates[font_candidates %in% available_fonts]
font_family <- if (length(font_family)) font_family[[1L]] else "sans"
plot_theme <- theme_classic(base_size = 8, base_family = font_family) +
  theme(text = element_text(colour = "#202020"),
        plot.title = element_text(size = 8.8, face = "bold", margin = margin(b = 3)),
        plot.subtitle = element_text(size = 7.2, margin = margin(b = 6)),
        axis.title = element_text(size = 7.7), axis.text = element_text(size = 7, colour = "#333333"),
        axis.line = element_line(linewidth = .25), axis.ticks = element_line(linewidth = .25),
        axis.ticks.length = grid::unit(1, "mm"), panel.grid = element_blank(),
        legend.title = element_text(size = 8), legend.text = element_text(size = 7.5),
        legend.key.width = grid::unit(7, "mm"), legend.key.height = grid::unit(4.5, "mm"),
        legend.margin = margin(0, 0, 0, 0), plot.margin = margin(5, 7, 5, 5))
basis_titles <- c(retained = "Retained catch", wcpfc_yearbook_reported = "WCPFC yearbook catch",
                  landings = "Landings", iccat_LF = "ICCAT code LF", dead_discards = "Reported dead discards")
basis_order <- names(basis_titles)
basis_subtitle <- function(scope, basis) {
  if (basis == "retained") {
    if (scope == "global") return("EPO + Indian Ocean | IATTC + IOTC")
    return("Eastern Pacific Ocean | IATTC")
  }
  if (basis == "wcpfc_yearbook_reported") return("Western and Central Pacific | WCPFC")
  if (basis == "iccat_LF") return("Atlantic Ocean | Definition unharmonized")
  if (basis == "dead_discards") return("Atlantic Ocean | ICCAT (DD)")
  if (basis == "landings") return("Atlantic Ocean | ICCAT (L)")
  paste("Source basis:", basis)
}
make_panel <- function(scope, basis, tag, legend_columns = 1L) {
  d <- line_data[line_data$scope == scope & line_data$catch_basis == basis, , drop = FALSE]
  points <- singletons[singletons$scope == scope & singletons$catch_basis == basis, , drop = FALSE]
  title <- unname(basis_titles[basis])
  if (is.na(title)) title <- basis
  breaks <- scales::breaks_pretty(n = 4)(c(start_year, end_year))
  breaks <- breaks[breaks >= start_year & breaks <= end_year]
  ggplot(d, aes(year, catch_thousand_tonnes, colour = display_group,
                linetype = display_group, group = series_id)) +
    # Explicit legend keys keep guides identical even when a panel lacks a gear.
    geom_line(linewidth = .38, na.rm = TRUE, show.legend = TRUE) +
    geom_point(data = points, size = .8, show.legend = FALSE, na.rm = TRUE) +
    scale_colour_manual(values = palette, limits = names(palette), drop = FALSE, name = "Fishing method") +
    scale_linetype_manual(values = line_types, limits = names(palette), drop = FALSE, name = "Fishing method") +
    scale_x_continuous(limits = c(start_year, end_year), breaks = breaks, expand = expansion(mult = c(.01, .02))) +
    scale_y_continuous(limits = c(0, NA), labels = scales::label_number(accuracy = NULL, big.mark = ","),
                       expand = expansion(mult = c(0, .08)), n.breaks = 4) +
    guides(colour = guide_legend(ncol = legend_columns, override.aes = list(linewidth = .65)),
           linetype = guide_legend(ncol = legend_columns)) +
    labs(title = paste0(tag, "  ", title), subtitle = basis_subtitle(scope, basis),
         x = "Year", y = "Reported catch (10³ t)") + plot_theme
}
annotation_theme <- theme(
  text = element_text(family = font_family, colour = "#202020"),
  plot.title = element_text(size = 11, face = "plain", margin = margin(b = 4)),
  plot.subtitle = element_text(size = 8, margin = margin(b = 8)),
  plot.caption = element_text(size = 6.8, hjust = 0, lineheight = 1.15, margin = margin(t = 7)),
  plot.margin = margin(9, 9, 9, 9))
period_label <- paste0(start_year, "–", end_year, " | YFT | Separate catch bases; independent y scales")
global_bases <- unique(annual_display$catch_basis[annual_display$scope == "global"])
global_bases <- c(intersect(basis_order, global_bases), setdiff(global_bases, basis_order))
global_panels <- lapply(seq_along(global_bases), function(i) make_panel("global", global_bases[[i]], letters[[i]]))
p_global_yft <- patchwork::wrap_plots(c(global_panels, list(patchwork::guide_area())), ncol = 2, guides = "collect") +
  patchwork::plot_annotation(title = "Global compilation of yellowfin tuna catch", subtitle = period_label,
    caption = paste0("Panels retain their source definitions and must not be added as a harmonized global total.\n",
                     "Other reported gears are pooled; unclassified codes remain separate. LF is kept as a source code.\n",
                     "Missing records are not zero. Source-view presence does not establish complete fishery coverage."),
    theme = annotation_theme)

pacific_bases <- unique(annual_display$catch_basis[annual_display$scope == "pacific"])
pacific_bases <- c(intersect(basis_order, pacific_bases), setdiff(pacific_bases, basis_order))
pacific_panels <- lapply(seq_along(pacific_bases), function(i) make_panel("pacific", pacific_bases[[i]], letters[[i]], 4L))
p_pacific_yft <- patchwork::wrap_plots(pacific_panels, ncol = 1, guides = "collect") +
  patchwork::plot_annotation(title = "Pacific yellowfin tuna catch", subtitle = period_label,
    caption = paste0("The panels cover complementary regions with different catch definitions; they are not combined.\n",
                     "The WCPFC series also appears in the global compilation. Other reported gears are pooled.\n",
                     "Missing records are not zero; unclassified codes remain separate."), theme = annotation_theme)
p_pacific_yft <- p_pacific_yft & theme(legend.position = "bottom")

# 5. 保存高清 PNG、可编辑 SVG，以及完整分渔具表、绘图分组表和版本依据。
global_height <- 205 + max(0, ceiling((length(global_bases) + 1) / 2) - 3) * 53
pacific_height <- 155 + max(0, length(pacific_bases) - 2) * 48
save_figure <- function(plot, stem, height) {
  ggsave(file.path(output_dir, paste0(stem, ".png")), plot, device = ragg::agg_png,
         width = 183, height = height, units = "mm", dpi = 600, bg = "white")
  ggsave(file.path(output_dir, paste0(stem, "_preview.png")), plot, device = ragg::agg_png,
         width = 183, height = height, units = "mm", dpi = 180, bg = "white")
  ggsave(file.path(output_dir, paste0(stem, ".svg")), plot, device = svglite::svglite,
         width = 183, height = height, units = "mm", bg = "white")
}
save_figure(p_global_yft, "global_yft_by_gear_and_basis", global_height)
save_figure(p_pacific_yft, "pacific_yft_by_gear_and_basis", pacific_height)
write.csv(annual_full_gears, file.path(output_dir, "yft_annual_all_gears.csv"), row.names = FALSE, na = "")
write.csv(annual_display, file.path(output_dir, "yft_annual_display_groups.csv"), row.names = FALSE, na = "")
write.csv(plot_data, file.path(output_dir, "yft_plot_data.csv"), row.names = FALSE, na = "")
write.csv(coverage_years, file.path(output_dir, "yft_source_coverage.csv"), row.names = FALSE, na = "")
write.csv(gear_display_crosswalk, file.path(output_dir, "gear_display_crosswalk.csv"), row.names = FALSE, na = "")
metadata <- list(
  created_at = format(Sys.time(), tz = "UTC", usetz = TRUE), input_run_id = input_meta$run_id,
  script_sha256 = reticulate::import("tuna_data.storage", convert = TRUE)$checksum(script_file),
  input_tables = input_meta$outputs[needed], source_snapshots = input_meta$sources,
  pipeline_adapter_sha256 = input_meta$adapter_sha256,
  plotted_years = c(start_year, end_year), available_years = range(yft_records$year),
  input_yft_rows = lapply(raw_by_scope, nrow),
  excluded_years = coverage_years[coverage_years$year < start_year | coverage_years$year > end_year, ],
  method = list(grouping = c("scope", "year", "display_group", "catch_basis"),
    weight = "Whole/live-weight equivalent tonnes, displayed as thousand tonnes",
    display_groups = as.list(main_gears), other_gears = "All remaining reported groups, including mixed gears",
    unclassified = "unknown and unmapped:*; not assigned to a specific fishing method",
    missing = "No zero filling; incomplete source years and missing cells blank; lines break at gaps",
    scales = "Linear, independent y axes; no smoothing, stacking or cross-basis sums",
    note = "Global panels are geographic/basis subsets, not separate estimates of the entire global total"),
  validation = list(observed_weight_conserved_by_scope_year_basis = TRUE,
    record_count_conserved = TRUE, missing_count_conserved = TRUE, no_duplicate_observation_within_scope = TRUE),
  figure_style = list(font = font_family, width_mm = 183, png_dpi = 600, preview_dpi = 180, palette = as.list(palette)),
  packages = as.list(vapply(required, function(p) as.character(packageVersion(p)), character(1L)))
)
jsonlite::write_json(metadata, file.path(output_dir, "yft_plot_provenance.json"), pretty = TRUE, auto_unbox = TRUE, digits = NA)
writeLines(capture.output(sessionInfo()), file.path(output_dir, "R_session_info.txt"))
if (interactive()) { print(p_global_yft); print(p_pacific_yft) }
cat("绘图期间：", start_year, "–", end_year, "；原始合并记录年份仍完整保留至 ", max(yft_records$year), "。\n", sep = "")
cat("全球黄鳍明细：", nrow(raw_by_scope$global), "；太平洋黄鳍明细：", nrow(raw_by_scope$pacific), "。\n", sep = "")
cat("图片、完整分渔具表、展示分组和版本信息：", normalizePath(output_dir, winslash = "/"), "\n", sep = "")
