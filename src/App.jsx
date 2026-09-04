import React from "react";
import Toast from "./components/Toast";
import SidebarNav from "./components/SidebarNav";
import TopBar from "./components/TopBar";
import Overview from "./components/Overview";
import StatusPanel from "./components/StatusPanel";
import ConnectionPanel from "./components/ConnectionPanel";
import ControlTab from "./components/ControlTab";
import UsersTab from "./components/UsersTab";
import LogsTab from "./components/LogsTab";
import SettingsTab from "./components/SettingsTab";
import PairingScreen from "./components/PairingScreen";
import Badge from "./components/Badge";
import useAppState from "./hooks/useAppState";
import useAppActions from "./hooks/useAppActions";

export default function App() {
  const state = useAppState();
  const actions = useAppActions(state);
  const mainRef = React.useRef(null);

  const locked = state.mode === "sim" ? state.sim.locked : state.status.lockState === "locked";
  const ignitionOn = state.mode === "sim" ? !!state.sim.ignitionOn : !!state.status.ignitionOn;
  const online = state.status.online;

  React.useEffect(() => {
    if (mainRef.current) mainRef.current.scrollTop = 0;
  }, [state.tab]);

  // Ask the Pi who this browser is whenever the connection target changes.
  React.useEffect(() => {
    actions.loadMe();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state.mode, state.baseUrl]);

  // Admin-only tabs are hidden for ordinary drivers. This is a convenience so
  // the UI does not offer actions that will fail -- it is NOT the security
  // boundary. Every one of these endpoints is enforced server-side, and a
  // driver who navigates here by hand still gets 403 from the Pi.
  const isAdmin = state.mode !== "device" || state.isAdmin;

  React.useEffect(() => {
    if (!isAdmin && ["users", "logs", "settings"].includes(state.tab)) {
      state.setTab("control");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isAdmin, state.tab]);

  if (state.mode === "device" && state.authState === "out") {
    return (
      <div className="app-shell flex flex-col overflow-y-auto text-slate-100">
        <Toast toast={state.toast} />
        <PairingScreen onPair={actions.pairDevice} busy={state.busy} />
      </div>
    );
  }

  return (
    <div className="app-shell flex overflow-hidden text-slate-100">
      <Toast toast={state.toast} />
      <SidebarNav tab={state.tab} setTab={state.setTab} isAdmin={isAdmin} />

      <div className="flex min-w-0 flex-1 flex-col">
        <TopBar
          tab={state.tab}
          mode={state.mode}
          locked={locked}
          ignitionOn={ignitionOn}
          online={online}
          busy={state.busy}
          onRefresh={actions.refresh}
        />

        <main ref={mainRef} className="app-content flex-1 overflow-y-auto overscroll-y-contain px-4 pt-4 sm:px-5 sm:pt-6 md:px-8 md:py-6">
          {state.tab === "control" && (
            <div className="mx-auto w-full max-w-6xl space-y-6">
              <ControlTab
                api={state.api}
                mode={state.mode}
                baseUrl={state.baseUrl}
                faceApiUrl={state.faceApiUrl}
                faceAccessAllowed={state.faceAccessAllowed}
                locked={locked}
                ignitionOn={ignitionOn}
                promptAutoLockSeconds={
                  typeof state.settings?.promptAutoLockSeconds === "number"
                    ? state.settings.promptAutoLockSeconds
                    : 0
                }
                doUnlock={actions.doUnlock}
                doLock={actions.doLock}
                doIgnitionStop={actions.doIgnitionStop}
                doFullReset={actions.doFullReset}
                popToast={state.popToast}
                busy={state.busy}
                onRefresh={actions.refresh}
              />
              <div id="panel-status" className="scroll-mt-6 space-y-4">
                <Overview mode={state.mode} sim={state.sim} status={state.status} />
                <StatusPanel
                  locked={locked}
                  busy={state.busy}
                  doUnlock={actions.doUnlock}
                  doLock={actions.doLock}
                  mode={state.mode}
                  sim={state.sim}
                  status={state.status}
                />
                <ConnectionPanel
                  mode={state.mode}
                  setMode={state.setMode}
                  baseUrl={state.baseUrl}
                  setBaseUrl={state.setBaseUrl}
                  me={state.me}
                  onSignOut={actions.signOut}
                  faceApiUrl={state.faceApiUrl}
                  setFaceApiUrl={state.setFaceApiUrl}
                />
              </div>
            </div>
          )}

          {state.tab === "users" && isAdmin && (
            <UsersTab
              mode={state.mode}
              sim={state.sim}
              setSim={state.setSim}
              deviceUsers={state.deviceUsers}
              name={state.name}
              setName={state.setName}
              busy={state.busy}
              addUserToDirectory={actions.addUserToDirectory}
              delUser={actions.delUser}
              faceApiUrl={state.faceApiUrl}
              popToast={state.popToast}
              faceAccessAllowed={state.faceAccessAllowed}
              setFaceAccessAllowed={state.setFaceAccessAllowed}
              setDeviceUsers={state.setDeviceUsers}
              api={state.api}
            />
          )}

          {state.tab === "logs" && isAdmin && (
            <LogsTab
              mode={state.mode}
              sim={state.sim}
              deviceLogs={state.deviceLogs}
              clearLogs={() => state.setSim((s) => ({ ...s, logs: [] }))}
            />
          )}

          {state.tab === "settings" && isAdmin && (
            <SettingsTab
              settings={state.settings}
              setSettings={state.setSettings}
              busy={state.busy}
              saveSettings={actions.saveSettings}
            />
          )}

          <footer className="mt-10 hidden flex-wrap items-center justify-center gap-x-3 gap-y-2 text-center text-xs text-slate-500 sm:flex">
            <Badge>v1 UI</Badge>
            <span className="text-slate-600">•</span>
            <span>DNA Builder–inspired layout</span>
            <span className="text-slate-600">•</span>
            <span>Local-first (LAN)</span>
            <span className="text-slate-600">•</span>
            <a
              href="https://github.com/SJSU-CMPE-195/group-project-team-face-id"
              target="_blank"
              rel="noopener noreferrer"
              className="text-slate-500 transition hover:text-violet-400 hover:underline"
            >
              Open on GitHub
            </a>
          </footer>
        </main>
      </div>
    </div>
  );
}
