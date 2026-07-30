"use client";

import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";
import { Bot, Repeat } from "lucide-react";
import { DemoProvider, useDemo } from "./demo-context";
import { DayStepper } from "./day-stepper";
import { LaunchChooser } from "./launch-chooser";
import { ThemeToggle } from "./theme-toggle";
import { DATASET_META, datasetCompany, isDatasetKey } from "@/lib/data";
import { cn } from "@/lib/cn";

const NAV = [
  { href: "/", label: "Overview" },
  { href: "/register", label: "Register" },
  { href: "/journal", label: "Journal entries" },
  { href: "/attention", label: "Needs attention" },
];

function NavLinks() {
  const pathname = usePathname();
  const { step, raw, datasetKey } = useDemo();
  const attentionCount =
    step.kpis.held + step.kpis.unconfirmed + step.kpis.openEscalations;
  return (
    <nav className="flex gap-1 overflow-x-auto">
      {NAV.map(({ href, label }) => {
        const active = pathname === href;
        const query = { mode: datasetKey, day: raw.id };
        return (
          <Link
            key={href}
            href={{ pathname: href, query }}
            className={cn(
              "relative whitespace-nowrap rounded-lg px-3 py-1.5 text-xs font-medium transition-colors",
              active
                ? "bg-card text-foreground shadow-sm border border-border"
                : "text-muted hover:text-foreground"
            )}
          >
            {label}
            {href === "/attention" && attentionCount > 0 && (
              <span className="ml-1.5 inline-flex min-w-4 items-center justify-center rounded-full bg-red-100 px-1 text-[10px] font-bold text-red-700 dark:bg-red-500/20 dark:text-red-300">
                {attentionCount}
              </span>
            )}
          </Link>
        );
      })}
    </nav>
  );
}

function DatasetBadge() {
  const { datasetKey } = useDemo();
  return (
    <Link
      href="/"
      title="Switch dataset"
      className="inline-flex items-center gap-1.5 rounded-lg border border-border px-2.5 py-1.5 text-xs font-medium text-muted transition-colors hover:text-foreground"
    >
      <Repeat className="size-3.5" />
      <span className="hidden sm:inline">{DATASET_META[datasetKey].tagline}</span>
      <span className="sm:hidden">{datasetKey}</span>
    </Link>
  );
}

function Header() {
  const { raw, data, datasetKey } = useDemo();
  const company = data.company ?? datasetCompany(datasetKey);
  return (
    <header className="flex flex-wrap items-center justify-between gap-3">
      <div className="flex items-center gap-3">
        <div className="flex size-9 items-center justify-center rounded-xl bg-accent/10 text-accent">
          <Bot className="size-5" />
        </div>
        <div>
          <h1 className="text-base font-bold leading-tight">
            {company} — accrual close {data.period}
          </h1>
          <p className="text-xs text-muted">
            Autonomous accrual agent · NetSuite + Zip · day {raw.closeDay} of{" "}
            {data.finalCloseDay} · {DATASET_META[datasetKey].tagline.toLowerCase()}
          </p>
        </div>
      </div>
      <div className="flex items-center gap-3">
        <NavLinks />
        <DatasetBadge />
        <ThemeToggle />
      </div>
    </header>
  );
}

export function DemoShell({ children }: { children: React.ReactNode }) {
  const mode = useSearchParams().get("mode");
  if (!isDatasetKey(mode)) {
    return <LaunchChooser />;
  }
  return (
    <DemoProvider key={mode} datasetKey={mode}>
      <div className="mx-auto flex min-h-screen w-full max-w-6xl flex-col gap-4 px-4 py-5 sm:px-6">
        <Header />
        <DayStepper />
        <main className="flex-1">{children}</main>
        <footer className="pb-2 pt-6 text-center text-[11px] text-muted">
          Demo environment — all vendors, amounts, and emails are simulated. Data
          exported from <code>accrual-agent export-web</code>.
        </footer>
      </div>
    </DemoProvider>
  );
}
