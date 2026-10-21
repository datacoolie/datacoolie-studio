import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { MaintenanceSourceCell } from "./details/MaintenanceDetails";
import { DataflowRouteEndpoint } from "./details/detailPrimitives";
import { monitoringConnectionStageClass } from "./monitoringStagePresentation";
import { EndpointRouteNode } from "./pages/DataflowsPagePresentation";
import { FailureRouteCell } from "./pages/FailurePageSupport";

describe("Monitoring connection stage presentation", () => {
  it.each([
    ["wwi_bronze_orders", "monitor-stage-bronze"],
    ["wwi_bronze_orders_silver_summary", "monitor-stage-bronze"],
    ["wwi_silver_orders_bronze_archive", "monitor-stage-silver"],
    ["WWI-GOLD-ORDERS", "monitor-stage-gold"],
    ["external_resource", ""],
    ["all", ""],
  ])("maps %s to %s", (connectionName, expectedClass) => {
    expect(monitoringConnectionStageClass(connectionName)).toBe(expectedClass);
  });

  it("colors the Dataflows health endpoint node without coloring its route container", () => {
    const markup = renderToStaticMarkup(createElement(EndpointRouteNode, {
      endpoint: {
        locator: "orders",
        connection: "wwi_bronze_source",
        connectionType: "lakehouse",
        format: "delta",
      },
    }));

    expect(markup).toContain("dataflow-route-endpoint is-connection-only monitor-stage-bronze");
    expect(markup).not.toContain("dataflow-route-line monitor-stage-bronze");
  });

  it("colors source and destination drawer endpoints independently", () => {
    const markup = renderToStaticMarkup(createElement("div", null,
      createElement(DataflowRouteEndpoint, {
        direction: "source",
        endpoint: { asset: "orders", connection: "wwi_bronze_source", format: "delta" },
      }),
      createElement(DataflowRouteEndpoint, {
        direction: "destination",
        endpoint: { asset: "orders", connection: "wwi_gold_destination", format: "delta" },
      }),
    ));

    expect(markup).toContain("monitoring-dataflow-route-endpoint is-source monitor-stage-bronze");
    expect(markup).toContain("monitoring-dataflow-route-endpoint is-destination monitor-stage-gold");
  });

  it("colors Failure endpoint impact nodes independently", () => {
    const markup = renderToStaticMarkup(createElement(FailureRouteCell, {
      row: {
        source_name: "wwi_bronze_source",
        destination_name: "wwi_silver_destination",
        source_format: "delta",
        destination_format: "delta",
      },
    }));

    expect(markup).toContain("failure-route-node monitor-stage-bronze");
    expect(markup).toContain("failure-route-node monitor-stage-silver");
    expect(markup).not.toContain("failure-route-cell monitor-stage-");
  });

  it("colors only the source cell in the Destination table drawer", () => {
    const markup = renderToStaticMarkup(createElement(MaintenanceSourceCell, {
      row: { source_name: "wwi_gold_source", source_full_table: "catalog.schema.orders" },
    }));

    expect(markup).toContain("maintenance-source-cell monitor-stage-gold");
  });
});
