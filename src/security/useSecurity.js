import { useContext } from "react";
import { SecurityContext } from "./securityContext.js";

export default function useSecurity() {
  const security = useContext(SecurityContext);
  if (!security) throw new Error("Security provider is missing.");
  return security;
}
