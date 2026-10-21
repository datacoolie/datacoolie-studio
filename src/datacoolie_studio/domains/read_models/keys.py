OVERVIEW = "environment-overview"
ASSETS_CATALOG = "assets.catalog"
ASSETS_IDENTITY_CATALOG = "assets.identity-catalog"
ASSETS_IDENTITY_MAP = "assets.identity-map"
LINEAGE_GRAPH = "lineage.graph"
LINEAGE_LATEST_RUNS = "lineage.latest-runs"


def monitoring_page(page: str) -> str:
    return f"monitoring.page.{page}"
