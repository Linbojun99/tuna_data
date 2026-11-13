# Python core with an R interface. Set RETICULATE_PYTHON before sourcing if needed.
# install.packages("reticulate")

tuna_library <- function(path = NULL) {
  if (!requireNamespace("reticulate", quietly = TRUE)) {
    stop("Install reticulate: install.packages('reticulate')")
  }
  td <- reticulate::import("tuna_data", convert = TRUE)
  # R's Sys.setenv() does not refresh Python's cached os.environ after Python
  # has started. Synchronize only the two storage settings, including unsets,
  # so switching Drive/NAS paths in an existing R session takes effect.
  python_env <- reticulate::import("os", convert = TRUE)$environ
  for (name in c("TUNA_DATA_HOME", "TUNA_DATA_CONFIG")) {
    value <- Sys.getenv(name, unset = NA_character_)
    if (is.na(value)) {
      python_env$pop(name, NULL)
    } else {
      python_env$update(stats::setNames(list(value), name))
    }
  }
  if (is.null(path)) td$Library() else td$Library(root = path)
}

tuna_catalog <- function(path = NULL) tuna_library(path)$catalog()

# Public stock-assessment results. The selected library can be local, Drive or NAS.
tuna_assessment_catalog <- function(path = NULL, run = NULL, ...) {
  db <- tuna_library(path)
  a <- reticulate::import("tuna_data.assessment", convert = TRUE)
  a$assessment_catalog(library = db, run = run, ...)
}

tuna_assessment <- function(table = "series", path = NULL, run = NULL,
                            include_forecast = FALSE, columns = NULL, ...) {
  db <- tuna_library(path)
  a <- reticulate::import("tuna_data.assessment", convert = TRUE)
  if (!is.null(columns)) columns <- as.list(columns)
  a$read_assessment(table, library = db, run = run, columns = columns,
                    include_forecast = include_forecast, ...)
}

tuna_assessment_native <- function(model, section, path = NULL, run = NULL) {
  db <- tuna_library(path)
  a <- reticulate::import("tuna_data.assessment", convert = TRUE)
  a$read_native(model, section, library = db, run = run)
}

tuna_assessment_file <- function(file_id, path = NULL, run = NULL) {
  db <- tuna_library(path)
  a <- reticulate::import("tuna_data.assessment", convert = TRUE)
  a$assessment_file(file_id, library = db, run = run)$as_posix()
}

tuna_assessment_validate <- function(path = NULL, run = NULL) {
  db <- tuna_library(path)
  reticulate::import("tuna_data.assessment", convert = TRUE)$validate_assessments(library = db, run = run)
}

tuna_assessment_copy <- function(destination, path = NULL, run = NULL) {
  db <- tuna_library(path)
  reticulate::import("tuna_data.assessment", convert = TRUE)$copy_assessments(destination, library = db, run = run)
}

tuna_members <- function(dataset, path = NULL) {
  tuna_library(path)$members(dataset)
}

tuna_read <- function(dataset, member = NULL, path = NULL, ...) {
  tuna_library(path)$read_table(dataset, member = member, ...)
}

tuna_update <- function(sources = c("iattc", "iccat", "iotc", "wcpfc", "ccsbt"), path = NULL) {
  result <- tuna_library(path)$update(sources = as.list(sources))
  if (length(result$errors)) stop(paste("Update incomplete:", paste(capture.output(str(result$errors)), collapse = "\n")))
  invisible(result)
}

# Build from already downloaded source snapshots. Raw files are not modified.
tuna_build <- function(config = NULL, path = NULL, output = NULL) {
  db <- tuna_library(path)
  pipeline <- reticulate::import("tuna_data.pipeline", convert = TRUE)
  pipeline$build_annual(config = config, library = db, output = output)
}

# run=NULL reads the latest successful run in the selected library.
# Set run to a fixed folder from tuna_build() for a reproducible analysis.
tuna_processed <- function(table = "global_by_gear", run = NULL, path = NULL) {
  db <- tuna_library(path)
  pipeline <- reticulate::import("tuna_data.pipeline", convert = TRUE)
  pipeline$read_processed(table, run = run, library = db)
}

# Keep catch_basis when aggregating. Unknown catch remains NA, not zero.
tuna_summarise <- function(data, by = c("year", "species_code", "gear_group", "catch_basis")) {
  pipeline <- reticulate::import("tuna_data.pipeline", convert = TRUE)
  pipeline$aggregate_catch(data, by = as.list(by))
}

# Spatial recipes preserve native records and create separate catch/effort/activity layers.
tuna_spatial_build <- function(config = NULL, path = NULL, output = NULL) {
  db <- tuna_library(path)
  pipeline <- reticulate::import("tuna_data.spatial", convert = TRUE)
  pipeline$build_spatial(config = config, library = db, output = output)
}

