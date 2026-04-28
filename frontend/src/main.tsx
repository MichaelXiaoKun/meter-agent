import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./index.css";
import App from "./App";
import SharedChatView from "./components/SharedChatView";
import SalesChatPage from "./components/SalesChatPage";
import { ThemeProvider } from "./context/ThemeProvider";

const hash = typeof window !== "undefined" ? window.location.hash : "";
const shareMatch = hash.match(/^#\/share\/([a-f0-9]{8,64})$/i);
const shareToken = shareMatch ? shareMatch[1] : null;
const isSalesPath =
  typeof window !== "undefined" &&
  (window.location.pathname === "/sales" || window.location.hash === "#/sales");

const el = document.getElementById("root")!;
if (shareToken) {
  createRoot(el).render(
    <StrictMode>
      <ThemeProvider>
        <SharedChatView token={shareToken} />
      </ThemeProvider>
    </StrictMode>,
  );
} else if (isSalesPath) {
  createRoot(el).render(
    <StrictMode>
      <ThemeProvider>
        <SalesChatPage />
      </ThemeProvider>
    </StrictMode>,
  );
} else {
  createRoot(el).render(
    <StrictMode>
      <ThemeProvider>
        <App />
      </ThemeProvider>
    </StrictMode>,
  );
}
