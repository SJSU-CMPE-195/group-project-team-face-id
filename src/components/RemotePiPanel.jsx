import React from "react";
import Btn from "./Btn.jsx";
import Card from "./Card.jsx";

function accountRole(user) {
  if (user.is_owner) return "Owner";
  if (user.is_admin) return "Administrator";
  return "Member";
}

export default function RemotePiPanel({ user, setTab }) {
  const canManageUsers = user.is_admin || user.is_owner;

  return (
    <Card>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="text-sm font-semibold text-slate-100">
            Pi dashboard
          </div>
          <p className="mt-1 text-xs text-slate-500">
            Signed in as {user.name}
          </p>
        </div>
        <span className="rounded-full border border-violet-400/20 bg-violet-400/10 px-2.5 py-1 text-xs font-medium text-violet-200">
          {accountRole(user)}
        </span>
      </div>
      <p className="mt-3 text-sm leading-relaxed text-slate-400">
        {canManageUsers
          ? "Manage users and enroll faces using the Pi camera."
          : "View your account and activity logs. An administrator manages users and face enrollment."}
      </p>
      <div className="mt-4 flex flex-col gap-2 sm:flex-row">
        <Btn className="flex-1" onClick={() => setTab("users")}>
          {canManageUsers ? "Users & face enrollment" : "Your account"}
        </Btn>
        <Btn
          variant="secondary"
          className="flex-1"
          onClick={() => setTab("logs")}
        >
          View logs
        </Btn>
      </div>
    </Card>
  );
}
