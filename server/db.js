import Database from "better-sqlite3";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const DB_PATH = path.join(__dirname, "face-lock.db");

const db = new Database(DB_PATH);
db.pragma("journal_mode = WAL");

function nowIso() {
  return new Date().toISOString();
}

function initDb() {
  db.exec(`
    CREATE TABLE IF NOT EXISTS users (
      id TEXT PRIMARY KEY,
      name TEXT NOT NULL UNIQUE,
      created_at TEXT NOT NULL,
      active INTEGER NOT NULL DEFAULT 1
    );

    CREATE TABLE IF NOT EXISTS face_embeddings (
      id TEXT PRIMARY KEY,
      user_id TEXT NOT NULL,
      embedding_ciphertext BLOB NOT NULL,
      iv BLOB NOT NULL,
      key_version INTEGER NOT NULL DEFAULT 1,
      created_at TEXT NOT NULL,
      FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS auth_attempts (
      id TEXT PRIMARY KEY,
      user_id TEXT,
      matched INTEGER NOT NULL,
      similarity REAL,
      liveness_ok INTEGER NOT NULL DEFAULT 1,
      source TEXT NOT NULL DEFAULT 'camera',
      created_at TEXT NOT NULL,
      FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL
    );

    CREATE TABLE IF NOT EXISTS device_state (
      id INTEGER PRIMARY KEY CHECK (id = 1),
      device_name TEXT NOT NULL,
      lock_state TEXT NOT NULL CHECK (lock_state IN ('locked', 'unlocked')),
      battery INTEGER NOT NULL,
      signal INTEGER NOT NULL,
      last_seen TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS settings (
      key TEXT PRIMARY KEY,
      value TEXT NOT NULL,
      updated_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS event_logs (
      id TEXT PRIMARY KEY,
      ts TEXT NOT NULL,
      type TEXT NOT NULL,
      ok INTEGER NOT NULL DEFAULT 1,
      detail TEXT NOT NULL,
      user_id TEXT,
      FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL
    );
  `);

  const stateExists = db.prepare("SELECT 1 FROM device_state WHERE id = 1").get();
  if (!stateExists) {
    db.prepare(
      `INSERT INTO device_state (id, device_name, lock_state, battery, signal, last_seen)
       VALUES (1, @device_name, @lock_state, @battery, @signal, @last_seen)`
    ).run({
      device_name: "FaceLock-Pi",
      lock_state: "locked",
      battery: 100,
      signal: 5,
      last_seen: nowIso(),
    });
  }

  const settingsCount = db.prepare("SELECT COUNT(*) AS count FROM settings").get().count;
  if (settingsCount === 0) {
    const insertSetting = db.prepare("INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)");
    const ts = nowIso();
    insertSetting.run("autoRelockSeconds", "10", ts);
    insertSetting.run("liveness", "true", ts);
    insertSetting.run("failLockout", "true", ts);
    insertSetting.run("lockoutAfter", "5", ts);
  }

  const usersCount = db.prepare("SELECT COUNT(*) AS count FROM users").get().count;
  if (usersCount === 0) {
    const insertUser = db.prepare("INSERT INTO users (id, name, created_at, active) VALUES (?, ?, ?, 1)");
    const ts = nowIso();
    insertUser.run("u_mei", "Mei", ts);
    insertUser.run("u_alex", "Alex", ts);
  }

  const logsCount = db.prepare("SELECT COUNT(*) AS count FROM event_logs").get().count;
  if (logsCount === 0) {
    const insertLog = db.prepare("INSERT INTO event_logs (id, ts, type, ok, detail, user_id) VALUES (?, ?, ?, ?, ?, ?)");
    const ts = nowIso();
    insertLog.run("l_boot", ts, "boot", 1, "Device started", null);
    insertLog.run("l_lock", ts, "lock", 1, "Locked", null);
  }
}

function genId(prefix) {
  return `${prefix}_${Math.random().toString(36).slice(2, 10)}`;
}

function getStatus() {
  const row = db.prepare("SELECT * FROM device_state WHERE id = 1").get();
  return {
    online: true,
    lockState: row.lock_state,
    deviceName: row.device_name,
    battery: row.battery,
    signal: row.signal,
    lastSeen: new Date(row.last_seen).getTime(),
  };
}

function setLockState(lockState) {
  db.prepare("UPDATE device_state SET lock_state = ?, last_seen = ? WHERE id = 1").run(lockState, nowIso());
}

function addLog(type, ok, detail, userId = null) {
  db.prepare("INSERT INTO event_logs (id, ts, type, ok, detail, user_id) VALUES (?, ?, ?, ?, ?, ?)").run(
    genId("log"),
    nowIso(),
    type,
    ok ? 1 : 0,
    detail,
    userId
  );
}

function listUsers() {
  return db
    .prepare("SELECT id, name, created_at FROM users WHERE active = 1 ORDER BY datetime(created_at) DESC")
    .all()
    .map((row) => ({ id: row.id, name: row.name, createdAt: new Date(row.created_at).getTime() }));
}

function createUser(name) {
  const user = {
    id: genId("u"),
    name,
    created_at: nowIso(),
  };
  db.prepare("INSERT INTO users (id, name, created_at, active) VALUES (@id, @name, @created_at, 1)").run(user);
  addLog("enroll", true, `Added ${name}`, user.id);
  return { id: user.id, name: user.name, createdAt: new Date(user.created_at).getTime() };
}

function deleteUser(id) {
  const result = db.prepare("DELETE FROM users WHERE id = ?").run(id);
  addLog("delete_user", result.changes > 0, `Deleted ${id}`);
  return result.changes > 0;
}

function listLogs() {
  return db
    .prepare("SELECT id, ts, type, ok, detail FROM event_logs ORDER BY datetime(ts) DESC LIMIT 80")
    .all()
    .map((row) => ({
      id: row.id,
      ts: new Date(row.ts).getTime(),
      type: row.type,
      ok: !!row.ok,
      detail: row.detail,
    }));
}

function upsertSettings(nextSettings) {
  const stmt = db.prepare(
    `INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)
     ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at`
  );
  const ts = nowIso();
  const tx = db.transaction((entries) => {
    for (const [key, value] of entries) {
      stmt.run(key, String(value), ts);
    }
  });
  tx(Object.entries(nextSettings));
  addLog("settings", true, "Updated settings");
  return true;
}

export {
  initDb,
  getStatus,
  setLockState,
  listUsers,
  createUser,
  deleteUser,
  listLogs,
  upsertSettings,
  addLog,
};
