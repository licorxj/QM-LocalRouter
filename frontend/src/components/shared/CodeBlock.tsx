import React from "react";
import { cn } from "@/lib/utils";
import { CopyButton } from "@/components/shared/CopyButton";

type CodeBlockProps = {
  code: string;
  /** Optional caption above the block, e.g. the endpoint name. */
  label?: React.ReactNode;
  className?: string;
  codeClassName?: string;
};

/** Terminal-style code snippet with a copy affordance. */
export function CodeBlock({ code, label, className, codeClassName }: CodeBlockProps) {
  return (
    <div className={cn("group relative overflow-hidden rounded-lg border bg-muted/40", className)}>
      {label ? (
        <div className="flex items-center justify-between border-b bg-muted/40 px-3 py-1.5">
          <span className="text-xs font-medium text-muted-foreground">{label}</span>
          <CopyButton value={code} />
        </div>
      ) : (
        <CopyButton value={code} className="absolute right-2 top-2 opacity-0 transition-opacity group-hover:opacity-100 focus-visible:opacity-100" />
      )}
      <pre className="overflow-x-auto p-3 font-mono text-xs leading-relaxed text-foreground">
        <code className={codeClassName}>{code}</code>
      </pre>
    </div>
  );
}
