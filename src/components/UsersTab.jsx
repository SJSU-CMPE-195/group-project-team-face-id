import React, { useEffect, useMemo, useRef, useState } from "react";
import { Crown, ShieldCheck } from "lucide-react";
import Badge from "./Badge";
import Btn from "./Btn";
import Card from "./Card";
import Input from "./Input";
import PairingInviteCard from "./PairingInviteCard.jsx";
import Switch from "./Switch";
import useEnrollment from "../hooks/useEnrollment.js";
import useOperationGrant from "../security/useOperationGrant.js";
import useSecurity from "../security/useSecurity.js";

export default function UsersTab({
  deviceUsers,
  setDeviceUsers,
  name,
  setName,
  busy,
  addUserToDirectory,
  delUser,
  popToast,
  api,
  currentUser,
  devices,
  setDevices,
  cameraLabel = "Host camera",
}) {
  const { requestGrant } = useOperationGrant();
  const { expireSession } = useSecurity();
  const [newUserPin, setNewUserPin] = useState("");
  const [pinDrafts, setPinDrafts] = useState({});
  const [invite, setInvite] = useState(null);
  const [inviteBusy, setInviteBusy] = useState(false);
  const [creating, setCreating] = useState(false);
  const [roleBusy, setRoleBusy] = useState("");
  const accountRoleRef = useRef("");
  const {
    enrollment,
    startEnrollment,
    cancelEnrollment,
    checkEnrollment,
  } = useEnrollment({
    api,
    busy,
    cameraLabel,
    currentUser,
    popToast,
    requestGrant,
    setDeviceUsers,
  });

  const users = useMemo(
    () =>
      currentUser.is_admin
        ? deviceUsers
        : deviceUsers.filter((user) => user.id === currentUser.id),
    [currentUser.id, currentUser.is_admin, deviceUsers],
  );

  useEffect(() => {
    const accountRole = `${currentUser.id}:${!!currentUser.is_owner}:${!!currentUser.is_admin}`;
    if (accountRoleRef.current && accountRoleRef.current !== accountRole) {
      setInvite(null);
      setPinDrafts({});
      setName("");
      setNewUserPin("");
    }
    accountRoleRef.current = accountRole;
  }, [currentUser, setName]);

  async function createUser() {
    const displayName = name.trim();
    if (!displayName) {
      popToast("err", "Name required", "Enter a display name.");
      return;
    }
    if (!/^\d{6}$/.test(newUserPin)) {
      popToast("err", "PIN required", "Enter a 6-digit ASCII PIN.");
      return;
    }
    setCreating(true);
    try {
      const created = await addUserToDirectory(displayName, newUserPin);
      if (!created) return;
      setName("");
      setNewUserPin("");
      popToast(
        "ok",
        "User added",
        `${created.name || displayName} can enroll a face later with ${cameraLabel}.`,
      );
    } catch (error) {
      popToast("err", "Add user failed", error.message);
    } finally {
      setCreating(false);
    }
  }

  async function updateAccess(user, allowed) {
    if (user.is_owner && !currentUser.is_owner) {
      popToast("err", "Owner only", "Only the owner can change this access.");
      return;
    }
    try {
      const grant = await requestGrant(
        "user.access",
        user.id,
        `change access for ${user.name}`,
      );
      if (!grant) return;
      await api.setAccess(user.id, allowed, grant);
      setDeviceUsers((previous) =>
        previous.map((row) =>
          row.id === user.id ? { ...row, faceAccess: allowed } : row,
        ),
      );
    } catch (error) {
      popToast("err", "Access update failed", error.message);
    }
  }

  async function updateRole(user, isAdmin) {
    if (!currentUser.is_owner || user.is_owner || roleBusy) return;
    setRoleBusy(user.id);
    try {
      const grant = await requestGrant(
        "user.role",
        user.id,
        `${isAdmin ? "make" : "remove"} administrator ${user.name}`,
      );
      if (!grant) return;
      const result = await api.setRole(user.id, isAdmin, grant);
      const updated = result.user || result;
      setDeviceUsers((previous) =>
        previous.map((row) => {
          if (row.is_owner) return row;
          if (row.id === user.id) return { ...row, ...updated };
          return isAdmin ? { ...row, is_admin: false } : row;
        }),
      );
      popToast(
        "ok",
        "Administrator updated",
        isAdmin
          ? `${user.name} is now the administrator.`
          : `${user.name} is now a regular user.`,
      );
    } catch (error) {
      popToast("err", "Role update failed", error.message);
    } finally {
      setRoleBusy("");
    }
  }

  async function resetPin(user) {
    if (user.is_owner && !currentUser.is_owner) {
      popToast("err", "Owner only", "Only the owner can change this PIN.");
      return;
    }
    const pin = pinDrafts[user.id] || "";
    if (!/^\d{6}$/.test(pin)) {
      popToast("err", "PIN required", "Enter a 6-digit ASCII PIN.");
      return;
    }
    try {
      const grant = await requestGrant(
        "user.pin",
        user.id,
        `reset PIN for ${user.name}`,
        { name: user.name, pin },
      );
      if (!grant) return;
      await api.resetUserPin(user.id, pin, grant);
      setPinDrafts((previous) => ({ ...previous, [user.id]: "" }));
      if (invite?.userId === user.id) setInvite(null);
      if (user.id === currentUser.id) {
        expireSession("login");
        return;
      }
      setDevices((previous) =>
        previous.map((device) =>
          device.user_id === user.id ? { ...device, active: false } : device,
        ),
      );
      popToast(
        "ok",
        "PIN updated",
        `${user.name}'s paired devices were revoked. Pair them again with the new PIN.`,
      );
      void api.devices().then(setDevices).catch(() => {});
    } catch (error) {
      popToast("err", "PIN reset failed", error.message);
    }
  }

  async function createInvite(user) {
    if (inviteBusy) return;
    if (user.is_owner && !currentUser.is_owner) {
      popToast("err", "Owner only", "Only the owner can pair this device.");
      return;
    }
    setInviteBusy(true);
    try {
      const grant = await requestGrant(
        "pairing.invite",
        user.id,
        `create pairing invite for ${user.name}`,
      );
      if (!grant) return;
      const requestedAt = Date.now();
      const result = await api.pairingInvite(user.id, grant);
      if (
        typeof result.qr_image !== "string" ||
        !result.qr_image.startsWith("data:image/png;base64,") ||
        !result.qr_payload ||
        typeof result.qr_payload !== "object" ||
        Array.isArray(result.qr_payload) ||
        result.qr_payload.version !== 3 ||
        result.qr_payload.purpose !== "pairing_invite" ||
        !Number.isFinite(result.expires_in) ||
        result.expires_in <= 0
      ) {
        throw new Error("The host did not return an invitation QR. Try again.");
      }
      setInvite({
        userId: user.id,
        userName: user.name,
        qrImage: result.qr_image,
        qrPayload: result.qr_payload,
        expiresAt: requestedAt + result.expires_in * 1000,
      });
    } catch (error) {
      popToast("err", "Invite failed", error.message);
    } finally {
      setInviteBusy(false);
    }
  }

  async function revokeDevice(device) {
    const ownerDevice = device.is_owner || device.user_is_owner;
    if (ownerDevice && !currentUser.is_owner) {
      popToast("err", "Owner only", "Only the owner can revoke this device.");
      return;
    }
    if (!confirm(`Revoke ${device.name || "this device"}?`)) return;
    try {
      const grant = await requestGrant(
        "device.revoke",
        device.id,
        `revoke ${device.name || "device"}`,
      );
      if (!grant) return;
      await api.revokeDevice(device.id, grant);
      try {
        setDevices(await api.devices());
      } catch (refreshError) {
        if (refreshError.status === 401) return;
        popToast(
          "info",
          "Device revoked",
          "The device was revoked, but the paired-device list could not be refreshed.",
        );
        return;
      }
      popToast("ok", "Device revoked", device.name || device.id);
    } catch (error) {
      popToast("err", "Revoke failed", error.message);
    }
  }

  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col gap-3">
      {currentUser.is_admin ? (
        <Card>
          <div className="text-sm font-semibold text-slate-100">Add user</div>
          <div className="mt-4 grid gap-3 sm:grid-cols-2">
            <div>
              <label htmlFor="display-name" className="mb-1 block text-[11px] font-medium uppercase tracking-wider text-slate-500">
                Display name
              </label>
              <Input
                id="display-name"
                value={name}
                onChange={(event) => setName(event.target.value)}
                maxLength={100}
                disabled={creating}
              />
            </div>
            <div>
              <label htmlFor="new-user-pin" className="mb-1 block text-[11px] font-medium uppercase tracking-wider text-slate-500">
                New user PIN
              </label>
              <Input
                id="new-user-pin"
                type="password"
                autoComplete="new-password"
                maxLength={6}
                inputMode="numeric"
                pattern="[0-9]{6}"
                value={newUserPin}
                onChange={(event) => setNewUserPin(event.target.value)}
                disabled={creating}
              />
            </div>
            <Btn
              className="sm:col-span-2"
              disabled={busy || creating}
              onClick={() => void createUser()}
            >
              {creating ? "Adding…" : "Add user"}
            </Btn>
          </div>
        </Card>
      ) : null}

      {enrollment ? (
        <Card>
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <div className="text-sm font-semibold text-slate-100">
                {enrollment.needsCheck
                  ? `Enrollment recovery for ${enrollment.userName}`
                  : `Enrolling ${enrollment.userName}`}
              </div>
              <p className="mt-2 text-xs text-slate-400">
                {cameraLabel} · {enrollment.state}
              </p>
            </div>
            <Badge>
              Samples {enrollment.count} / {enrollment.samplesNeeded}
            </Badge>
          </div>
          {enrollment.message ? (
            <p className="mt-3 text-xs leading-relaxed text-amber-100">
              {enrollment.message}
            </p>
          ) : null}
          <div className="mt-4 flex flex-wrap gap-2">
            {enrollment.canCancel ? (
              <Btn
                variant="secondary"
                onClick={() => void cancelEnrollment()}
              >
                Cancel
              </Btn>
            ) : null}
            {enrollment.needsCheck ? (
              <Btn
                variant="secondary"
                onClick={() => void checkEnrollment()}
              >
                Check status
              </Btn>
            ) : null}
          </div>
        </Card>
      ) : null}

      <Card>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="text-sm font-semibold text-slate-100">Users</div>
          <Badge>{users.length}</Badge>
        </div>
        <div className="mt-4 space-y-3">
          {users.length === 0 ? (
            <div className="text-sm text-slate-500">No users available.</div>
          ) : (
            users.map((user) => (
              <UserRow
                key={user.id}
                user={user}
                currentUser={currentUser}
                busy={busy}
                enrollment={enrollment}
                roleBusy={roleBusy}
                inviteBusy={inviteBusy}
                pin={pinDrafts[user.id] || ""}
                setPin={(pin) =>
                  setPinDrafts((previous) => ({
                    ...previous,
                    [user.id]: pin,
                  }))
                }
                onEnroll={() => void startEnrollment(user)}
                onAccess={(allowed) => void updateAccess(user, allowed)}
                onRole={(isAdmin) => void updateRole(user, isAdmin)}
                onResetPin={() => void resetPin(user)}
                onPair={() => void createInvite(user)}
                onRemove={() => void delUser(user.id)}
              />
            ))
          )}
        </div>

        {invite ? (
          <PairingInviteCard
            key={invite.expiresAt}
            invite={invite}
            onDismiss={() => setInvite(null)}
          />
        ) : null}
      </Card>

      {currentUser.is_admin ? (
        <PairedDevices
          devices={devices}
          currentUser={currentUser}
          onRevoke={(device) => void revokeDevice(device)}
        />
      ) : null}
    </div>
  );
}

