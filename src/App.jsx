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

  return (
    <div className="app-shell flex overflow-hidden text-slate-100">
      <Toast toast={state.toast} />
      <SidebarNav tab={state.tab} setTab={state.setTab} />

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
                  faceApiUrl={state.faceApiUrl}
                  setFaceApiUrl={state.setFaceApiUrl}
                />
              </div>
            </div>
          )}

          {state.tab === "users" && (
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

          {state.tab === "logs" && (
            <LogsTab
              mode={state.mode}
              sim={state.sim}
              deviceLogs={state.deviceLogs}
              clearLogs={() => state.setSim((s) => ({ ...s, logs: [] }))}
            />
          )}

          {state.tab === "settings" && (
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
