# RFMO spatial catch/effort + GFW activity: build once, then read and plot.
# source("/你的代码目录/tuna_data/examples/r_spatial_workflow.R")
# 首次安装：install.packages(c("reticulate", "ggplot2", "patchwork", "ragg",
#   "svglite", "systemfonts", "jsonlite", "scales", "sf", "rnaturalearth", "rnaturalearthdata"))
# 首次无空间结果时，自动从已下载的原始文件构建 2024 年结果。
# 修改年份：复制 templates/spatial_2024.toml，调整年份后设置 TUNA_SPATIAL_CONFIG。
# Sys.setenv(TUNA_DATA_HOME = "/Volumes/NAS/research/tuna-library")
# Sys.setenv(TUNA_SPATIAL_BUILD = "1")  # 更新源文件后明确重新构建
# Sys.setenv(TUNA_SPATIAL_RUN = "/固定结果目录/Spatial_...") # 固定某次结果
# Sys.setenv(TUNA_SPATIAL_YEAR = "2024", TUNA_SPATIAL_SPECIES = "YFT")
# Sys.setenv(TUNA_SPATIAL_OUT = "/你的图片目录")

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
required <- c("reticulate", "ggplot2", "patchwork", "ragg", "svglite", "systemfonts", "jsonlite", "scales", "sf", "rnaturalearth", "rnaturalearthdata")
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

# 1. 构建或固定结果目录。完整原始文件不变；native 表保留所选年份的原始粒度。
run_dir <- path.expand(Sys.getenv("TUNA_SPATIAL_RUN", unset = ""))
parent <- file.path(db$root$as_posix(), "processed", "spatial")
if (!nzchar(run_dir)) {
  latest_file <- file.path(parent, "latest.json")
  if (!file.exists(latest_file) || identical(Sys.getenv("TUNA_SPATIAL_BUILD"), "1")) {
    recipe <- path.expand(Sys.getenv("TUNA_SPATIAL_CONFIG", unset = file.path(repo_dir, "templates", "spatial_2024.toml")))
    result <- tuna_spatial_build(config = recipe)
    run_dir <- result$run_dir
  } else {
    run_dir <- file.path(parent, jsonlite::read_json(latest_file)$run_id)
  }
}
run_dir <- normalizePath(run_dir, winslash = "/", mustWork = TRUE)
input_meta <- jsonlite::read_json(file.path(run_dir, "provenance.json"), simplifyVector = FALSE)
year <- as.integer(Sys.getenv("TUNA_SPATIAL_YEAR", unset = "2024"))
species <- Sys.getenv("TUNA_SPATIAL_SPECIES", unset = "YFT")
if (is.na(year) || !species %in% unlist(input_meta$recipe$species)) stop("年份或鱼种无效。")
out_dir <- path.expand(Sys.getenv("TUNA_SPATIAL_OUT", unset = file.path(repo_dir, "reports", "figures", "spatial")))
dir.create(out_dir, recursive = TRUE, showWarnings = FALSE)

# 所有鱼种/渔具仍保存在结果表。下述图只选择黄鳍（默认）、延绳钓和围网。
global_cells <- tuna_spatial("global_catch_cells", run = run_dir, year = year, species = species)
pacific_cells <- tuna_spatial("pacific_catch_cells", run = run_dir, year = year, species = species)
quality <- tuna_spatial("rfmo_quality", run = run_dir, year = year)
source_inventory <- tuna_spatial("source_inventory", run = run_dir)
catch <- global_cells[global_cells$year == year & global_cells$species_code == species & global_cells$unit == "tonnes", ]
if (!nrow(catch)) stop("所选年份和鱼种没有可绘制的吨数记录。")
# 保留来源、口径、单位和渔具，不把几个机构的空间表相加为一个全球估计量。
if (anyDuplicated(catch[c("source", "product", "catch_basis", "gear_group", "map_west", "map_south")])) stop("绘图键重复，请检查来源视图。")
gear_labels <- c(longline = "Longline", purse_seine = "Purse seine")
source_labels <- c(iattc = "IATTC · retained, unraised", wcpfc = "WCPFC · public reported",
                   iotc = "IOTC · georeferenced reported", iccat = "ICCAT · CATDIS raised", ccsbt = "CCSBT · spatial reported")