function UserRow({
  user,
  currentUser,
  busy,
  enrollment,
  roleBusy,
  inviteBusy,
  pin,
  setPin,
  onEnroll,
  onAccess,
  onRole,
  onResetPin,
  onPair,
  onRemove,
}) {
  const ownerProtected = user.is_owner && !currentUser.is_owner;
  const hasFace = user.faceEnrolled === true;

  return (
    <div className="rounded-xl border border-white/[0.06] bg-dna-bg p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-1.5 text-sm font-medium text-slate-100">
            <span>{user.name}</span>
            {user.is_owner ? (
              <span title="Owner">
                <Crown
                  className="h-4 w-4 text-amber-300"
                  aria-hidden="true"
                />
                <span className="sr-only">Owner</span>
              </span>
            ) : user.is_admin ? (
              <span title="Administrator">
                <ShieldCheck
                  className="h-4 w-4 text-sky-300"
                  aria-hidden="true"
                />
                <span className="sr-only">Administrator</span>
              </span>
            ) : null}
          </div>
          <div className="mt-2 flex flex-wrap gap-2">
            <Badge variant={hasFace ? "ok" : "warn"}>
              {hasFace ? "Face enrolled" : "Face pending"}
            </Badge>
            {user.faceAccess === false ? (
              <Badge variant="warn">Access blocked</Badge>
            ) : null}
          </div>
        </div>
        {currentUser.is_admin ? (
          <Btn
            variant="secondary"
            disabled={!!enrollment || ownerProtected}
            onClick={onEnroll}
          >
            {hasFace ? "Replace face" : "Enroll face"}
          </Btn>
        ) : null}
      </div>

      {currentUser.is_admin ? (
        <div className="mt-4 space-y-3 border-t border-white/[0.06] pt-4">
          <div className="flex min-h-11 items-center justify-between gap-3">
            <span className="text-sm text-slate-300">Face access</span>
            <Switch
              ariaLabel={`${user.name} face access`}
              checked={user.faceAccess !== false}
              disabled={ownerProtected}
              onChange={onAccess}
            />
          </div>
          {currentUser.is_owner && !user.is_owner ? (
            <div className="flex min-h-11 items-center justify-between gap-3">
              <div>
                <div className="flex items-center gap-1.5 text-sm text-slate-300">
                  <ShieldCheck
                    className="h-4 w-4 text-sky-300"
                    aria-hidden="true"
                  />
                  Administrator
                </div>
                <div className="text-xs text-slate-500">
                  Enabling this replaces the current administrator.
                </div>
              </div>
              <Switch
                ariaLabel={`${user.name} administrator role`}
                checked={!!user.is_admin}
                disabled={!!roleBusy}
                onChange={onRole}
              />
            </div>
          ) : null}
          <div className="flex flex-col gap-2 sm:flex-row">
            <Input
              type="password"
              autoComplete="new-password"
              maxLength={6}
              inputMode="numeric"
              pattern="[0-9]{6}"
              value={pin}
              onChange={(event) => setPin(event.target.value)}
              placeholder="New 6-digit PIN"
              aria-label={`New PIN for ${user.name}`}
              disabled={ownerProtected}
            />
            <Btn
              variant="secondary"
              disabled={ownerProtected}
              onClick={onResetPin}
            >
              Reset PIN
            </Btn>
            <Btn
              variant="secondary"
              disabled={inviteBusy || ownerProtected}
              onClick={onPair}
            >
              Pair device
            </Btn>
            <Btn
              variant="danger"
              disabled={busy || !!user.is_owner}
              onClick={onRemove}
            >
              Remove
            </Btn>
          </div>
        </div>
      ) : null}
    </div>
  );
}

