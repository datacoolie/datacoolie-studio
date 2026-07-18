OVERVIEW = "environment-overview"
ASSETS_CATALOG = "assets.catalog"
LINEAGE_GRAPH = "lineage.graph"
LINEAGE_LATEST_RUNS = "lineage.latest-runs"


def monitoring_page(page: str) -> str:
    return f"monitoring.page.{page}"