species_names <- c(YFT = "Yellowfin tuna", BET = "Bigeye tuna", SKJ = "Skipjack tuna", ALB = "Albacore",
                    PBF = "Pacific bluefin tuna", BFT = "Atlantic bluefin tuna", SBF = "Southern bluefin tuna")
selected <- catch[catch$gear_group %in% names(gear_labels), ]
if (!nrow(selected)) stop("该鱼种无延绳钓/围网数据，请更改绘图渔具。")

# 2. 陆地先 dissolve，再画海岸线，不显示国境线。平面操作只用于底图显示。
world <- rnaturalearth::ne_countries(scale = 110, returnclass = "sf")
land_geo <- sf::st_union(sf::st_make_valid(sf::st_set_crs(sf::st_geometry(world), NA)))
stopifnot(length(land_geo) == 1L, all(sf::st_is_valid(land_geo)))
polygon_frame <- function(geom) {
  geom <- sf::st_cast(geom, "MULTIPOLYGON", warn = FALSE)
  xy <- as.data.frame(sf::st_coordinates(geom))
  xy$group <- interaction(xy$L2, xy$L3, drop = TRUE)
  xy$ring <- interaction(xy$L1, xy$L2, xy$L3, drop = TRUE)
  xy
}
land_global <- polygon_frame(land_geo)
box <- function(x0, x1) sf::st_as_sfc(sf::st_bbox(c(xmin = x0, ymin = -90, xmax = x1, ymax = 90)))
west_land <- suppressWarnings(sf::st_intersection(land_geo, box(-180, 0))) + c(360, 0)
east_land <- suppressWarnings(sf::st_intersection(land_geo, box(0, 180)))
land_pacific <- polygon_frame(c(east_land, west_land))

fonts <- c("Arial", "Helvetica", "Liberation Sans", "DejaVu Sans")
available <- fonts[fonts %in% unique(systemfonts::system_fonts()$family)]
font <- if (length(available)) available[[1L]] else "sans"
theme_map <- theme_classic(base_size = 8, base_family = font) +
  theme(panel.grid = element_blank(), panel.border = element_rect(fill = NA, colour = "#555555", linewidth = .2),
        axis.line = element_blank(), axis.ticks = element_line(linewidth = .2),
        axis.ticks.length = grid::unit(1, "mm"), axis.text = element_text(size = 6.5, colour = "#333333"),
        plot.title = element_text(size = 8, face = "plain", margin = margin(b = 3)),
        plot.subtitle = element_text(size = 7, margin = margin(b = 3)),
        legend.title = element_text(size = 7.5), legend.text = element_text(size = 7),
        plot.margin = margin(4, 5, 4, 5))
lon_labels <- function(x) {
  x <- ifelse(x > 180, x - 360, x)
  ifelse(x == 0, "0°", paste0(abs(x), "°", ifelse(abs(x) == 180, "", ifelse(x < 0, "W", "E"))))
}
lat_labels <- function(x) ifelse(x == 0, "0°", paste0(abs(x), "°", ifelse(x < 0, "S", "N")))
positive <- selected$observed_value[selected$observed_value > 0 & !is.na(selected$observed_value)]
if (!length(positive)) stop("没有正值可用于对数色标。")
catch_limits <- 10 ^ c(floor(log10(min(positive))), ceiling(log10(max(positive))))