function PairedDevices({ devices, currentUser, onRevoke }) {
  return (
    <Card>
      <div className="text-sm font-semibold text-slate-100">
        Paired devices
      </div>
      <div className="mt-3 space-y-2">
        {(devices || []).length === 0 ? (
          <div className="text-sm text-slate-500">No paired devices.</div>
        ) : (
          devices.map((device) => (
            <div
              key={device.id}
              className="flex items-center justify-between gap-3 rounded-xl border border-white/[0.06] bg-dna-bg px-4 py-3"
            >
              <div className="min-w-0">
                <div className="truncate text-sm text-slate-100">
                  {device.name || "Paired device"}
                </div>
                <div className="flex items-center gap-1 text-xs text-slate-500">
                  <span>
                    {device.user_name} · {device.active ? "Active" : "Revoked"}
                  </span>
                  {device.is_owner || device.user_is_owner ? (
                    <span title="Owner">
                      <Crown
                        className="h-3.5 w-3.5 text-amber-300"
                        aria-hidden="true"
                      />
                      <span className="sr-only">Owner</span>
                    </span>
                  ) : null}
                </div>
              </div>
              {device.active ? (
                <Btn
                  variant="danger"
                  disabled={
                    (device.is_owner || device.user_is_owner) &&
                    !currentUser.is_owner
                  }
                  onClick={() => onRevoke(device)}
                >
                  Revoke
                </Btn>
              ) : null}
            </div>
          ))
        )}
      </div>
    </Card>
  );
}
