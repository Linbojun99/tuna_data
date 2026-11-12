# 两张真实数据图：IATTC 黄鳍金枪鱼年度保留渔获量 + GFW 年度空间分布。
# source("/你的代码目录/tuna_data/examples/r_plot_catch_and_gfw.R")
# 只读取完整的本地/Drive/NAS 数据库，不请求 GFW API，不需要 token。
# 首次需安装 R 包：
# install.packages(c("reticulate", "ggplot2", "sf", "rnaturalearth",
#                    "rnaturalearthdata", "ragg", "systemfonts", "jsonlite"))

# 可选参数（在 source() 前设置）；默认延续读取示例中的 IOTC 2024 年。
# Sys.setenv(TUNA_PLOT_RFMO = "IOTC", TUNA_PLOT_YEAR = "2024")
# Sys.setenv(TUNA_PLOT_GRID = "1")       # 以度为单位的展示网格
# Sys.setenv(TUNA_PLOT_OUT = "/你的图片输出目录")
# 数据路径仍使用 TUNA_DATA_HOME 或 ~/.config/tuna-data/config.toml。

source_files <- Filter(function(x) is.character(x) && length(x) == 1L && !is.na(x),
                       lapply(sys.frames(), function(frame) frame$ofile))
script_file <- if (length(source_files)) tail(source_files, 1L)[[1L]] else NULL
if (is.null(script_file)) {
  file_arg <- grep("^--file=", commandArgs(), value = TRUE)
  if (length(file_arg)) script_file <- sub("^--file=", "", file_arg[[1L]])
}
if (is.null(script_file) || !file.exists(script_file)) stop("请用 source(完整路径) 或 Rscript 运行本脚本。")
repo_dir <- normalizePath(file.path(dirname(script_file), ".."), winslash = "/")
source(file.path(repo_dir, "R", "tuna_data.R"), local = TRUE)
required <- c("reticulate", "ggplot2", "sf", "rnaturalearth", "rnaturalearthdata", "ragg", "systemfonts", "jsonlite")
missing <- required[!vapply(required, requireNamespace, logical(1L), quietly = TRUE)]
if (length(missing)) stop("请先安装缺少的 R 包：install.packages(c(", paste(sprintf('"%s"', missing), collapse = ", "), "))")

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
    python_key(reticulate::py_config()$python) != python_key(python_bin)) stop("请重启 R，再运行本脚本以选择正确的 Python。")
Sys.setenv(RETICULATE_PYTHON = python_bin)
reticulate::use_python(python_bin, required = TRUE)
db <- tuna_library()
cleaner <- reticulate::import("tuna_data.cleaning", convert = TRUE)
suppressPackageStartupMessages(library(ggplot2))

gfw_rfmo <- toupper(Sys.getenv("TUNA_PLOT_RFMO", unset = "IOTC"))
gfw_year <- suppressWarnings(as.integer(Sys.getenv("TUNA_PLOT_YEAR", unset = "2024")))
grid_deg <- suppressWarnings(as.numeric(Sys.getenv("TUNA_PLOT_GRID", unset = "1")))
if (!gfw_rfmo %in% c("IATTC", "ICCAT", "IOTC", "WCPFC", "CCSBT")) stop("RFMO 名称无效。")
if (is.na(gfw_year) || gfw_year < 2012L) stop("GFW 年份应为 2012 年或以后。")
if (!is.finite(grid_deg) || !grid_deg %in% c(0.5, 1, 2, 5)) stop("展示网格可设为 0.5、1、2 或 5 度。原始报告为 0.1 度。")
output_dir <- path.expand(Sys.getenv("TUNA_PLOT_OUT", unset = file.path(repo_dir, "reports", "figures")))
dir.create(output_dir, recursive = TRUE, showWarnings = FALSE)

# 中文字体；无中文字体的机器自动使用英文图注，避免方框乱码。
font_candidates <- c("PingFang SC", "Microsoft YaHei", "Noto Sans CJK SC", "WenQuanYi Zen Hei", "Arial Unicode MS")
available_fonts <- unique(systemfonts::system_fonts()$family)
matched_fonts <- font_candidates[font_candidates %in% available_fonts]
chinese <- length(matched_fonts) > 0L
font_family <- if (chinese) matched_fonts[[1L]] else "sans"
txt <- function(zh, en) if (chinese) zh else en
fmt <- function(x) formatC(x, format = "f", digits = 0L, big.mark = ",")
base_theme <- theme_minimal(base_size = 13, base_family = font_family) +
  theme(plot.title = element_text(size = 18, face = "bold", colour = "#20343D"),
        plot.subtitle = element_text(size = 11, colour = "#52626B", margin = margin(b = 14)),
        plot.caption = element_text(size = 9.5, colour = "#52626B", hjust = 0, margin = margin(t = 12)),
        panel.grid.minor = element_blank(), panel.grid.major = element_line(colour = "#E2E8EC", linewidth = .3),
        axis.title = element_text(colour = "#34454F"), plot.margin = margin(15, 20, 14, 14))

