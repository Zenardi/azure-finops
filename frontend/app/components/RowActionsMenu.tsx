"use client";

import { useEffect, useLayoutEffect, useRef, useState } from "react";

export type MenuItem = {
  label: string;
  onClick: () => void;
  danger?: boolean;
  disabled?: boolean;
};

/**
 * Row "more actions" overflow menu — collapses the per-row action buttons into a
 * single kebab trigger plus a dropdown, shared across data tables (Subscriptions,
 * Policies, …). The dropdown is `position: fixed`, anchored to the trigger's rect,
 * so it escapes the table's `overflow: hidden` (which rounds the table corners and
 * would otherwise clip an absolutely-positioned child). It closes on outside click,
 * Escape, and scroll/resize — a fixed menu would otherwise detach from its trigger
 * as the page moves under it.
 *
 * `open`/`onToggle`/`onClose` are controlled by the parent so only one row's menu is
 * open at a time (the parent tracks the open row id).
 */
export function RowActionsMenu({
  label,
  items,
  open,
  onToggle,
  onClose,
}: {
  label: string;
  items: MenuItem[];
  open: boolean;
  onToggle: () => void;
  onClose: () => void;
}) {
  const btnRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState<{ left: number; top?: number; bottom?: number } | null>(null);

  // Anchor the fixed menu to the trigger; flip above when it would overflow below.
  useLayoutEffect(() => {
    if (!open || !btnRef.current) return;
    const r = btnRef.current.getBoundingClientRect();
    const MENU_W = 184;
    const estH = items.length * 38 + 12;
    const left = Math.max(8, r.right - MENU_W);
    const openUp = r.bottom + estH + 8 > window.innerHeight;
    setPos(
      openUp
        ? { left, bottom: window.innerHeight - r.top + 4 }
        : { left, top: r.bottom + 4 },
    );
  }, [open, items.length]);

  useEffect(() => {
    if (!open) return;
    const onDocDown = (e: MouseEvent) => {
      const t = e.target as Node;
      if (menuRef.current?.contains(t) || btnRef.current?.contains(t)) return;
      onClose();
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        onClose();
        btnRef.current?.focus();
      }
    };
    const dismiss = () => onClose();
    document.addEventListener("mousedown", onDocDown);
    document.addEventListener("keydown", onKey);
    window.addEventListener("scroll", dismiss, true);
    window.addEventListener("resize", dismiss);
    return () => {
      document.removeEventListener("mousedown", onDocDown);
      document.removeEventListener("keydown", onKey);
      window.removeEventListener("scroll", dismiss, true);
      window.removeEventListener("resize", dismiss);
    };
  }, [open, onClose]);

  // Move focus into the menu once positioned, so it's operable by keyboard.
  useEffect(() => {
    if (open && pos) {
      menuRef.current?.querySelector<HTMLButtonElement>("button:not([disabled])")?.focus();
    }
  }, [open, pos]);

  return (
    <>
      <button
        ref={btnRef}
        className="kebab-btn"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={label}
        onClick={onToggle}
      >
        <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
          <circle cx="12" cy="5" r="1.7" />
          <circle cx="12" cy="12" r="1.7" />
          <circle cx="12" cy="19" r="1.7" />
        </svg>
      </button>
      {open && pos && (
        <div
          ref={menuRef}
          className="menu"
          role="menu"
          aria-label={label}
          style={{ left: pos.left, top: pos.top, bottom: pos.bottom }}
        >
          {items.map((it, i) => (
            <button
              key={i}
              role="menuitem"
              className={it.danger ? "danger" : undefined}
              disabled={it.disabled}
              onClick={() => {
                it.onClick();
                onClose();
              }}
            >
              {it.label}
            </button>
          ))}
        </div>
      )}
    </>
  );
}
