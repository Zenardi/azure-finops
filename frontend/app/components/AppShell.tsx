"use client";

import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import AuthGate from "./AuthGate";
import Sidebar, { MenuIcon } from "./Nav";

const COLLAPSE_KEY = "cw.sidebar.collapsed";

/**
 * App shell: a grid of grouped left sidebar + content. Owns the desktop
 * collapse preference (persisted) and the mobile drawer state. The `/login`
 * route renders chrome-less (no sidebar) so the unauthenticated screen stays
 * clean and there's no redirect-loop surface.
 */
export default function AppShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const noChrome = pathname === "/login";

  const [collapsed, setCollapsed] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);
  const [hydrated, setHydrated] = useState(false);

  // Restore the collapse preference after mount (avoids SSR hydration mismatch).
  useEffect(() => {
    try {
      setCollapsed(localStorage.getItem(COLLAPSE_KEY) === "1");
    } catch {
      /* localStorage unavailable — keep default */
    }
    setHydrated(true);
  }, []);

  useEffect(() => {
    if (!hydrated) return;
    try {
      localStorage.setItem(COLLAPSE_KEY, collapsed ? "1" : "0");
    } catch {
      /* ignore */
    }
  }, [collapsed, hydrated]);

  // Close the mobile drawer whenever the route changes.
  useEffect(() => {
    setMobileOpen(false);
  }, [pathname]);

  // Lock body scroll while the mobile drawer is open.
  useEffect(() => {
    document.body.style.overflow = mobileOpen ? "hidden" : "";
    return () => {
      document.body.style.overflow = "";
    };
  }, [mobileOpen]);

  if (noChrome) {
    return (
      <div className="app-shell" data-nochrome="true">
        <main className="content">
          <div className="container">
            <AuthGate>{children}</AuthGate>
          </div>
        </main>
      </div>
    );
  }

  return (
    <div
      className="app-shell"
      data-collapsed={collapsed ? "true" : "false"}
      data-mobile-open={mobileOpen ? "true" : "false"}
    >
      <div className="mobile-bar">
        <button
          className="hamburger"
          aria-label="Open navigation"
          aria-expanded={mobileOpen}
          onClick={() => setMobileOpen(true)}
        >
          <MenuIcon />
        </button>
        <span className="mobile-brand">
          <span aria-hidden>🛡️</span> CloudWarden
        </span>
      </div>

      <div className="scrim" onClick={() => setMobileOpen(false)} aria-hidden />

      <Sidebar
        collapsed={collapsed}
        onToggleCollapse={() => setCollapsed((c) => !c)}
        onCloseMobile={() => setMobileOpen(false)}
      />

      <main className="content">
        <div className="container">
          <AuthGate>{children}</AuthGate>
        </div>
      </main>
    </div>
  );
}
