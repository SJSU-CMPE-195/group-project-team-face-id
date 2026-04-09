import express from "express";
import cors from "cors";
import {
  addLog,
  createUser,
  deleteUser,
  getStatus,
  initDb,
  listLogs,
  listUsers,
  setLockState,
  upsertSettings,
} from "./db.js";

const app = express();
const runtimeProcess = globalThis.process;
const PORT = Number(runtimeProcess?.env?.PORT || 3001);

app.use(cors());
app.use(express.json());

initDb();

app.get("/api/status", (_req, res) => {
  res.json(getStatus());
});

app.post("/api/unlock", (req, res) => {
  const reason = req.body?.reason || "manual_ui";
  setLockState("unlocked");
  addLog("unlock", true, reason);
  res.json({ ok: true });
});

app.get("/api/users", (_req, res) => {
  res.json(listUsers());
});

app.post("/api/users", (req, res) => {
  const name = String(req.body?.name || "").trim();
  if (!name) return res.status(400).json({ error: "name is required" });
  try {
    const user = createUser(name);
    return res.status(201).json(user);
  } catch {
    return res.status(409).json({ error: "user already exists" });
  }
});

app.delete("/api/users/:id", (req, res) => {
  const ok = deleteUser(req.params.id);
  if (!ok) return res.status(404).json({ error: "user not found" });
  return res.json({ ok: true });
});

app.get("/api/logs", (_req, res) => {
  res.json(listLogs());
});

app.post("/api/settings", (req, res) => {
  const payload = req.body || {};
  upsertSettings(payload);
  res.json({ ok: true });
});

app.get("/health", (_req, res) => {
  res.json({ ok: true });
});

app.listen(PORT, () => {
  console.log(`BASS backend listening on http://localhost:${PORT}`);
});
