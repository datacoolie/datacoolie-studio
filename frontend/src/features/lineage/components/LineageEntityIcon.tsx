import { Icon } from "@iconify/react";
import { assetTypeIconId, referenceTypeAssetType, type AssetIconKind } from "../../../shared/assetPresentation";
import { LineageFormatIcon } from "../../../shared/components/FormatIcon";

export function LineageEntityIcon({ iconKind, badge, referenceType, size = 18 }: {
  iconKind: AssetIconKind;
  badge: string;
  referenceType?: string | null;
  size?: number;
}) {
  if (referenceType) {
    return <Icon icon={assetTypeIconId(referenceTypeAssetType(referenceType))} width={size} height={size} aria-hidden="true" />;
  }
  return <LineageFormatIcon kind={iconKind} label={badge} size={size} />;
}
