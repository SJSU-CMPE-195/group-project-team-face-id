import { useContext } from "react";
import { PcClientContext } from "./pcClientContext.js";

export default function usePcClient() {
  return useContext(PcClientContext);
}
