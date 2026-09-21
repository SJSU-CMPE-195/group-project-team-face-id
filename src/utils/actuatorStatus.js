function runtimeFlag(runtime, capabilities, key) {
  if (typeof runtime?.[key] === "boolean") return runtime[key];
  return capabilities?.[key] === true;
}

export function resolveRuntimeCapabilities({
  mode,
  runtime = {},
  capabilities = {},
}) {
  const localSimulation = mode === "sim";

  return {
    simulatedActuators:
      localSimulation ||
      runtimeFlag(runtime, capabilities, "simulated_actuators"),
    actuatorControlAvailable:
      localSimulation ||
      runtimeFlag(runtime, capabilities, "actuator_control_available"),
    physicalStateConfirmed: runtimeFlag(
      runtime,
      capabilities,
      "physical_state_confirmed",
    ),
    livenessAvailable: runtimeFlag(
      runtime,
      capabilities,
      "liveness_available",
    ),
  };
}

export function actuatorLabels({
  online,
  locked,
  ignitionOn,
  simulatedActuators,
  actuatorControlAvailable,
  physicalStateConfirmed,
}) {
  if (!online) {
    return {
      control: "Unavailable",
      ignitionLong: "Ignition state unavailable",
      ignitionShort: "Unavailable",
      lockLong: "State unavailable",
      lockPanel: "Device state unavailable",
      lockShort: "Unavailable",
    };
  }

  const control = actuatorControlAvailable
    ? simulatedActuators
      ? "Simulation"
      : "Available"
    : "Outputs blocked";

  if (simulatedActuators) {
    return {
      control,
      ignitionLong: ignitionOn
        ? "Ignition on (simulated)"
        : "Ignition off (simulated)",
      ignitionShort: ignitionOn ? "On (sim)" : "Off (sim)",
      lockLong: locked ? "Locked (simulated)" : "Unlocked (simulated)",
      lockPanel: locked ? "Locked (simulated)" : "Unlocked (simulated)",
      lockShort: locked ? "Locked (sim)" : "Unlocked (sim)",
    };
  }

  if (physicalStateConfirmed) {
    return {
      control,
      ignitionLong: ignitionOn ? "Ignition on" : "Ignition off",
      ignitionShort: ignitionOn ? "On" : "Off",
      lockLong: locked ? "Locked" : "Unlocked",
      lockPanel: locked ? "Locked" : "Unlocked",
      lockShort: locked ? "Locked" : "Unlocked",
    };
  }

  return {
    control,
    ignitionLong: "Ignition state unconfirmed",
    ignitionShort: "Unconfirmed",
    lockLong: "Physical state unconfirmed",
    lockPanel: "Physical state unconfirmed",
    lockShort: "Unconfirmed",
  };
}