draw_map <- function(d, title, subtitle, xlim, ylim, pacific = FALSE, limits, legend_title, mark_partial = FALSE) {
  d$x <- if (pacific) ifelse(d$map_west < 0, d$map_west + 360, d$map_west) else d$map_west
  d$y <- d$map_south
  land <- if (pacific) land_pacific else land_global
  # 未知（灰）、已报告零（浅蓝）、正值（cividis）；无记录区域保留白色。
  missing <- d[is.na(d$observed_value), ]
  zeros <- d[!is.na(d$observed_value) & d$observed_value == 0, ]
  pos <- d[!is.na(d$observed_value) & d$observed_value > 0, ]
  p <- ggplot() +
    geom_rect(data = missing, aes(xmin = x, xmax = x + map_size, ymin = y, ymax = y + map_size), fill = "#CCCCCC") +
    geom_rect(data = zeros, aes(xmin = x, xmax = x + map_size, ymin = y, ymax = y + map_size), fill = "#DDEBF0", colour = "#ABC4CD", linewidth = .1) +
    geom_rect(data = pos, aes(xmin = x, xmax = x + map_size, ymin = y, ymax = y + map_size, fill = observed_value))
  if (mark_partial) {
    partial <- pos[pos$n_missing > 0, ]
    p <- p + geom_point(data = partial, aes(x + map_size / 2, y + map_size / 2), shape = 3, size = .8, stroke = .22, colour = "#BB4F00")
  }
  p + geom_polygon(data = land, aes(X, Y, group = group, subgroup = ring), rule = "evenodd",
                   fill = "#F2F2F2", colour = "#777777", linewidth = .12) +
    scale_fill_viridis_c(option = "E", trans = "log10", limits = limits,
                        breaks = 10 ^ seq.int(ceiling(log10(limits[1])), floor(log10(limits[2]))),
                        labels = function(x) vapply(x, format, character(1), scientific = FALSE, trim = TRUE, big.mark = ","), name = legend_title,
                        guide = guide_colourbar(barheight = grid::unit(38, "mm"), barwidth = grid::unit(3.5, "mm"))) +
    scale_x_continuous(breaks = seq(-180, 360, 30), labels = lon_labels, expand = c(0, 0)) +
    scale_y_continuous(breaks = seq(-90, 90, 30), labels = lat_labels, expand = c(0, 0)) +
    coord_fixed(ratio = 1, xlim = xlim, ylim = ylim, expand = FALSE) +
    labs(title = title, subtitle = subtitle, x = NULL, y = NULL) + theme_map
}

sources <- c("iattc", "wcpfc", "iotc", "iccat", "ccsbt")
sources <- sources[sources %in% selected$source]
extents <- list(iattc = list(c(100, 290), c(-60, 50)), wcpfc = list(c(100, 290), c(-60, 50)),
                iotc = list(c(20, 150), c(-55, 35)), iccat = list(c(-100, 40), c(-55, 70)),
                ccsbt = list(c(-180, 180), c(-65, 20)))
for (s in sources) {
  d <- selected[selected$source == s, ]
  xs <- if (s %in% c("iattc", "wcpfc")) ifelse(d$map_west < 0, d$map_west + 360, d$map_west) else d$map_west
  extents[[s]][[1L]] <- range(c(extents[[s]][[1L]], xs, xs + d$map_size))
  extents[[s]][[2L]] <- range(c(extents[[s]][[2L]], d$map_south, d$map_south + d$map_size))
}
catch_panels <- list()
for (s in sources) for (g in names(gear_labels)) {
  d <- selected[selected$source == s & selected$gear_group == g, ]
  catch_panels[[paste(s, g)]] <- draw_map(d, paste0(LETTERS[length(catch_panels) + 1L], "  ", source_labels[[s]]),
      gear_labels[[g]], extents[[s]][[1L]], extents[[s]][[2L]], pacific = s %in% c("iattc", "wcpfc"),
      limits = catch_limits, legend_title = "Observed catch (t)\nper 5° cell · log10", mark_partial = TRUE)
}
caption_rfmo <- paste0(
  "Public spatial products have different reporting coverage and statistical bases. Panels are not a uniform global catch estimate.\n",
  "Grey: no disclosed value; pale blue: reported zero; orange crosses: observed subtotal with missing components. Blank: no included record.\n",
  "Native cells coarser than 5° remain in the data and are excluded here. Pacific authority split: 150°W. Land: Natural Earth; coastlines only.")
annotation_theme <- theme(plot.title = element_text(family = font, size = 11),
                           plot.subtitle = element_text(family = font, size = 8),
                           plot.caption = element_text(family = font, size = 6.5, hjust = 0, lineheight = 1.15),
                           plot.margin = margin(8, 8, 8, 8))
