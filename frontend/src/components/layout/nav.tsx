import {
  Activity,
  GitBranch,
  LayoutDashboard,
  MessageSquare,
  Network,
  Route,
  ScrollText,
  Server,
  Settings,
  UserRound,
} from "lucide-react";
import { useI18n } from "../../i18n";

export function useNavItems() {
  const { t } = useI18n();
  return [
    { to: "/", icon: LayoutDashboard, label: t("sidebar.dashboard"), section: "overview" },
    { to: "/accounts", icon: UserRound, label: t("sidebar.accounts"), section: "access" },
    { to: "/providers", icon: Server, label: t("sidebar.providers"), section: "access" },
    { to: "/strategies", icon: GitBranch, label: t("sidebar.strategies"), section: "access" },
    { to: "/chat", icon: MessageSquare, label: t("sidebar.chat"), section: "observe" },
    { to: "/logs", icon: ScrollText, label: t("sidebar.logs"), section: "observe" },
    { to: "/monitor", icon: Activity, label: t("sidebar.monitor"), section: "observe" },
    { to: "/endpoints", icon: Network, label: t("sidebar.endpoints"), section: "config" },
    { to: "/forwarding", icon: Route, label: t("sidebar.forwarding"), section: "config" },
    { to: "/settings", icon: Settings, label: t("sidebar.settings"), section: "config" },
  ];
}
