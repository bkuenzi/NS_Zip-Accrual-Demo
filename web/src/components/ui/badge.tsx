import { cn } from "@/lib/cn";
import { STATUS_META } from "@/lib/status";
import type { AccrualStatus } from "@/lib/types";

export function Badge({
  className,
  children,
}: {
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 whitespace-nowrap rounded-full px-2 py-0.5 text-[11px] font-semibold",
        className
      )}
    >
      {children}
    </span>
  );
}

export function StatusBadge({ status }: { status: AccrualStatus }) {
  const meta = STATUS_META[status];
  return (
    <Badge className={meta.badge}>
      <span className={cn("size-1.5 rounded-full", meta.dot)} />
      {meta.label}
    </Badge>
  );
}
