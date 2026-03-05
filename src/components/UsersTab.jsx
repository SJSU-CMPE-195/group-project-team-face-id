import React from "react";
import Card from "./Card";
import Badge from "./Badge";
import Input from "./Input";
import Btn from "./Btn";
import { fmt } from "../utils/helpers";

export default function UsersTab({ mode, sim, name, setName, busy, addUser, delUser }) {
  if (mode === "device") {
    return (
      <Card>
        <div className="text-sm font-semibold">Users (Device API mode)</div>
        <div className="mt-2 text-sm text-neutral-600">
          Implement <code>/api/users</code> (GET/POST/DELETE) on Pi, then this can become live.
        </div>
      </Card>
    );
  }

  return (
    <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
      <div className="md:col-span-2">
        <Card>
          <div className="flex items-center justify-between">
            <div>
              <div className="text-sm font-semibold">Enrolled users</div>
              <div className="mt-1 text-xs text-neutral-600">Each user represents a stored face template.</div>
            </div>
            <Badge>{sim.users.length} users</Badge>
          </div>

          <div className="mt-4 flex gap-2">
            <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="Name (e.g., Mei)" />
            <Btn disabled={busy} onClick={addUser}>Add</Btn>
          </div>

          <div className="mt-4 space-y-2">
            {sim.users.map((u) => (
              <div key={u.id} className="flex items-center justify-between rounded-2xl border border-neutral-200 px-4 py-3">
                <div>
                  <div className="text-sm font-medium">{u.name}</div>
                  <div className="text-xs text-neutral-500">Added: {fmt(u.createdAt)}</div>
                </div>
                <Btn variant="danger" disabled={busy} onClick={() => delUser(u.id)}>Remove</Btn>
              </div>
            ))}
          </div>
        </Card>
      </div>

      <Card>
        <div className="text-sm font-semibold">Checklist</div>
        <div className="mt-2 text-sm text-neutral-700 space-y-2">
          <div>• Good lighting</div>
          <div>• Multiple angles</div>
          <div>• Liveness ON recommended</div>
        </div>
      </Card>
    </div>
  );
}
