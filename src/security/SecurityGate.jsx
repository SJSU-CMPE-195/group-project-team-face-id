import React, { useEffect, useRef, useState } from "react";
import { ShieldCheck } from "lucide-react";
import App from "../App.jsx";
import Btn from "../components/Btn.jsx";
import Card from "../components/Card.jsx";
import Input from "../components/Input.jsx";
import SidebarNav from "../components/SidebarNav.jsx";
import TopBar from "../components/TopBar.jsx";
import HardwareSimulatorPanel from "../hardware/HardwareSimulatorPanel.jsx";
import useHardwareSimulator from "../hardware/useHardwareSimulator.js";
import OperationGrantProvider from "./OperationGrantProvider.jsx";
import useSecurity from "./useSecurity.js";

const PIN_PATTERN = /^\d{6}$/;

function rememberedAccountKey(account) {
  return account
    ? JSON.stringify([account.device_id, account.generation, account.user.id])
    : "";
}

export default function SecurityGate() {
  const security = useSecurity();
  const simulator = useHardwareSimulator();
  const [anonymousTab, setAnonymousTab] = useState("control");
  const [name, setName] = useState("");
  const [pin, setPin] = useState("");
  const [formError, setFormError] = useState("");
  const [busy, setBusy] = useState(false);
  const submitPendingRef = useRef(false);
  const accountKey = rememberedAccountKey(security.rememberedAccount);
  const previousAccountKeyRef = useRef(accountKey);
  const previousSessionEpochRef = useRef(security.sessionEpoch);
  const securityPhase = security.phase;
  const refreshAuthentication = security.refreshAuthentication;

  useEffect(() => {
    const accountChanged = previousAccountKeyRef.current !== accountKey;
    const sessionChanged =
      previousSessionEpochRef.current !== security.sessionEpoch;
    if (accountChanged || sessionChanged) {
      setPin("");
      setFormError("");
    }
    if (previousAccountKeyRef.current && !accountKey) {
      setName("");
    }
    previousAccountKeyRef.current = accountKey;
    previousSessionEpochRef.current = security.sessionEpoch;
  }, [accountKey, security.sessionEpoch]);

  useEffect(() => {
    if (securityPhase !== "setup") return undefined;
    const timer = window.setInterval(() => {
      void refreshAuthentication().catch(() => {});
    }, 2500);
    return () => window.clearInterval(timer);
  }, [refreshAuthentication, securityPhase]);

  if (security.phase === "authenticated") {
    return (
      <OperationGrantProvider>
        <div
          className="contents"
          inert={security.loggingOut || !!security.logoutError || undefined}
        >
          <App
            key={security.sessionEpoch}
            onHardwareReset={() => setAnonymousTab("hardware")}
          />
        </div>
        <RememberWarning message={security.rememberWarning} />
        <LogoutOverlay security={security} />
      </OperationGrantProvider>
    );
  }

  async function submit(event) {
    event.preventDefault();
    if (submitPendingRef.current) return;
    const displayName = name.trim();
    if (!security.rememberedAccount && !displayName) {
      setFormError("Enter your name.");
      return;
    }
    if (!PIN_PATTERN.test(pin)) {
      setFormError("PIN must be exactly 6 ASCII digits.");
      return;
    }
    submitPendingRef.current = true;
    setBusy(true);
    setFormError("");
    try {
      await security.login(displayName, pin);
      setPin("");
    } catch (error) {
      setPin("");
      setFormError(error.message);
    } finally {
      submitPendingRef.current = false;
      setBusy(false);
    }
  }

  async function useAnotherAccount() {
    if (submitPendingRef.current) return;
    submitPendingRef.current = true;
    setBusy(true);
    setFormError("");
    try {
      const changed = await security.useAnotherAccount();
      if (changed) {
        setName("");
        setPin("");
      }
    } catch (error) {
      setFormError(error.message);
    } finally {
      submitPendingRef.current = false;
      setBusy(false);
    }
  }

  const activeTab =
    anonymousTab === "hardware" && !simulator.enabled
      ? "control"
      : anonymousTab;

  return (
    <div className="app-shell flex overflow-hidden text-slate-100">
      <SidebarNav
        tab={activeTab}
        setTab={setAnonymousTab}
        isAdmin={false}
        hardwareEnabled={simulator.enabled}
      />
      <div className="flex min-w-0 flex-1 flex-col">
        <TopBar
          tab={activeTab}
          locked
          ignitionOn={false}
          online={false}
          simulatedActuators={false}
          actuatorControlAvailable={false}
          physicalStateConfirmed={false}
          busy={busy || security.phase === "loading"}
          onRefresh={
            activeTab === "hardware"
              ? () => void simulator.refresh().catch(() => {})
              : () => void security.refreshAuthentication().catch(() => {})
          }
          user={null}
        />
        <main className="app-content flex-1 overflow-y-auto overscroll-y-contain px-4 pt-4 sm:px-5 sm:pt-6 md:px-8 md:py-6">
          {activeTab === "hardware" ? (
            <HardwareSimulatorPanel embedded />
          ) : (
            <div className="flex min-h-full items-center justify-center py-4">
              <AuthenticationCard
                security={security}
                simulator={simulator}
                name={name}
                setName={setName}
                pin={pin}
                setPin={setPin}
                formError={formError}
                busy={busy}
                onSubmit={submit}
                onUseAnotherAccount={useAnotherAccount}
                onOpenHardware={() => setAnonymousTab("hardware")}
              />
            </div>
          )}
        </main>
      </div>
    </div>
  );
}

function RememberWarning({ message }) {
  if (!message) return null;
  return (
    <div
      className="fixed bottom-4 right-4 z-[70] max-w-sm rounded-xl border border-amber-400/25 bg-slate-950/95 p-3 text-sm text-amber-100 shadow-xl"
      role="status"
    >
      {message}
    </div>
  );
}

