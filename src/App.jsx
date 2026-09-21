import React from "react";
import Toast from "./components/Toast";
import SidebarNav from "./components/SidebarNav";
import TopBar from "./components/TopBar";
import Overview from "./components/Overview";
import StatusPanel from "./components/StatusPanel";
import ControlTab from "./components/ControlTab";
import UsersTab from "./components/UsersTab";
import LogsTab from "./components/LogsTab";
import SettingsTab from "./components/SettingsTab";
import useAppState from "./hooks/useAppState";
import useAppActions from "./hooks/useAppActions";
import useSecurity from "./security/useSecurity.js";
import HardwareSimulatorPanel from "./hardware/HardwareSimulatorPanel.jsx";
import useHardwareSimulator from "./hardware/useHardwareSimulator.js";
import { resolveRuntimeCapabilities } from "./utils/actuatorStatus.js";

export default function App({ onHardwareReset }) {
  const { user, logout } = useSecurity();
  const hardware = useHardwareSimulator();
  const state = useAppState();
  const actions = useAppActions(state);
  const mainRef = React.useRef(null);

  const runtime = state.status.runtime || {};
  const capabilities = state.status.capabilities || {};
  const locked =
    state.mode === "sim"
      ? state.sim.locked
      : state.status.lockState === "locked";
  const ignitionOn =
    state.mode === "sim" ? !!state.sim.ignitionOn : !!state.status.ignitionOn;
  const online = state.status.online;
  const {
    simulatedActuators,
    actuatorControlAvailable,
    physicalStateConfirmed,
    livenessAvailable,
  } = resolveRuntimeCapabilities({
    mode: state.mode,
    runtime,
    capabilities,
  });

  React.useEffect(() => {
    if (mainRef.current) mainRef.current.scrollTop = 0;
  }, [state.tab]);

  return (
    <div className="app-shell flex overflow-hidden text-slate-100">
      <Toast toast={state.toast} />
      <SidebarNav
        tab={state.tab}
        setTab={state.setTab}
        isAdmin={user.is_admin}
        hardwareEnabled={hardware.enabled}
      />

      <div className="flex min-w-0 flex-1 flex-col">
        <TopBar
          tab={state.tab}
          locked={locked}
          ignitionOn={ignitionOn}
          online={online}
          simulatedActuators={simulatedActuators}
          actuatorControlAvailable={actuatorControlAvailable}
          physicalStateConfirmed={physicalStateConfirmed}
          busy={state.busy}
          onRefresh={
            state.tab === "hardware"
              ? () => void hardware.refresh().catch(() => {})
              : actions.refresh
          }
          user={user}
          onLogout={logout}
        />

        <main ref={mainRef} className="app-content flex-1 overflow-y-auto overscroll-y-contain px-4 pt-4 sm:px-5 sm:pt-6 md:px-8 md:py-6">
          {state.tab === "control" && (
            <div className="mx-auto w-full max-w-6xl space-y-6">
              <ControlTab
                api={state.api}
                currentUser={user}
                cameraSource={
                  state.status.runtime?.camera_source ||
                  state.status.camera_source ||
                  state.status.cameraSource
                }
                cameraAvailable={
                  state.status.capabilities?.device_camera !== false
                }
                online={state.status.online}
                locked={locked}
                ignitionOn={ignitionOn}
                simulatedActuators={simulatedActuators}
                actuatorControlAvailable={actuatorControlAvailable}
                physicalStateConfirmed={physicalStateConfirmed}
                promptAutoLockSeconds={
                  typeof state.settings?.promptAutoLockSeconds === "number"
                    ? state.settings.promptAutoLockSeconds
                    : 0
                }
                doLock={actions.doLock}
                doIgnitionStop={actions.doIgnitionStop}
                doFullReset={actions.doFullReset}
                popToast={state.popToast}
                busy={state.busy}
                onRefresh={actions.refresh}
              />
              <div id="panel-status" className="scroll-mt-6 space-y-4">
                <Overview status={state.status} />
                <StatusPanel
                  locked={locked}
                  busy={state.busy}
                  doLock={actions.doLock}
                  status={state.status}
                  simulatedActuators={simulatedActuators}
                  actuatorControlAvailable={actuatorControlAvailable}
                  physicalStateConfirmed={physicalStateConfirmed}
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
              currentUser={user}
              devices={state.devices}
              setDevices={state.setDevices}
            />
          )}

          {state.tab === "logs" && user.is_admin && (
            <LogsTab
              deviceLogs={state.deviceLogs}
            />
          )}

          {state.tab === "settings" && (
            <SettingsTab
              settings={state.settings}
              setSettings={state.setSettings}
              busy={state.busy}
              saveSettings={actions.saveSettings}
              isAdmin={user.is_admin}
              livenessAvailable={livenessAvailable}
            />
          )}

          {state.tab === "hardware" && hardware.enabled && (
            <HardwareSimulatorPanel
              embedded
              onReset={onHardwareReset}
            />
          )}

        </main>
      </div>
    </div>
  );
}
