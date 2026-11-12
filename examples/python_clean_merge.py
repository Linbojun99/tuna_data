"""Run from any working directory after installing tuna-data[excel,parquet]."""
from pathlib import Path

from tuna_data import Library
from tuna_data.pipeline import build_annual, read_processed, aggregate_catch

# Uses TUNA_DATA_HOME, then the existing local config. A mounted NAS also works:
# db = Library("/mnt/lab-nas/tuna-library")
db = Library()
repo = Path(__file__).resolve().parents[1]
result = build_annual(repo / "templates" / "annual_catch.toml", library=db)
run = result["run_dir"]
global_catch = read_processed("global_catch", run=run)
pacific_catch = read_processed("pacific_catch", run=run)
by_gear = read_processed("global_by_gear", run=run)
pacific_yft = pacific_catch.loc[pacific_catch.species_code.eq("YFT")]
longline = global_catch.loc[global_catch.gear_group.eq("longline")]
print(by_gear.tail(10).to_string(index=False))
print(aggregate_catch(pacific_yft).tail(10).to_string(index=False))
print("Saved:", run)
# Reproduce later using run/recipe.lock.toml, or read these fixed outputs.
# build_annual(Path(run) / "recipe.lock.toml", library=db)
