export function freshnessFilterTransition(
  previousSignature: string,
  nextSignature: string,
  activeKind: string | null | undefined,
  stackKinds: readonly string[] = [],
) {
  const changed = previousSignature !== nextSignature;
  return {
    changed,
    shouldClose: changed && (
      activeKind === "freshness"
      || activeKind === "dataflow-registry"
      || stackKinds.includes("freshness")
      || stackKinds.includes("dataflow-registry")
    ),
  };
}
