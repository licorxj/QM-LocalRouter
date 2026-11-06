import React from "react";
import { cn } from "@/lib/utils";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";

type Tone = "primary" | "success" | "warning" | "destructive" | "info" | "neutral";

const toneTile: Record<Tone, string> = {
  primary: "bg-primary/12 text-primary",
  success: "bg-success/12 text-success",
  warning: "bg-warning/12 text-warning",
  destructive: "bg-destructive/12 text-destructive",
  info: "bg-info/12 text-info",
  neutral: "bg-muted text-muted-foreground",
};

const toneHint: Record<Tone, string> = {
  primary: "text-primary",
  success: "text-success",
  warning: "text-warning",
  destructive: "text-destructive",
  info: "text-info",
  neutral: "text-muted-foreground",
};

type StatCardProps = {
  label: React.ReactNode;
  value: React.ReactNode;
  icon?: React.ReactNode;
  tone?: Tone;
  /** Small context line under the value, e.g. delta or unit. */
  hint?: React.ReactNode;
  loading?: boolean;
  className?: string;
};

export function StatCard({ label, value, icon, tone = "neutral", hint, loading, className }: StatCardProps) {
  return (
    <Card className={cn("card-hover overflow-hidden", className)}>
      <CardContent className="flex items-start gap-4 p-5">
        {icon ? (
          <div
            aria-hidden="true"
            className={cn(
              "flex h-10 w-10 flex-none items-center justify-center rounded-xl [&>svg]:h-5 [&>svg]:w-5",
              toneTile[tone]
            )}
          >
            {icon}
          </div>
        ) : null}
        <div className="min-w-0 flex-1">
          <p className="truncate text-xs font-medium uppercase tracking-wide text-muted-foreground">
            {label}
          </p>
          {loading ? (
            <Skeleton className="mt-2 h-7 w-20" />
          ) : (
            <p className="mt-1 text-2xl font-bold tabular-nums tracking-tight">{value}</p>
          )}
          {hint ? (
            <p className={cn("mt-1 truncate text-xs", toneHint[tone])}>{hint}</p>
          ) : null}
        </div>
      </CardContent>
    </Card>
  );
}
