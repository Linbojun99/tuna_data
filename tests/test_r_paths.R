# Synthetic regression test; no research data, downloads or credentials.
# Set RETICULATE_PYTHON to an environment with tuna-data, then source this file
# or run Rscript tests/test_r_paths.R from the repository root.
source_files <- Filter(function(x) is.character(x) && length(x) == 1L && !is.na(x),
                       lapply(sys.frames(), function(frame) frame$ofile))
test_file <- if (length(source_files)) tail(source_files, 1L)[[1L]] else NULL
if (is.null(test_file)) {
  file_arg <- grep("^--file=", commandArgs(), value = TRUE)
  if (length(file_arg)) test_file <- sub("^--file=", "", file_arg[[1L]])
}
repo_dir <- normalizePath(file.path(dirname(test_file), ".."), winslash = "/")
source(file.path(repo_dir, "R", "tuna_data.R"), local = TRUE)

local({
  work <- tempfile("tuna-r-paths-")
  dir.create(work)
  work <- normalizePath(work, winslash = "/")
  previous <- Sys.getenv(c("TUNA_DATA_HOME", "TUNA_DATA_CONFIG"), unset = NA_character_)
  on.exit({
    for (key in names(previous)) {
      if (is.na(previous[[key]])) Sys.unsetenv(key)
      else do.call(Sys.setenv, stats::setNames(list(previous[[key]]), key))
    }
    # Restore the Python-side settings too if this test was sourced interactively.
    python_env <- reticulate::import("os", convert = TRUE)$environ
    for (key in names(previous)) {
      if (is.na(previous[[key]])) python_env$pop(key, NULL)
      else python_env$update(stats::setNames(list(previous[[key]]), key))
    }
    unlink(work, recursive = TRUE)
  }, add = TRUE)
  a_dir <- file.path(work, "a")
  b_dir <- file.path(work, "NAS path 含空格", "b")
  a_config <- file.path(work, "a.toml")
  b_config <- file.path(work, "b.toml")
  writeLines(c("[storage]", paste0("root = ", encodeString(a_dir, quote = '"'))), a_config)
  writeLines(c("[storage]", paste0("root = ", encodeString(b_dir, quote = '"'))), b_config)
  Sys.unsetenv("TUNA_DATA_HOME")
  Sys.setenv(TUNA_DATA_CONFIG = a_config)

  # Initialize Python and populate A while R's environment points to config A.
  a <- tuna_library()
  csv <- file.path(work, "sample.csv")
  writeLines(c("flag,value", "001,NA", "002,0"), csv)
  invisible(a$import_file(csv, "iotc/test"))
  expected <- tuna_read("iotc/test")
  stopifnot(identical(expected$flag, c("001", "002")), identical(expected$value, c("NA", "0")))
  invisible(a$mirror(b_dir))

  # Change the R environment after Python has already started.
  Sys.setenv(TUNA_DATA_HOME = b_dir)
  stopifnot(tuna_library()$root$as_posix() == b_dir)
  stopifnot(identical(as.matrix(tuna_read("iotc/test")), as.matrix(expected)))
  stopifnot(tuna_library(path = a_dir)$root$as_posix() == a_dir)

  # Unsetting the override must restore config A; changing config selects B.
  Sys.unsetenv("TUNA_DATA_HOME")
  stopifnot(tuna_library()$root$as_posix() == a_dir)
  Sys.setenv(TUNA_DATA_CONFIG = b_config)
  stopifnot(tuna_library()$root$as_posix() == b_dir)
  Sys.setenv(TUNA_DATA_HOME = a_dir)
  stopifnot(tuna_library()$root$as_posix() == a_dir)
  Sys.setenv(TUNA_DATA_HOME = "")
  stopifnot(tuna_library()$root$as_posix() == b_dir)
  cat("PASS: R path changes after Python initialization; explicit path, env/config precedence, unsets, spaces, Unicode, and lossless copy.\n")
})