p_global <- patchwork::wrap_plots(catch_panels, ncol = 2, guides = "collect") +
  patchwork::plot_annotation(title = paste0(species_names[[species]], " catch distribution · ", year),
    subtitle = "Global source coverage | Longline and purse seine | Source-specific statistical bases",
    caption = caption_rfmo, theme = annotation_theme) & theme(legend.position = "right")
pacific_panels <- catch_panels[grepl("^(iattc|wcpfc) ", names(catch_panels))]
p_pacific <- if (length(pacific_panels)) patchwork::wrap_plots(pacific_panels, ncol = 2, guides = "collect") +
  patchwork::plot_annotation(title = paste0("Pacific ", tolower(species_names[[species]]), " catch distribution · ", year),
    subtitle = "Complementary EPO and WCPO spatial partitions | Statistical bases kept separate",
    caption = caption_rfmo, theme = annotation_theme) & theme(legend.position = "right") else NULL

# 3. GFW：跨月/船旗/渔具汇总，但每个 RFMO 区域单独显示；有空间重叠，不能跨区域求和。
# GFW 没有目标鱼种标签；这些小时不是黄鳍捕捞量，也不代表全部实际捕鱼活动。
p_gfw <- NULL
gfw_plot_cells <- NULL
if ("gfw_cells" %in% names(input_meta$outputs)) {
  gfw <- tuna_spatial("gfw_cells", run = run_dir, year = year)
  gfw <- gfw[gfw$year == year, ]
  if (nrow(gfw)) {
    if (anyNA(gfw$value)) stop("GFW 小时存在缺失，不能按完整年度合计。")
    gfw_plot_cells <- aggregate(value ~ region_raw + map_west + map_south + map_size, gfw, sum)
    gfw_plot_cells$observed_value <- gfw_plot_cells$value
    gfw_plot_cells$n_missing <- 0
    before <- aggregate(value ~ region_raw, gfw, sum)
    after <- aggregate(value ~ region_raw, gfw_plot_cells, sum)
    stopifnot(isTRUE(all.equal(before, after, tolerance = 1e-10)))
    positive_hours <- gfw_plot_cells$value[gfw_plot_cells$value > 0]
    hour_limits <- 10 ^ c(floor(log10(min(positive_hours))), ceiling(log10(max(positive_hours))))
    gfw_panels <- list()
    for (rfmo in c("IATTC", "WCPFC", "IOTC", "ICCAT", "CCSBT")) {
      d <- gfw_plot_cells[gfw_plot_cells$region_raw == rfmo, ]
      if (!nrow(d)) next
      pac <- rfmo %in% c("IATTC", "WCPFC")
      xs <- if (pac) ifelse(d$map_west < 0, d$map_west + 360, d$map_west) else d$map_west
      # Shared extents within rows keep panels legible; include all reported cells.
      xr <- switch(rfmo, IATTC = c(90, 290), WCPFC = c(90, 290), IOTC = c(0, 160), ICCAT = c(-110, 50), CCSBT = c(-180, 180))
      yr <- if (rfmo == "CCSBT") c(-65, 0) else c(-65, 75)
      xr <- range(c(xr, xs, xs + d$map_size))
      yr <- range(c(yr, d$map_south, d$map_south + d$map_size))
      gfw_panels[[rfmo]] <- draw_map(d, paste0(LETTERS[length(gfw_panels) + 1L], "  ", rfmo),
        "All flags and gears", xr, yr, pacific = pac, limits = hour_limits,
        legend_title = paste0("Apparent fishing hours\nper ", unique(d$map_size), "° cell · log10"))
    }
    gfw_layout <- if (length(gfw_panels) == 5L) {
      patchwork::wrap_plots(patchwork::wrap_plots(gfw_panels[1:4], ncol = 2),
                           gfw_panels[[5L]], ncol = 1, heights = c(1, .23), guides = "collect")
    } else patchwork::wrap_plots(gfw_panels, ncol = 2, guides = "collect")
    gfw_versions <- unique(sub("^.*:", "", unlist(lapply(input_meta$sources, function(x) x$request[["datasets[0]"]]))))
    p_gfw <- gfw_layout +
      patchwork::plot_annotation(title = paste0("Apparent fishing activity in RFMO regions · ", year),
        subtitle = paste0("Global Fishing Watch ", paste(gfw_versions, collapse = ", "), " | All fishing activity, not species-specific | Separate RFMO reports"),
        caption = paste0("Regions overlap: panel totals must not be added. Source 0.1° reported coordinates binned to the display grid; no area normalization.\n",
                         "Blank areas do not imply zero fishing; AIS coverage is incomplete. Land: Natural Earth; coastlines only."),
        theme = annotation_theme) & theme(legend.position = "right")
  }
}

