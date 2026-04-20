from pull_nbarapm_data import pull_datasets
from pull_nba_stats import MANUAL_DIR


if __name__ == "__main__":
    pull_datasets(["mamba"], MANUAL_DIR / "playerlist.csv")
