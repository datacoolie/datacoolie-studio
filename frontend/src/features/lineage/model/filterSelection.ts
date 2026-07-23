export function toggleFilterValue(
  values: string[],
  option: string,
  additive: boolean,
): string[] {
  if (values.includes(option)) {
    return values.filter((value) => value !== option);
  }
  return additive ? [...values, option] : [option];
}
