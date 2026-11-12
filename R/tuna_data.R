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
