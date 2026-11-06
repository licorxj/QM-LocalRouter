import React from "react";
import { cn } from "@/lib/utils";
import { Card, CardContent } from "@/components/ui/card";

type EmptyStateProps = {
  icon?: React.ReactNode;
  title: React.ReactNode;
  description?: React.ReactNode;
  action?: React.ReactNode;
  className?: string;
};

/** Shared dashed-border empty state with an oversized ghost icon. */
export function EmptyState({ icon, title, description, action, className }: EmptyStateProps) {
  return (
    <Card className={cn("border-dashed bg-transparent shadow-none", className)}>
      <CardContent className="flex flex-col items-center justify-center gap-3 px-6 py-14 text-center">
        {icon ? (
          <div aria-hidden="true" className="empty-icon [&>svg]:h-12 [&>svg]:w-12 text-foreground">
            {icon}
          </div>
        ) : null}
        <div>
          <p className="text-sm font-medium">{title}</p>
          {description ? (
            <p className="mt-1 text-sm text-muted-foreground">{description}</p>
          ) : null}
        </div>
        {action ? <div className="mt-1">{action}</div> : null}
      </CardContent>
    </Card>
  );
}
