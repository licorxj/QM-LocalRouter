import React from "react";
import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";

export type StatusTone = "success" | "warning" | "destructive" | "info" | "neutral";

const dotClass: Record<StatusTone, string> = {
  success: "bg-success",
  warning: "bg-warning",
  destructive: "bg-destructive",
  info: "bg-info",
  neutral: "bg-muted-foreground/50",
};

/** Small live-status dot (plain, no blur halo — halos read as noise at 8px). */
export function StatusDot({ tone = "neutral", pulse, className }: {
  tone?: StatusTone;
  pulse?: boolean;
  className?: string;
}) {
  return (
    <span
      aria-hidden="true"
      className={cn("inline-block h-2 w-2 flex-none rounded-full", dotClass[tone], pulse && "pulse-dot", className)}
    />
  );
}

const badgeVariant: Record<StatusTone, "success" | "warning" | "destructive" | "info" | "secondary"> = {
  success: "success",
  warning: "warning",
  destructive: "destructive",
  info: "info",
  neutral: "secondary",
};

/** Semantic status badge with optional leading dot. */
export function StatusBadge({ tone = "neutral", dot = true, children, className }: {
  tone?: StatusTone;
  dot?: boolean;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <Badge variant={badgeVariant[tone]} className={cn("gap-1.5 font-medium", className)}>
      {dot ? <StatusDot tone={tone} className="h-1.5 w-1.5" /> : null}
      {children}
    </Badge>
  );
}
