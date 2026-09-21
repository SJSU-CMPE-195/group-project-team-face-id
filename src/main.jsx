import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./index.css";
import ThemeProvider from "./context/ThemeProvider.jsx";
import AuthProvider from "./security/AuthProvider.jsx";
import SecurityGate from "./security/SecurityGate.jsx";
import HardwareSimulatorProvider from "./hardware/HardwareSimulatorProvider.jsx";

if (import.meta.env.PROD && "serviceWorker" in navigator) {
  window.addEventListener(
    "load",
    () => {
      navigator.serviceWorker
        .register("/sw.js", { scope: "/", updateViaCache: "none" })
        .catch((error) => console.warn("Service worker registration failed", error));
    },
    { once: true },
  );
}

createRoot(document.getElementById("root")).render(
  <StrictMode>
    <ThemeProvider>
      <HardwareSimulatorProvider>
        <AuthProvider>
          <SecurityGate />
        </AuthProvider>
      </HardwareSimulatorProvider>
    </ThemeProvider>
  </StrictMode>
);
