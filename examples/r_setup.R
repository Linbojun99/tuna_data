# 新电脑只需运行一次；以后更新 Python 接口时也可重跑。
# 前提：已安装 R 和 Python 3.11–3.13（推荐 3.12），并下载完整代码仓库。
# source("/你的代码目录/tuna_data/examples/r_setup.R")
# 只安装代码与依赖，不下载研究数据、不修改数据路径或 .Rprofile。

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
if (!requireNamespace("reticulate", quietly = TRUE)) {
  install.packages("reticulate", repos = "https://cloud.r-project.org")
}
venv_dir <- path.expand(Sys.getenv("TUNA_SETUP_VENV", unset = "~/.venvs/tuna-data"))
python_rel <- if (.Platform$OS.type == "windows") "Scripts/python.exe" else "bin/python"
python_bin <- file.path(venv_dir, python_rel)

if (!file.exists(python_bin)) {
  # 只创建新环境，不覆盖同名的非空文件夹。
  if (dir.exists(venv_dir) && length(list.files(venv_dir, all.files = TRUE, no.. = TRUE))) {
    stop("目标目录非空且不是可用的 Python 环境：", venv_dir)
  }
  python_base <- Sys.getenv("TUNA_SETUP_PYTHON", unset = "")
  if (!nzchar(python_base)) {
    python_base <- reticulate::virtualenv_starter(version = ">=3.11,<3.14")
  }
  if (is.null(python_base) || !nzchar(python_base) || !file.exists(python_base)) {
    stop("未找到 Python 3.11–3.13。请先安装 Python 3.12，或设置 TUNA_SETUP_PYTHON 为其可执行文件路径。")
  }
  dir.create(dirname(venv_dir), recursive = TRUE, showWarnings = FALSE)
  status <- system2(python_base, c("-m", "venv", shQuote(venv_dir)))
  if (status != 0L) stop("Python 环境创建失败；请检查上面的错误。")
}
# --editable 让后续代码更新直接生效；请保留代码仓库目录。
status <- system2(python_bin, c("-m", "pip", "install", "--editable",
                                shQuote(paste0(repo_dir, "[excel,parquet]"))))
if (status != 0L) stop("接口安装失败；请检查网络连接及上面的 pip 错误。")
Sys.setenv(RETICULATE_PYTHON = python_bin)
cat("安装完成。Python 路径：", python_bin, "\n", sep = "")
cat("接下来同步完整 library 数据目录，设置 TUNA_DATA_HOME，再运行 r_quickstart.R。\n")
cat("若本次 R 会话已加载其他 Python，请重启 R 后再读取。\n")
