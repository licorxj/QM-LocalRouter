import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Globe, Link2, Network } from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "../ui/dialog";
import { Badge } from "../ui/badge";
import { Label } from "../ui/label";
import { CopyButton } from "../shared";
import { useI18n } from "../../i18n";
import { getSettings, getLanIp, getEndpointsInfo } from "../../services/api";

type AddressRowProps = {
  label: string;
  value: string;
  disabled?: boolean;
};

function AddressRow({ label, value, disabled }: AddressRowProps) {
  return (
    <div className="flex items-center gap-2 rounded-lg border px-3 py-2">
      <Badge variant="outline" className="shrink-0 text-[10px]">
        {label}
      </Badge>
      <code
        className={`min-w-0 flex-1 truncate font-mono text-xs ${disabled ? "text-muted-foreground/60" : "text-foreground"}`}
        title={value}
      >
        {value}
      </code>
      {!disabled && <CopyButton value={value} className="shrink-0" />}
    </div>
  );
}

/**
 * 顶栏「接入地址」弹窗：各协议端点（本机 / 局域网）+ 局域网地址，全部可复制。
 * 数据复用「端点和鉴权设置」页的接口：settings + lan-ip + endpoint registry。
 */
export default function AccessAddressDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
}) {
  const { t } = useI18n();
  const [lanIp, setLanIp] = useState("");

  useEffect(() => {
    if (!open) return;
    getLanIp()
      .then((r) => setLanIp(r.data.ip))
      .catch(() => {});
  }, [open]);

  const { data: appSettings } = useQuery({
    queryKey: ["appSettings"],
    queryFn: () => getSettings().then((r) => r.data),
    enabled: open,
  });
  const { data: endpointsInfo } = useQuery({
    queryKey: ["endpointsInfo"],
    queryFn: () => getEndpointsInfo().then((r) => r.data),
    enabled: open,
  });

  const backendPort = appSettings?.backend_port || 12002;
  const lanEnabled = !!(appSettings?.lan_access && lanIp);
  const localBase = `http://127.0.0.1:${backendPort}`;
  const lanBase = lanEnabled ? `http://${lanIp}:${backendPort}` : "";

  // 只列出启用分组里的 POST/路径端点；完整列表见「端点和鉴权设置」页
  const endpoints = (endpointsInfo?.endpoints || []).filter(
    (e: any) => e.method === "POST" && endpointsInfo?.enabled_groups?.[e.group] !== false,
  );

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl max-h-[80vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2 text-base">
            <Link2 className="h-4 w-4 text-info" aria-hidden="true" />
            {t("settings.endpointAddress")}
          </DialogTitle>
        </DialogHeader>
        <p className="text-xs text-muted-foreground -mt-1">
          {t("settings.endpointAddressSubtitle")}
        </p>

        <div className="space-y-5">
          {/* 本机 / 局域网 Base URL */}
          <div className="space-y-2">
            <div className="flex items-center gap-2">
              <Globe className="h-3.5 w-3.5 text-muted-foreground" aria-hidden="true" />
              <Label className="text-sm font-medium">{t("settings.accessUrls")}</Label>
            </div>
            <AddressRow label={t("settings.endpointAddressLocal")} value={`${localBase}/v1`} />
            <AddressRow
              label={t("settings.endpointAddressLan")}
              value={lanBase ? `${lanBase}/v1` : t("settings.endpointAddressLanHint")}
              disabled={!lanEnabled}
            />
          </div>

          {/* 各协议端点 */}
          {endpoints.length > 0 && (
            <div className="space-y-2">
              <div className="flex items-center gap-2">
                <Network className="h-3.5 w-3.5 text-muted-foreground" aria-hidden="true" />
                <Label className="text-sm font-medium">{t("settings.endpointAddressProto")}</Label>
              </div>
              <div className="space-y-1.5">
                {endpoints.map((e: any, i: number) => (
                  <div key={i} className="space-y-1">
                    <AddressRow label={e.group} value={`${localBase}${e.path}`} />
                    {lanEnabled && <AddressRow label={t("settings.endpointAddressLan")} value={`${lanBase}${e.path}`} />}
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