# 4. PNG 预览、600 dpi 输出、可编辑 SVG，以及本次绘图所用的数据表和版本记录。
save_figure <- function(plot, stem, height) {
  ggsave(file.path(out_dir, paste0(stem, "_preview.png")), plot, device = ragg::agg_png, width = 230, height = height, units = "mm", dpi = 180, bg = "white")
  ggsave(file.path(out_dir, paste0(stem, ".png")), plot, device = ragg::agg_png, width = 230, height = height, units = "mm", dpi = 600, bg = "white")
  ggsave(file.path(out_dir, paste0(stem, ".svg")), plot, device = svglite::svglite, width = 230, height = height, units = "mm", bg = "white")
}
stem <- paste0(tolower(species), "_", year)
save_figure(p_global, paste0("global_", stem, "_catch"), max(150, length(sources) * 62 + 30))
if (!is.null(p_pacific)) save_figure(p_pacific, paste0("pacific_", stem, "_catch"), 166)
if (!is.null(p_gfw)) save_figure(p_gfw, paste0("gfw_", year, "_rfmo_activity"), 242)
write.csv(catch, file.path(out_dir, paste0("global_", stem, "_catch_cells.csv")), row.names = FALSE, na = "")
write.csv(pacific_cells[pacific_cells$year == year & pacific_cells$species_code == species, ],
          file.path(out_dir, paste0("pacific_", stem, "_catch_cells.csv")), row.names = FALSE, na = "")
if (!is.null(gfw_plot_cells)) write.csv(gfw_plot_cells, file.path(out_dir, paste0("gfw_", year, "_rfmo_activity_cells.csv")), row.names = FALSE, na = "")
write.csv(quality, file.path(out_dir, "rfmo_quality.csv"), row.names = FALSE, na = "")
write.csv(source_inventory, file.path(out_dir, "source_inventory.csv"), row.names = FALSE, na = "")
jsonlite::write_json(list(run_id = input_meta$run_id, recipe = input_meta$recipe,
  sources = input_meta$sources, output_checksums = input_meta$outputs, year = year, species = species,
  catch_map = list(unit = "tonnes", grid_degrees = 5, displayed_gears = names(gear_labels),
                  value = "observed_value; orange crosses when n_missing > 0", authority_split = "150W"),
  gfw_map = list(value = "apparent_fishing_hours", rfmo_partitions_overlap = TRUE, species_specific = FALSE),
  style = list(national_borders = FALSE, land = "Dissolved Natural Earth 110m", font = font, palette = "cividis", colour_scale = "log10"),
  packages = as.list(vapply(required, function(p) as.character(packageVersion(p)), character(1L)))),
  file.path(out_dir, "spatial_plot_provenance.json"), pretty = TRUE, auto_unbox = TRUE, digits = NA)
writeLines(capture.output(sessionInfo()), file.path(out_dir, "R_session_info.txt"))
if (interactive()) { print(p_global); if (!is.null(p_pacific)) print(p_pacific); if (!is.null(p_gfw)) print(p_gfw) }
cat("空间结果：", run_dir, "\n图片与绘图数据：", normalizePath(out_dir, winslash = "/"), "\n", sep = "")
cat("可读取：tuna_spatial('rfmo_native')、tuna_spatial('rfmo_effort_cells')、tuna_spatial('gfw_cells')\n")

