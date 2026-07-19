import type { Metadata } from "next";
import { Suspense } from "react";
import { ThemeProvider } from "next-themes";
import "./globals.css";
import { DemoShell } from "@/components/demo-shell";

export const metadata: Metadata = {
  title: "Accrual Agent — Close Demo",
  description:
    "Interactive demo of the autonomous month-end accrual agent: NetSuite + Zip identification, vendor confirmations, and journal-entry write-back.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body className="antialiased">
        <ThemeProvider attribute="class" defaultTheme="system" enableSystem>
          <Suspense>
            <DemoShell>{children}</DemoShell>
          </Suspense>
        </ThemeProvider>
      </body>
    </html>
  );
}
