import React from "react";
import TabBtn from "./TabBtn";

export default function Tabs({ tab, setTab }) {
  return (
    <div className="mt-7 flex flex-wrap gap-2">
      <TabBtn active={tab === "control"} onClick={() => setTab("control")}>Control</TabBtn>
      <TabBtn active={tab === "users"} onClick={() => setTab("users")}>Users</TabBtn>
      <TabBtn active={tab === "logs"} onClick={() => setTab("logs")}>Logs</TabBtn>
      <TabBtn active={tab === "settings"} onClick={() => setTab("settings")}>Settings</TabBtn>
    </div>
  );
}
