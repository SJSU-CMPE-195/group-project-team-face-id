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

function cameraLabel(status) {
  const source = String(
    status.runtime?.camera_source ||
      status.camera_source ||
      status.cameraSource ||
      status.capabilities?.camera_source ||
      "",
  ).toLowerCase();
  const mode = String(status.runtime?.mode || "").toLowerCase();
  if (mode.includes("pi") || source.includes("pi")) return "Pi camera";
  if (source.includes("webcam") || source.includes("pc")) {
    return "PC development camera";
  }
  return source ? source.replaceAll("_", " ") : "Host camera";
}

export default function App({ onHardwareReset }) {
  const { user, logout } = useSecurity();
  const hardware = useHardwareSimulator();
  const state = useAppState();
  const actions = useAppActions(state);
  const mainRef = React.useRef(null);
  const { setTab, tab } = state;

  const runtime = state.status.runtime || {};
  const capabilities = state.status.capabilities || {};
  const localControls = capabilities.local_controls === true;
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
  const localActuatorControlAvailable =
    localControls && actuatorControlAvailable;

  React.useEffect(() => {
    if (!localControls && ["settings", "hardware"].includes(tab)) {
      setTab("control");
    }
  }, [localControls, setTab, tab]);

  React.useEffect(() => {
    if (mainRef.current) mainRef.current.scrollTop = 0;
  }, [state.tab]);

  return (
    <div className="app-shell flex overflow-hidden text-slate-100">
      <Toast toast={state.toast} />
      <SidebarNav
        tab={state.tab}
        setTab={state.setTab}
        hardwareEnabled={hardware.enabled}
        localControls={localControls}
      />

      <div className="flex min-w-0 flex-1 flex-col">
        <TopBar
          tab={state.tab}
          locked={locked}
          ignitionOn={ignitionOn}
          online={online}
          simulatedActuators={simulatedActuators}
          actuatorControlAvailable={localActuatorControlAvailable}
          physicalStateConfirmed={physicalStateConfirmed}
          busy={state.busy}
          onRefresh={
            state.tab === "hardware"
              ? () => void hardware.refresh().catch(() => {})
              : actions.refresh
          }
          user={user}
          onLogout={logout}
          localControls={localControls}
        />

        <main ref={mainRef} className="app-content flex-1 overflow-y-auto overscroll-y-contain px-4 pt-4 sm:px-5 sm:pt-6 md:px-8 md:py-6">
          {state.tab === "control" && (
            <div className="mx-auto w-full max-w-6xl space-y-6">
              {localControls ? <ControlTab
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
                actuatorControlAvailable={localActuatorControlAvailable}
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
              /> : null}
              <div id="panel-status" className="scroll-mt-6 space-y-4">
                <Overview status={state.status} />
                <StatusPanel
                  locked={locked}
                  busy={state.busy}
                  doLock={actions.doLock}
                  status={state.status}
                  simulatedActuators={simulatedActuators}
                  actuatorControlAvailable={localActuatorControlAvailable}
                  physicalStateConfirmed={physicalStateConfirmed}
                />
              </div>
            </div>
          )}

          {state.tab === "users" && (
            <UsersTab
              deviceUsers={state.deviceUsers}
              name={state.name}
              setName={state.setName}
              busy={state.busy}
              addUserToDirectory={actions.addUserToDirectory}
              delUser={actions.delUser}
              popToast={state.popToast}
              setDeviceUsers={state.setDeviceUsers}
              api={state.api}
              currentUser={user}
              devices={state.devices}
              setDevices={state.setDevices}
              cameraLabel={cameraLabel(state.status)}
            />
          )}

          {state.tab === "logs" && (
            <LogsTab
              deviceLogs={state.deviceLogs}
            />
          )}

          {state.tab === "settings" && localControls && (
            <SettingsTab
              settings={state.settings}
              setSettings={state.setSettings}
              busy={state.busy}
              saveSettings={actions.saveSettings}
              isAdmin={user.is_admin}
              livenessAvailable={livenessAvailable}
            />
          )}

          {state.tab === "hardware" && hardware.enabled && localControls && (
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
