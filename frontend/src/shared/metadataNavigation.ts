export interface MetadataNavigationTarget {
  dataflowIds: string[];
  fallbackQuery?: string;
}

export function metadataNavigationTarget(dataflowIds: Array<string | null | undefined>, fallbackQuery?: string): MetadataNavigationTarget {
  return {
    dataflowIds: [...new Set(dataflowIds.filter((value): value is string => Boolean(value?.trim())))],
    fallbackQuery,
  };
}