# 一、完整读取 IATTC 表，按年份求和；不合并其他 RFMO、不填补缺失值。
catch_id <- "iattc/catchbyflaggear"
catch_record <- db$record(catch_id)
catch_raw <- tuna_read(catch_id, version = catch_record$sha256)
catch <- cleaner$numeric_columns(catch_raw, list("AnoYear", "t"))
yft <- catch[catch$EspeciesSpecies == "YFT", , drop = FALSE]
if (!nrow(yft) || anyNA(yft[c("AnoYear", "t")]) || any(!is.finite(yft$t)) || any(yft$t < 0)) stop("YFT 年份/渔获量存在缺失或无效值，请核对原表。")
if (anyDuplicated(yft[c("AnoYear", "BanderaFlag", "ArteGear", "EspeciesSpecies")])) stop("IATTC 表出现重复统计键，请先检查数据口径。")
annual_yft <- aggregate(t ~ AnoYear, data = yft, FUN = sum)
names(annual_yft) <- c("year", "catch_tonnes")
annual_yft <- merge(data.frame(year = seq.int(min(annual_yft$year), max(annual_yft$year))), annual_yft, by = "year", all.x = TRUE)
stopifnot(isTRUE(all.equal(sum(annual_yft$catch_tonnes, na.rm = TRUE), sum(yft$t), tolerance = 1e-10)))
latest <- tail(annual_yft[!is.na(annual_yft$catch_tonnes), ], 1L)
latest$label <- paste0(latest$year, txt(" 年：", ": "), fmt(latest$catch_tonnes), txt(" 吨", " t"))
p_yft <- ggplot(annual_yft, aes(year, catch_tonnes)) +
  geom_area(fill = "#287782", alpha = .09) +
  geom_line(colour = "#216F7A", linewidth = .85) +
  geom_point(data = latest, colour = "#B24D32", size = 2.8) +
  geom_text(data = latest, aes(label = label), hjust = 1, vjust = -1.1, colour = "#993E29", size = 4.1, family = font_family) +
  scale_x_continuous(breaks = seq(1920, max(annual_yft$year), by = 20), expand = expansion(mult = c(.01, .02))) +
  scale_y_continuous(labels = scales::label_number(big.mark = ",", accuracy = 1), limits = c(0, NA), expand = expansion(mult = c(0, .15))) +
  labs(title = txt("东太平洋黄鳍金枪鱼：年度保留渔获量", "Eastern Pacific yellowfin tuna: annual retained catch"),
       subtitle = paste0("IATTC  |  ", min(annual_yft$year), "–", max(annual_yft$year), txt("  |  各船旗、渔具合计", "  |  All flags and gears combined")),
       x = txt("年份", "Year"), y = txt("保留渔获量（吨）", "Retained catch (metric tonnes)"),
       caption = txt("来源：IATTC CatchByFlagGear；原表中的 0 表示低于 0.5 吨。\n按源文件逐年求和；未平滑，未对历史统计口径变化作校正。",
                     "Source: IATTC CatchByFlagGear. Source zeros mean less than 0.5 t.\nAnnual sums as reported; no smoothing or adjustment for historical reporting changes.")) + base_theme

