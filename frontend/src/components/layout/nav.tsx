import {
  GitBranch,
  LayoutDashboard,
  MessageSquare,
  Network,
  Route,
  ScrollText,
  Server,
  Settings,
} from "lucide-react";
import { useI18n } from "../../i18n";

export function useNavItems() {
  const { t } = useI18n();
  return [
    { to: "/", icon: LayoutDashboard, label: t("sidebar.dashboard") },
    { to: "/chat", icon: MessageSquare, label: t("sidebar.chat") },
    { to: "/providers", icon: Server, label: t("sidebar.providers") },
    { to: "/strategies", icon: GitBranch, label: t("sidebar.strategies") },
    { to: "/logs", icon: ScrollText, label: t("sidebar.logs") },
    { to: "/endpoints", icon: Network, label: t("sidebar.endpoints") },
    { to: "/forwarding", icon: Route, label: t("sidebar.forwarding") },
    { to: "/settings", icon: Settings, label: t("sidebar.settings") },
  ];
}
