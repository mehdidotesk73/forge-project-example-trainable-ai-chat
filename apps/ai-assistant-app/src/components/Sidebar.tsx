import React from "react";
import { Container, Navbar } from "@forge-suite/ts";
import type { Page } from "../App.js";

const NAV_ITEMS = [{ id: "Ai Chat", label: "AI Chat", icon: "◈" }] as const;

interface Props {
  activePage: Page;
  onNavigate: (p: Page) => void;
}

export function Sidebar({ activePage, onNavigate }: Props) {
  return (
    <Container
      direction='column'
      size='220px'
      separator
      style={{
        minHeight: "100vh",
        background: "var(--bg-panel)",
        borderRight: "1px solid var(--border)",
      }}
    >
      <Container direction='row' gap={8} padding='18px 16px 14px'>
        <span style={{ fontWeight: 700, fontSize: 15 }}>ai-assistant-app</span>
      </Container>
      <Navbar
        orientation='vertical'
        items={NAV_ITEMS.map((item) => ({
          id: item.id,
          label: item.label,
          icon: item.icon,
          active: activePage === item.id,
          onClick: () => onNavigate(item.id as Page),
        }))}
        style={{ padding: "4px 0" }}
      />
    </Container>
  );
}
