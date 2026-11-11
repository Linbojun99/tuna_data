# Python core with an R interface. Set RETICULATE_PYTHON before sourcing if needed.
# install.packages("reticulate")

tuna_library <- function(path = NULL) {
  if (!requireNamespace("reticulate", quietly = TRUE)) {
    stop("Install reticulate: install.packages('reticulate')")
  }
  td <- reticulate::import("tuna_data", convert = TRUE)
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
