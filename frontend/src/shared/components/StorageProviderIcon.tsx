import amazonS3Icon from "@iconify-icons/simple-icons/amazons3";
import databricksIcon from "@iconify-icons/simple-icons/databricks";
import googleCloudIcon from "@iconify-icons/simple-icons/googlecloud";
import microsoftIcon from "@iconify-icons/simple-icons/microsoft";
import microsoftAzureIcon from "@iconify-icons/simple-icons/microsoftazure";
import minioIcon from "@iconify-icons/simple-icons/minio";
import { Icon } from "@iconify/react";
import { Folder } from "lucide-react";

import type { StorageProvider } from "../api/domainTypes";

const PROVIDER_ICONS = {
  s3: amazonS3Icon,
  minio: minioIcon,
  adls: microsoftAzureIcon,
  onelake: microsoftIcon,
  gcs: googleCloudIcon,
  dbfs: databricksIcon,
} satisfies Record<Exclude<StorageProvider, "local">, typeof amazonS3Icon>;

export function StorageProviderIcon({
  provider,
  size = 16,
}: {
  provider: StorageProvider;
  size?: number;
}) {
  if (provider === "local") return <Folder size={size} />;
  return <Icon icon={PROVIDER_ICONS[provider]} width={size} height={size} />;
}
