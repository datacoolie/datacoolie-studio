import { useEffect, useMemo, useRef, useState } from "react";
import { cachedLineageLayout, layoutLineage } from "../layout/elkLayout";
import type { LineageFlow } from "../model/types";

export function useLineageLayout(flow: LineageFlow, layoutKey: string) {
  const [layout, setLayout] = useState<{
    key: string;
    positions: Map<string, { x: number; y: number }>;
  } | null>(() => {
    const positions = cachedLineageLayout(layoutKey);
    return positions ? { key: layoutKey, positions } : null;
  });
  const generation = useRef(0);

  useEffect(() => {
    const currentGeneration = ++generation.current;
    if (!flow.nodes.length) {
      setLayout({ key: layoutKey, positions: new Map() });
      return;
    }
    const cached = cachedLineageLayout(layoutKey);
    if (cached) {
      setLayout({ key: layoutKey, positions: cached });
      return;
    }
    const timer = window.setTimeout(() => {
      void layoutLineage(flow.nodes, flow.edges, layoutKey).then((positions) => {
        if (generation.current === currentGeneration) setLayout({ key: layoutKey, positions });
      });
    }, 80);
    return () => window.clearTimeout(timer);
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
