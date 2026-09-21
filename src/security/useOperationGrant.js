import { useContext } from "react";
import { OperationGrantContext } from "./operationGrantContext.js";

export default function useOperationGrant() {
  const grants = useContext(OperationGrantContext);
  if (!grants) throw new Error("Operation grant provider is missing.");
  return grants;
}
