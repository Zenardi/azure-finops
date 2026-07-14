import "./globals.css";
import type { Metadata } from "next";
import type { ReactNode } from "react";
import AppShell from "./components/AppShell";

export const metadata: Metadata = {
  title: "CloudWarden",
  description: "Multi-cloud governance-as-code & FinOps: policy posture, cost analysis, right-sizing recommendations, and guarded remediation.",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>
        <AppShell>{children}</AppShell>
      </body>
    </html>
  );
}