# 二、选择无额外过滤、包含完整 12 个月的 GFW 月度 0.1° 报告。
catalogue <- tuna_catalog()
prefix <- paste0("gfw/", tolower(gfw_rfmo), "_", gfw_year, "-")
candidates <- catalogue[startsWith(catalogue$id, prefix), , drop = FALSE]
date_range <- paste0(gfw_year, "-01-01,", gfw_year + 1L, "-01-01")
matches <- vapply(candidates$id, function(id) {
  p <- db$record(id)$provenance$request
  identical(p[["region-id"]], gfw_rfmo) && identical(p[["date-range"]], date_range) &&
    identical(p[["temporal-resolution"]], "MONTHLY") && identical(p[["spatial-resolution"]], "LOW") &&
    identical(p[["spatial-aggregation"]], "false") && identical(p[["group-by"]], "FLAGANDGEARTYPE") && is.null(p[["filters[0]"]])
}, logical(1L))
candidates <- candidates[matches, , drop = FALSE]
if (!nrow(candidates)) stop("当前库没有该区域/年份的完整年度报告；请先补齐数据，或改用已完整下载的年份。")
candidates <- candidates[order(candidates$retrieved_at, decreasing = TRUE), , drop = FALSE]
gfw_id <- candidates$id[[1L]]
gfw_record <- db$record(gfw_id)
members <- unlist(db$members(gfw_id, version = gfw_record$sha256), use.names = FALSE)
csv_member <- grep("[.]csv$", members, value = TRUE, ignore.case = TRUE)
if (length(csv_member) != 1L) stop("报告中存在多张 CSV，请明确选择数据表。")
message("读取完整 GFW 报告：", gfw_id)
gfw_raw <- tuna_read(gfw_id, version = gfw_record$sha256, member = csv_member[[1L]]) # 不设 nrows
gfw <- cleaner$gfw_hours(gfw_raw)
if (anyNA(gfw[c("lat", "lon", "date", "fishing_hours")]) || any(!is.finite(gfw$fishing_hours))) stop("GFW 存在缺失/无效时数或坐标。")
expected_months <- sprintf("%d-%02d", gfw_year, 1:12)
if (!setequal(unique(gfw$date), expected_months)) stop("报告未包含该年全部 12 个月，请检查完整性。")

# 原始 0.1° 坐标按报告给出的中心点分入更粗的展示网格，跨月/船旗/渔具求和。
# 保留所有原始报告行。数值转换后重合的键（如 0 与 -0）不作为删行依据。
# 不把 vessel_ids 跨月份相加当成唯一船舶数；不把没有记录的网格填为零。
gfw$grid_lon <- floor(gfw$lon / grid_deg) * grid_deg + grid_deg / 2
gfw$grid_lat <- floor(gfw$lat / grid_deg) * grid_deg + grid_deg / 2
gfw_grid <- aggregate(fishing_hours ~ grid_lon + grid_lat, data = gfw, FUN = sum)
stopifnot(isTRUE(all.equal(sum(gfw_grid$fishing_hours), sum(gfw$fishing_hours), tolerance = 1e-10)))
positive_grid <- gfw_grid[gfw_grid$fishing_hours > 0, , drop = FALSE] # 对数色标只绘制正值
if (!nrow(positive_grid)) stop("没有可绘制的正活动时数。")

# 使用响应 ZIP 中的真实 RFMO 几何；Natural Earth 只作陆地底图。
geo_member <- "layer-reference-geometry/geometry.geojson"
if (!geo_member %in% members) stop("报告缺少区域几何文件。")
gfw_path <- db$path(gfw_id, version = gfw_record$sha256)$as_posix()
geo_dir <- tempfile("tuna-gfw-geometry-"); dir.create(geo_dir)
utils::unzip(gfw_path, files = geo_member, exdir = geo_dir)
rfmo_boundary <- sf::st_read(file.path(geo_dir, geo_member), quiet = TRUE)
unlink(geo_dir, recursive = TRUE)
if (is.na(sf::st_crs(rfmo_boundary))) stop("区域几何缺少坐标系。")
rfmo_boundary <- sf::st_transform(rfmo_boundary, 4326)
world <- rnaturalearth::ne_countries(scale = 110, returnclass = "sf")
bbox <- sf::st_bbox(rfmo_boundary)
x_limits <- c(max(-180, min(bbox[["xmin"]], min(gfw_grid$grid_lon) - grid_deg / 2) - 3),
              min(180, max(bbox[["xmax"]], max(gfw_grid$grid_lon) + grid_deg / 2) + 3))
y_limits <- c(max(-85, min(bbox[["ymin"]], min(gfw_grid$grid_lat) - grid_deg / 2) - 3),
              min(85, max(bbox[["ymax"]], max(gfw_grid$grid_lat) + grid_deg / 2) + 3))
