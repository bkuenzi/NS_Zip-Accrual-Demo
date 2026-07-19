"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Bot } from "lucide-react";
import { DemoProvider, useDemo } from "./demo-context";
import { DayStepper } from "./day-stepper";
import { ThemeToggle } from "./theme-toggle";
import { demoData } from "@/lib/data";
import { cn } from "@/lib/cn";

const NAV = [
  { href: "/", label: "Overview" },
  { href: "/register", label: "Register" },
  { href: "/journal", label: "Journal entries" },
  { href: "/attention", label: "Needs attention" },
];

function NavLinks() {
  const pathname = usePathname();
  const { step, raw } = useDemo();
  const attentionCount =
    step.kpis.held + step.kpis.unconfirmed + step.kpis.openEscalations;
  return (
    <nav className="flex gap-1 overflow-x-auto">
      {NAV.map(({ href, label }) => {
        const active = pathname === href;
        const query = { day: raw.id };
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

function Header() {
  const { raw } = useDemo();
  return (
    <header className="flex flex-wrap items-center justify-between gap-3">
      <div className="flex items-center gap-3">
        <div className="flex size-9 items-center justify-center rounded-xl bg-accent/10 text-accent">
          <Bot className="size-5" />
        </div>
        <div>
          <h1 className="text-base font-bold leading-tight">
            Accrual close — {demoData.period}
          </h1>
          <p className="text-xs text-muted">
            Autonomous accrual agent · NetSuite + Zip · day {raw.closeDay} of{" "}
            {demoData.finalCloseDay} · mock data
          </p>
        </div>
      </div>
      <div className="flex items-center gap-3">
        <NavLinks />
        <ThemeToggle />
      </div>
    </header>
  );
}

export function DemoShell({ children }: { children: React.ReactNode }) {
  return (
    <DemoProvider>
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
