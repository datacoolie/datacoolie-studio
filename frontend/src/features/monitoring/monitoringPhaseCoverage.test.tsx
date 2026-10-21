import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { DataflowPhaseCell, HealthStripCard, RuntimePhaseContribution, RuntimePhaseMatrixTooltip, runtimePhaseContributionTooltip } from "./components/monitoringPresentation";
import { scatterObservations } from "./components/monitoringObservation";
import { performancePageTestUtils } from "./pages/PerformancePage";
import { maintenanceEfficiencyOption } from "./pages/MaintenancePage";
import { VolumeRoutePanel } from "./pages/VolumePage";
import { DataflowSummaryInsight, summaryPhaseTiming } from "./details/DataflowRunSummary";
import { buildDataflowStory } from "./details/dataflowStoryModel";
import { phaseContributionBottleneck, rowPhaseContribution } from "./monitoringPhaseContribution";

type ScatterOption = { series?: Array<{ data: number[][] }>; graphic?: { style: { text: string } } };

describe("Monitoring phase and measurement coverage", () => {
  it("keeps Monitoring visual help owned by the Monitoring presentation boundary", () => {
    const html = renderToStaticMarkup(<HealthStripCard label="Runs" value={12} title="Runs in the selected range" />);
    expect(html).toContain("help-card-corner");
    expect(html).toContain('aria-label="About Runs"');
  });

  it.each([
    {name:"partial failed source",raw:{status:"failed",attempts:[{source:{status:"failed",duration_seconds:2},destination:{duration_seconds:8}}]},durations:[2,null,8,null],shares:[20,null,80,null],label:"Destination"},
    {name:"tied phases",raw:{attempts:[{duration_seconds:8,source:{duration_seconds:4},destination:{duration_seconds:4}}]},durations:[4,null,4,0],shares:[50,null,50,0],label:"Source · Destination"},
    {name:"all zero",raw:{attempts:[{duration_seconds:0,source:{duration_seconds:0}}]},durations:[0,null,null,0],shares:[null,null,null,null],label:"unk"},
    {name:"no attempt",raw:{attempts:[]},durations:[null,null,null,null],shares:[null,null,null,null],label:"unk"},
    {name:"invalid duration",raw:{attempts:[{duration_seconds:5,source:{duration_seconds:false},destination:{duration_seconds:3}}]},durations:[null,null,3,null],shares:[null,null,100,null],label:"Destination"},
    {name:"reported precedence",raw:{performance_bottleneck_phase:"source",attempts:[{duration_seconds:10,source:{duration_seconds:2},destination:{duration_seconds:8}}]},durations:[2,null,8,0],shares:[20,null,80,0],label:"Source"},
  ])("shares exact table/drawer interpretation for $name", ({raw,durations,shares,label})=>{
    const row={phase_health:"source_failed",performance_bottleneck_phase:"unknown",source_duration_seconds:999,overhead_duration_seconds:500,raw_record_json:JSON.stringify(raw)};
    const {root,timing}=rowPhaseContribution(row);const model=buildDataflowStory(row);
    expect(timing).toEqual(summaryPhaseTiming(model));expect(timing.phases.map(phase=>phase.duration)).toEqual(durations);expect(timing.phases.map(phase=>phase.share)).toEqual(shares);expect(phaseContributionBottleneck(root,timing).label).toBe(label);
    const table=renderToStaticMarkup(<DataflowPhaseCell row={row}/>);expect(table).toContain(`Bottleneck: ${label}`);expect(table).not.toContain('Source: 16.65m');
  });

  it("does not replace invalid or absent native timing with projections",()=>{
    for(const raw_record_json of ['invalid-json','null','{}','{"attempts":[]}']){
      const row={raw_record_json,source_duration_seconds:4,destination_duration_seconds:9,overhead_duration_seconds:3};
      expect(rowPhaseContribution(row).timing).toEqual(summaryPhaseTiming(buildDataflowStory(row)));expect(rowPhaseContribution(row).timing.proportional).toBe(false);
      expect(renderToStaticMarkup(<DataflowPhaseCell row={row}/>)).not.toContain('<i class="phase-');
    }
    const row={raw_record_json:"",attempts:[{duration_seconds:3,source:{duration_seconds:2}}]};expect(rowPhaseContribution(row).timing).toEqual(summaryPhaseTiming(buildDataflowStory(row)));
  });

  it("uses observed projection-only queue values without health/failure gates or missing-as-zero",()=>{
    const row={phase_health:"source_failed",performance_bottleneck_phase:"unknown",source_duration_seconds:2,transform_duration_seconds:null,destination_duration_seconds:8,overhead_duration_seconds:null};
    const {root,timing}=rowPhaseContribution(row);expect(timing.phases.map(phase=>phase.share)).toEqual([20,null,80,null]);expect(phaseContributionBottleneck(root,timing).label).toBe("Destination");expect(renderToStaticMarkup(<DataflowPhaseCell row={row}/>)).toContain('Transform: Unavailable');
  });
  it.each(["succeeded","failed","skipped","running","pending","unknown"])("matches the drawer's native timing for %s despite conflicting projections", status=>{
    const raw={status,attempts:[{duration_seconds:100,source:{duration_seconds:100}},{duration_seconds:10,source:{status:"failed",duration_seconds:2},transform:{duration_seconds:1},destination:{duration_seconds:5}}]};
    const row={...raw,raw_record_json:JSON.stringify(raw),phase_health:"source_failed",performance_bottleneck_phase:"unknown",source_duration_seconds:99,transform_duration_seconds:0,destination_duration_seconds:0,overhead_duration_seconds:999};
    const model=buildDataflowStory(row);const table=renderToStaticMarkup(<DataflowPhaseCell row={row}/>);const drawer=renderToStaticMarkup(<DataflowSummaryInsight model={model}/>);
    expect(table).toContain('>Destination</span>');expect(drawer).toContain('>Destination</span>');
    for(const phase of summaryPhaseTiming(model).phases)if(phase.share)expect(table).toContain(`flex:0 0 ${phase.share}%`);
  });
  it("derives measured bottlenecks despite unknown projection without inventing Source from zero durations", () => {
    const html = renderToStaticMarkup(<DataflowPhaseCell row={{ performance_bottleneck_phase: "unknown", destination_duration_seconds: 7 }} />);
    expect(html).toContain(">Destination</span>");
    expect(html).toContain('<i class="phase-destination"');
    expect(renderToStaticMarkup(<DataflowPhaseCell row={{}} />)).toContain(">unk</strong>");
    expect(renderToStaticMarkup(<DataflowPhaseCell row={{ performance_bottleneck_phase: "destination", destination_duration_seconds: 7 }} />)).toContain(">Destination</span>");
  });

  it("retains unavailable phase groups and explains observed final-attempt coverage", () => {
    const row = { operation_type: "maintenance", phase_runs_measured: 0, phase_runs_eligible: 44, total_duration_seconds: null };
    const html = renderToStaticMarkup(<RuntimePhaseContribution rows={[row]} showLegend={false} />);
    expect(html).toContain("maintenance");
    expect(html).toContain('aria-label="44"');
    expect(html).toContain('>44</span> runs');
    expect(html).not.toContain("0 / 44");
    expect(html).toContain("Phase timing unavailable");
    const tooltip = renderToStaticMarkup(<RuntimePhaseMatrixTooltip row={row} labelKey="operation_type" />);
    expect(tooltip).toContain('aria-label="Phase contribution for maintenance"');
    expect(tooltip).toContain("Unavailable");
    expect(tooltip).not.toContain("help-context");
    expect(tooltip).toContain("<tfoot");
    expect(runtimePhaseContributionTooltip("operation")).toContain("final-attempt duration");
  });

  it("shows a recorded failure without inventing its missing duration", () => {
    const row = { operation_type: "etl", phase_runs_measured: 1, phase_runs_eligible: 1,
      total_duration_seconds: 2, source_duration_seconds: 2, source_duration_percent: 100,
      source_run_count: 1, destination_duration_seconds: null, destination_duration_percent: null,
      destination_run_count: 0, destination_failed: 1 };
    const html = renderToStaticMarkup(<RuntimePhaseMatrixTooltip row={row} labelKey="operation_type" />);
    expect(html).toContain('data-label="Phase"><span class="phase-text-destination">Destination</span></td>');
    expect(html).toContain('data-label="Total">Unavailable</td>');
    expect(html).toContain('data-label="Runs"><span class="phase-text-destination">0</span></td>');
    expect(html).toContain('data-label="Failed" class="help-tone-failed">1</td>');
  });

  it("excludes unknown scatter coordinates while preserving measured zero", () => {
    const rows = [{ x: null, y: 2 }, { x: 3, y: null }, { x: "0", y: 2 }];
    const observed = scatterObservations(rows, (row) => row.x, (row) => row.y);
    expect(observed.points.map(({ x, y }) => [x, y])).toEqual([[0, 2]]);
    expect(observed.coverage).toBe("1 / 3 points measured; 2 missing measurements");
  });

  it("filters both efficiency charts and maintains the tooltip's source row", () => {
    const rows = [
      { dataflow_name: "unknown", rows_processed: null, maintenance_bytes_processed: null, bytes_reclaimed: null, duration_seconds: 4, paired_bytes_added:null, paired_bytes_runtime:null, paired_bytes_runs:null },
      { dataflow_name: "measured", rows_processed: 0, maintenance_bytes_processed: 0, bytes_reclaimed: 0, duration_seconds: 2, paired_bytes_added:0, paired_bytes_runtime:2, paired_bytes_runs:1 },
    ];
    const performance = performancePageTestUtils.workloadEfficiencyOption(rows, "linear", "maintenance") as ScatterOption;
    const maintenance = maintenanceEfficiencyOption(rows, "linear") as ScatterOption;
    expect(performance.series?.[0].data).toEqual([[0, 2]]);
    expect(maintenance.series?.[0].data).toEqual([[0, 2]]);
    const empty = performancePageTestUtils.workloadEfficiencyOption(rows.slice(0, 1), "linear", "maintenance") as ScatterOption;
    expect(empty.graphic?.style.text).toContain("0 / 1 point · 1 missing");
  });

  it("renders routes with observed row evidence and measurement coverage", () => {
    const row = { source_name: "oracle", destination_name: "parquet", rows_read: null, observed_rows_read: "224", rows_read_measured: "28", rows_read_eligible: "30", rows_written: null, runs: 30 };
    const html = renderToStaticMarkup(<VolumeRoutePanel rows={[row]} />);
    expect(html).toContain("oracle");
    expect(html).toContain('aria-description="224"');
    expect(html).not.toContain("observed</span>");
    expect(html).not.toContain("runs measured");
    expect(html).toContain('volume-route-value-neutral">—</strong>');
    const unknown = renderToStaticMarkup(<VolumeRoutePanel rows={[{ ...row, rows_read_measured: "0" }]} />);
    expect(unknown).toContain("No route volume signals");
    const writtenCases: Array<Record<string, string | number | null>> = [
      { rows_written: 0 },
      { rows_written: null, observed_rows_written: "0", rows_written_measured: "28", rows_written_eligible: "30" },
    ];
    for (const written of writtenCases) {
      const measuredZero = renderToStaticMarkup(<VolumeRoutePanel rows={[{ ...row, ...written }]} />);
      expect(measuredZero).toContain("Rows written: 0");
      expect(measuredZero).not.toContain("Destination volume unavailable");
      expect(measuredZero).toContain('volume-route-value-observed');
    }
  });
});
