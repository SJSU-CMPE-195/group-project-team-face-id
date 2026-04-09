import React from "react";
import Card from "./Card";
import Badge from "./Badge";
import Input from "./Input";
import Btn from "./Btn";
import { fmt } from "../utils/helpers";

export default function UsersTab({ mode, sim, deviceUsers, name, setName, busy, addUser, delUser }) {
  const users = mode === "device" ? deviceUsers : sim.users;

  return (
    <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
      <div className="md:col-span-2">
        <Card>
          <div className="flex items-center justify-between gap-2">
            <div>
              <div className="text-sm font-semibold text-slate-100">Enrolled users</div>
              <div className="mt-1 text-xs text-slate-400">
                {mode === "device"
                  ? "Names on Pi SQLite; face templates from your enrollment pipeline."
                  : "Simulated templates for the demo."}
              </div>
            </div>
            <Badge>{users.length} users</Badge>
          </div>

          <div className="mt-4 flex gap-2">
            <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="Display name" />
            <Btn disabled={busy} onClick={addUser}>
              Add
            </Btn>
          </div>

          <div className="mt-4 space-y-2">
            {users.length === 0 ? (
              <div className="text-sm text-slate-500">No users yet.</div>
            ) : (
              users.map((u) => (
                <div
                  key={u.id}
                  className="flex items-center justify-between gap-3 rounded-xl border border-white/[0.06] bg-dna-bg px-4 py-3"
                >
                  <div>
                    <div className="text-sm font-medium text-slate-100">{u.name}</div>
                    <div className="text-xs text-slate-500">Added {fmt(u.createdAt)}</div>
                  </div>
                  <Btn variant="danger" disabled={busy} onClick={() => delUser(u.id)}>
                    Remove
                  </Btn>
                </div>
              ))
            )}
          </div>
        </Card>
      </div>

      <Card>
        <div className="text-sm font-semibold text-slate-100">Checklist</div>
        <div className="mt-3 space-y-2 text-sm text-slate-400">
          <div className="flex gap-2">
            <span className="text-violet-400">▸</span> Even lighting
          </div>
          <div className="flex gap-2">
            <span className="text-violet-400">▸</span> Multiple angles
          </div>
          <div className="flex gap-2">
            <span className="text-fuchsia-400">▸</span> Liveness on
          </div>
        </div>
      </Card>
    </div>
  );
}
