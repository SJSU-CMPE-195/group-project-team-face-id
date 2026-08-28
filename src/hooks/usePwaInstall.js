import { useCallback, useEffect, useState } from "react";

function isStandalone() {
  if (typeof window === "undefined") return false;
  return (
    window.matchMedia("(display-mode: standalone)").matches ||
    window.navigator.standalone === true
  );
}

export default function usePwaInstall() {
  const [installPrompt, setInstallPrompt] = useState(null);
  const [installed, setInstalled] = useState(isStandalone);
  const [outcome, setOutcome] = useState(null);

  useEffect(() => {
    const displayMode = window.matchMedia("(display-mode: standalone)");
    const updateInstalled = () => setInstalled(isStandalone());
    const handleBeforeInstall = (event) => {
      event.preventDefault();
      setInstallPrompt(event);
      setOutcome(null);
    };
    const handleInstalled = () => {
      setInstalled(true);
      setInstallPrompt(null);
      setOutcome("accepted");
    };

    window.addEventListener("beforeinstallprompt", handleBeforeInstall);
    window.addEventListener("appinstalled", handleInstalled);
    displayMode.addEventListener?.("change", updateInstalled);

    return () => {
      window.removeEventListener("beforeinstallprompt", handleBeforeInstall);
      window.removeEventListener("appinstalled", handleInstalled);
      displayMode.removeEventListener?.("change", updateInstalled);
    };
  }, []);

  const install = useCallback(async () => {
    if (!installPrompt) return "unavailable";
    try {
      await installPrompt.prompt();
      const choice = await installPrompt.userChoice;
      setOutcome(choice.outcome);
      setInstallPrompt(null);
      return choice.outcome;
    } catch {
      setOutcome("error");
      setInstallPrompt(null);
      return "error";
    }
  }, [installPrompt]);

  return {
    canInstall: Boolean(installPrompt) && !installed,
    install,
    installed,
    outcome,
    secureContext: typeof window !== "undefined" && window.isSecureContext,
  };
}
