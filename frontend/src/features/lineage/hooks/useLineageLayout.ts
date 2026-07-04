import { useEffect, useMemo, useState } from "react";
import { layoutLineage } from "../layout/elkLayout";
import type { LineageFlow } from "../model/types";

export function useLineageLayout(flow: LineageFlow, layoutKey: string) {
  const [layout, setLayout] = useState<{
    key: string;
    positions: Map<string, { x: number; y: number }>;
  } | null>(null);

  useEffect(() => {
    let cancelled = false;
    if (!flow.nodes.length) {
      setLayout({ key: layoutKey, positions: new Map() });
      return;
    }
    void layoutLineage(flow.nodes, flow.edges, layoutKey).then((positions) => {
      if (!cancelled) setLayout({ key: layoutKey, positions });
    });
    return () => {
      cancelled = true;
    };
  }, [layoutKey]);

  const ready = layout?.key === layoutKey;
  const nodes = useMemo(
    () => flow.nodes.map((node) => ({
      ...node,
      position: ready ? layout.positions.get(node.id) ?? node.position : node.position
    })),
    [flow.nodes, layout, ready]
  );
  return { nodes, ready };
}
