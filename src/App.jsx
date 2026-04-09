import React from "react";
import Toast from "./components/Toast";
import Header from "./components/Header";
import Overview from "./components/Overview";
import StatusPanel from "./components/StatusPanel";
import ConnectionPanel from "./components/ConnectionPanel";
import Tabs from "./components/Tabs";
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

  const locked = state.mode === "sim" ? state.sim.locked : state.status.lockState === "locked";

  return (
    <div className="min-h-screen bg-neutral-50 text-neutral-900">
      <Toast toast={state.toast} />

      <div className="mx-auto max-w-5xl px-4 py-6 md:px-8">
        <Header busy={state.busy} onRefresh={actions.refresh} />
        <Overview mode={state.mode} sim={state.sim} status={state.status} />

        <div className="mt-3 grid grid-cols-1 gap-3 md:grid-cols-3">
          <div className="md:col-span-2">
            <StatusPanel
              locked={locked}
              busy={state.busy}
              doUnlock={actions.doUnlock}
              doLockSim={actions.doLockSim}
              mode={state.mode}
              sim={state.sim}
              status={state.status}
            />
          </div>

          <ConnectionPanel
            mode={state.mode}
            setMode={state.setMode}
            baseUrl={state.baseUrl}
            setBaseUrl={state.setBaseUrl}
          />
        </div>

        <Tabs tab={state.tab} setTab={state.setTab} />

        <div className="mt-4">
          {state.tab === "control" && <ControlTab busy={state.busy} doUnlock={actions.doUnlock} setTab={state.setTab} />}
          {state.tab === "users" && (
            <UsersTab
              mode={state.mode}
              sim={state.sim}
              deviceUsers={state.deviceUsers}
              name={state.name}
              setName={state.setName}
              busy={state.busy}
              addUser={actions.addUser}
              delUser={actions.delUser}
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
        </div>

        <div className="mt-10 text-xs text-neutral-500">
          <Badge>v1 UI</Badge> <span className="mx-2">•</span> Local-first (LAN) <span className="mx-2">•</span> Demo-ready
        </div>
      </div>
    </div>
  );
}