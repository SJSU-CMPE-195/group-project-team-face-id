import { useContext } from "react";
import { HardwareSimulatorContext } from "./hardwareSimulatorContext.js";

export default function useHardwareSimulator() {
  const simulator = useContext(HardwareSimulatorContext);
  if (!simulator) throw new Error("Hardware simulator provider is missing.");
  return simulator;
}