tuna_spatial <- function(table = "global_catch_cells", run = NULL, path = NULL,
                         year = NULL, species = NULL, gear = NULL, source = NULL,
                         rfmo = NULL, unit = NULL, measure = NULL, columns = NULL,
                         include_partial = TRUE, verify = TRUE) {
  db <- tuna_library(path)
  pipeline <- reticulate::import("tuna_data.spatial", convert = TRUE)
  values <- function(x) if (is.null(x)) NULL else as.list(x)
  pipeline$read_spatial(table, run = run, library = db,
    year = values(year), species = values(species), gear = values(gear), source = values(source),
    rfmo = values(rfmo), unit = values(unit), measure = values(measure), columns = values(columns),
    include_partial = include_partial, verify = verify)
}

# Available years/species/gears and source coverage; much smaller than native data.
tuna_spatial_catalog <- function(run = NULL, path = NULL) {
  tuna_spatial("coverage", run = run, path = path)
}

# ---- Model preparation: CPUE standardization and SDM inputs ----------------
# Public aggregated catch-effort only. Build once, then read species tables.
# Each table carries attr(x, "filter_log"): rows before/after every rule.
.tuna_modelprep <- function() reticulate::import("tuna_data.modelprep", convert = TRUE)
.tuna_list <- function(x) if (is.null(x)) NULL else as.list(x)
.tuna_with_log <- function(result) {
  data <- result[[1L]]
  attr(data, "filter_log") <- result[[2L]]
  data
}

tuna_modelprep_build <- function(config = NULL, path = NULL, output = NULL) {
  db <- tuna_library(path)
  .tuna_modelprep()$build_modelprep(config = config, library = db, output = output)
}

# Stocks, default units and cited notes for one species (or all).
tuna_species_rules <- function(species = NULL) .tuna_modelprep()$rules_table(species)

tuna_cpue_data <- function(species, gear = "longline", stock = NULL, years = NULL, months = NULL,
                           fleets = NULL, exclude_fleets = NULL, products = NULL, zeros = "reported",
                           catch_unit = NULL, effort_unit = NULL, time_resolution = NULL,
                           max_cell_degrees = 5, school_types = NULL, apply_exclusions = TRUE,
                           allow_unrecommended = FALSE, composition = FALSE, run = NULL, path = NULL) {
  db <- tuna_library(path)
  .tuna_with_log(.tuna_modelprep()$cpue_table(
    species, gear, stock = .tuna_list(stock), products = .tuna_list(products), years = .tuna_list(years),
    months = .tuna_list(months), fleets = .tuna_list(fleets), exclude_fleets = .tuna_list(exclude_fleets),
    catch_unit = catch_unit, effort_unit = effort_unit, zeros = zeros,
    time_resolution = .tuna_list(time_resolution), max_cell_degrees = max_cell_degrees,
    school_types = .tuna_list(school_types), apply_exclusions = apply_exclusions,
    allow_unrecommended = allow_unrecommended, composition = composition, run = run, library = db,
    return_log = TRUE))
}

tuna_sdm_data <- function(species, gear = NULL, stock = NULL, years = NULL, months = NULL,
                          fleets = NULL, exclude_fleets = NULL, products = NULL, zeros = "reported",
                          time_resolution = NULL, max_cell_degrees = 5, grid = NULL, period = NULL,
                          apply_exclusions = TRUE, allow_unrecommended = FALSE, gfw = FALSE,
                          gfw_missing = "raise", run = NULL, path = NULL) {
  db <- tuna_library(path)
  .tuna_with_log(.tuna_modelprep()$sdm_table(
    species, gear, stock = .tuna_list(stock), products = .tuna_list(products), years = .tuna_list(years),
    months = .tuna_list(months), fleets = .tuna_list(fleets), exclude_fleets = .tuna_list(exclude_fleets),
    zeros = zeros, time_resolution = .tuna_list(time_resolution), max_cell_degrees = max_cell_degrees,
    grid = grid, period = period, apply_exclusions = apply_exclusions,
    allow_unrecommended = allow_unrecommended, gfw = gfw, gfw_missing = gfw_missing,
    run = run, library = db, return_log = TRUE))
}

# Raw canonical tables: "strata", "effort", "catch", "products", "status_counts", "adapter_qc".
tuna_modelprep <- function(table = "strata", run = NULL, path = NULL, product = NULL, year = NULL,
                           species = NULL, columns = NULL) {
  db <- tuna_library(path)
  .tuna_modelprep()$read_modelprep(table, run = run, library = db, product = .tuna_list(product),
                                   year = .tuna_list(year), species = .tuna_list(species),
                                   columns = .tuna_list(columns))
}

tuna_filter_log <- function(x) attr(x, "filter_log")
