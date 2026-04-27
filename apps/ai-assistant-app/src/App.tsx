import React, { useState } from "react";
import { Container } from "@forge-suite/ts";
import { AiChat } from "./pages/aiChat.js";

export type Page = "Ai Chat";

export function App() {
  const [page, setPage] = useState<Page>("Ai Chat");

  return (
    <Container direction='row' style={{ minHeight: "100vh" }}>
      <Container direction='column' size={1} padding={24}>
        {page === "Ai Chat" && <AiChat />}
      </Container>
    </Container>
  );
}
