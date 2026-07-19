import { cn } from "@/lib/cn";

const VARIANTS = {
  primary:
    "bg-accent text-white hover:opacity-90 dark:text-slate-900 font-semibold",
  outline:
    "border border-border bg-card hover:bg-background text-foreground",
  success:
    "bg-green-600 text-white hover:bg-green-700 font-semibold",
  danger:
    "border border-red-300 text-red-700 hover:bg-red-50 dark:border-red-500/40 dark:text-red-300 dark:hover:bg-red-500/10",
  ghost: "text-muted hover:text-foreground",
} as const;

export function Button({
  variant = "outline",
  className,
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: keyof typeof VARIANTS;
}) {
  return (
    <button
      className={cn(
        "inline-flex cursor-pointer items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs transition-colors disabled:cursor-not-allowed disabled:opacity-40",
        VARIANTS[variant],
        className
      )}
      {...props}
    />
  );
}