function AuthenticationCard({
  security,
  simulator,
  name,
  setName,
  pin,
  setPin,
  formError,
  busy,
  onSubmit,
  onUseAnotherAccount,
  onOpenHardware,
}) {
  return (
    <div className="w-full max-w-md">
      <Card>
        <div className="flex items-center gap-3">
          <div className="flex h-11 w-11 items-center justify-center rounded-xl bg-gradient-to-br from-violet-600 to-fuchsia-600">
            <ShieldCheck className="h-5 w-5 text-white" />
          </div>
          <div>
            <h1 className="text-lg font-semibold">BASS host dashboard</h1>
            <p className="text-sm text-slate-400">
              {security.phase === "setup"
                ? "Activate the first owner from the phone."
                : "Sign in on this host."}
            </p>
          </div>
        </div>

        {security.phase === "loading" ? (
          <div className="mt-6 text-sm text-slate-400" role="status">
            Checking host security…
          </div>
        ) : security.phase === "error" ? (
          <div
            className="mt-6 rounded-xl border border-rose-400/20 bg-rose-400/10 p-4 text-sm text-rose-200"
            role="alert"
          >
            {security.error || "Could not reach the local security service."}
          </div>
        ) : security.phase === "setup" ? (
          <FirstOwnerSetup
            hardwareAvailable={simulator.enabled}
            onOpenHardware={onOpenHardware}
            onRefresh={() =>
              void security.refreshAuthentication().catch(() => {})
            }
          />
        ) : (
          <form className="mt-6 space-y-4" onSubmit={onSubmit}>
            {security.rememberWarning ? (
              <div
                className="rounded-xl border border-amber-400/20 bg-amber-400/10 p-3 text-sm text-amber-100"
                role="status"
              >
                {security.rememberWarning}
              </div>
            ) : null}
            {security.rememberedAccount ? (
              <div className="rounded-xl border border-white/10 bg-white/5 p-3">
                <div className="text-xs text-slate-400">Account</div>
                <div className="mt-1 font-medium text-slate-100">
                  {security.rememberedAccount.user.name}
                </div>
                <button
                  type="button"
                  className="mt-2 text-sm text-violet-300 hover:text-violet-200 disabled:opacity-50"
                  disabled={busy}
                  onClick={onUseAnotherAccount}
                >
                  Use another account
                </button>
              </div>
            ) : (
              <div>
                <label
                  htmlFor="security-name"
                  className="mb-1 block text-sm font-medium"
                >
                  Name
                </label>
                <Input
                  id="security-name"
                  autoComplete="username"
                  autoFocus
                  value={name}
                  onChange={(event) => setName(event.target.value)}
                  disabled={busy}
                />
              </div>
            )}
            <div>
              <label
                htmlFor="security-pin"
                className="mb-1 block text-sm font-medium"
              >
                6-digit PIN
              </label>
              <Input
                id="security-pin"
                type="password"
                autoComplete="current-password"
                maxLength={6}
                inputMode="numeric"
                pattern="[0-9]{6}"
                autoFocus={Boolean(security.rememberedAccount)}
                value={pin}
                onChange={(event) => setPin(event.target.value)}
                disabled={busy}
              />
            </div>
            {formError ? (
              <div className="text-sm text-rose-300" role="alert">
                {formError}
              </div>
            ) : null}
            <Btn type="submit" disabled={busy} className="w-full">
              {busy ? "Please wait…" : "Sign in"}
            </Btn>
          </form>
        )}
      </Card>
    </div>
  );
}

function FirstOwnerSetup({ hardwareAvailable, onOpenHardware, onRefresh }) {
  const instructions = hardwareAvailable
    ? "Open Hardware Simulator, reveal the activation card, hold the multifunction button for 3 seconds, then scan and choose the owner PIN on the phone."
    : "Use the activation card supplied with the device, hold its physical multifunction button for 3 seconds, then scan and choose the owner PIN on the phone.";

  return (
    <div className="mt-6 space-y-4">
      <div className="rounded-xl border border-sky-400/20 bg-sky-400/10 p-4 text-sm leading-relaxed text-sky-100">
        This device has no owner. {instructions}
      </div>
      <div className="flex flex-col gap-2 sm:flex-row">
        {hardwareAvailable ? (
          <Btn className="flex-1" onClick={onOpenHardware}>
            Open Hardware Simulator
          </Btn>
        ) : null}
        <Btn variant="secondary" className="flex-1" onClick={onRefresh}>
          Check activation
        </Btn>
      </div>
      <p className="text-xs leading-relaxed text-slate-500">
        This page updates automatically after the phone completes ownership.
        There is no local first-administrator shortcut.
      </p>
    </div>
  );
}

function LogoutOverlay({ security }) {
  if (!security.loggingOut && !security.logoutError) return null;
  return (
    <div className="fixed inset-0 z-[90] flex items-center justify-center bg-black/75 px-4 backdrop-blur-sm">
      <div className="w-full max-w-sm rounded-2xl border border-white/10 bg-dna-surface p-5 shadow-2xl">
        {security.loggingOut ? (
          <div role="status" className="text-sm text-slate-200">
            Confirming sign-out with the host…
          </div>
        ) : (
          <>
            <div role="alert" className="text-sm text-rose-200">
              {security.logoutError}
            </div>
            <p className="mt-2 text-xs leading-relaxed text-slate-400">
              This browser may still have an active host session.
            </p>
            <div className="mt-5 flex justify-end gap-2">
              <Btn variant="secondary" onClick={security.dismissLogoutError}>
                Return to dashboard
              </Btn>
              <Btn onClick={() => void security.logout()}>
                Retry sign-out
              </Btn>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