gfw_version <- gfw_record$provenance$request[["datasets[0]"]]
p_gfw <- ggplot() +
  geom_tile(data = positive_grid, aes(grid_lon, grid_lat, fill = fishing_hours), width = grid_deg, height = grid_deg) +
  geom_sf(data = world, fill = "#E3E6E7", colour = "#AAB4B9", linewidth = .2) +
  geom_sf(data = rfmo_boundary, fill = NA, colour = "#3F5867", linewidth = .4, linetype = "dashed") +
  scale_fill_viridis_c(option = "C", trans = "log10", breaks = c(.1, 1, 10, 100, 1000, 10000, 100000),
                       labels = scales::label_number(big.mark = ",", accuracy = .1),
                       name = txt(paste0("捕鱼活动时数\n小时 / ", grid_deg, "° 网格"), paste0("Fishing hours\nper ", grid_deg, "° cell"))) +
  coord_sf(crs = sf::st_crs(4326), default_crs = sf::st_crs(4326), xlim = x_limits, ylim = y_limits, expand = FALSE) +
  labs(title = txt(paste0("GFW 捕鱼活动空间分布：", gfw_rfmo, "，", gfw_year), paste0("GFW apparent fishing activity: ", gfw_rfmo, ", ", gfw_year)),
       subtitle = txt(paste0("完整年度报告  |  ", grid_deg, "° 网格求和  |  所有船旗与渔具  |  对数色标"),
                      paste0("Complete annual report  |  ", grid_deg, "° sums  |  All flags and gears  |  Log colour scale")),
       x = txt("经度", "Longitude"), y = txt("纬度", "Latitude"),
       caption = txt(paste0("来源：GFW ", sub(".*:", "", gfw_version), "；虚线为报告附带的 RFMO 边界；陆地：Natural Earth。\n原始 0.1° 网格按中心点归并；未按面积标准化。空白不表示无捕鱼，AIS 覆盖并不完整。"),
                     paste0("Source: GFW ", sub(".*:", "", gfw_version), "; dashed line: RFMO boundary from report; land: Natural Earth.\nNative 0.1° cells grouped by reported centres; not area-normalized. Blank is not zero fishing; AIS coverage is incomplete."))) +
  base_theme + theme(panel.background = element_rect(fill = "#F5F7FA", colour = NA),
                     panel.grid.major = element_line(colour = "#D6DDE2", linewidth = .25),
                     legend.title = element_text(size = 11), legend.text = element_text(size = 10),
                     legend.key.height = grid::unit(1.3, "cm"))

# 图片和派生表默认放在被 Git 忽略的 reports/figures/；原始文件保持不变。
catch_png <- file.path(output_dir, "iattc_yft_annual_catch.png")
gfw_stem <- paste0("gfw_", tolower(gfw_rfmo), "_", gfw_year, "_activity_", grid_deg, "deg")
gfw_png <- file.path(output_dir, paste0(gfw_stem, ".png"))
ggsave(catch_png, p_yft, device = ragg::agg_png, width = 10.5, height = 5.8, dpi = 300, bg = "white")
ggsave(gfw_png, p_gfw, device = ragg::agg_png, width = 11.2, height = 8.2, dpi = 300, bg = "white")
write.csv(annual_yft, file.path(output_dir, "iattc_yft_annual_catch.csv"), row.names = FALSE, na = "")
write.csv(gfw_grid, file.path(output_dir, paste0(gfw_stem, ".csv")), row.names = FALSE, na = "")
metadata <- list(
  created_at = format(Sys.time(), tz = "UTC", usetz = TRUE),
  iattc = list(id = catch_id, sha256 = catch_record$sha256, source_url = catch_record$source_url,
               input_yft_rows = nrow(yft), year_range = range(annual_yft$year), latest = latest[c("year", "catch_tonnes")],
               method = "Sum retained-catch tonnes over flags and gears within year; source zeros mean <0.5 t"),
  gfw = list(id = gfw_id, sha256 = gfw_record$sha256, request = gfw_record$provenance$request,
             input_rows = nrow(gfw), months = sort(unique(gfw$date)), annual_hours = sum(gfw$fishing_hours),
             displayed_grid_degrees = grid_deg, grid_cells = nrow(gfw_grid), positive_grid_cells = nrow(positive_grid),
             repeated_numeric_keys_retained = sum(duplicated(gfw[c("lat", "lon", "date", "flag", "geartype")])),
             method = "Keep all report rows; floor reported cell centres to degree bins and sum hours; no area normalization; no zero filling"),
  validation = list(iattc_sum_conserved = TRUE, gfw_sum_conserved = TRUE, all_12_months_present = TRUE),
  packages = as.list(vapply(required, function(p) as.character(packageVersion(p)), character(1L)))
)
jsonlite::write_json(metadata, file.path(output_dir, "plot_provenance.json"), pretty = TRUE, auto_unbox = TRUE, digits = NA)
writeLines(capture.output(sessionInfo()), file.path(output_dir, "R_session_info.txt"))
if (interactive()) { print(p_yft); print(p_gfw) }
cat("IATTC：", nrow(annual_yft), " 年；", latest$year, " 年 ", fmt(latest$catch_tonnes), " 吨。\n", sep = "")
cat("GFW：", fmt(nrow(gfw)), " 行；", fmt(nrow(gfw_grid)), " 个展示网格；全年 ", format(sum(gfw$fishing_hours), digits = 12), " 小时。\n", sep = "")
cat("图片与汇总表已保存至：", normalizePath(output_dir, winslash = "/"), "\n", sep = "")
