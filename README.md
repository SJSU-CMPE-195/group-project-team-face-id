# The Biometric Automobile Security System
## Project Structure

```
src/
  components/           # Reusable UI components
  hooks/               # Custom React hooks (state & logic)
  utils/               # Helper functions
  App.jsx              # Root container component
  main.jsx             # Entry point
```

## Components Reference

| Component | Location | Purpose |
|-----------|----------|---------|
| **Badge** | `components/Badge.jsx` | Small inline label with neutral styling |
| **Card** | `components/Card.jsx` | Rounded card container with shadow |
| **Btn** | `components/Btn.jsx` | Button with primary/secondary/danger variants |
| **Input** | `components/Input.jsx` | Text/number input field |
| **Switch** | `components/Switch.jsx` | Toggle switch for boolean settings |
| **TabBtn** | `components/TabBtn.jsx` | Tab button (control, users, logs, settings) |
| **Toast** | `components/Toast.jsx` | Notification popup (success/error/info) |
| **Header** | `components/Header.jsx` | Page title, description, and refresh button |
| **Overview** | `components/Overview.jsx` | Three info cards (Mode, Device, Safety) |
| **StatusPanel** | `components/StatusPanel.jsx` | Lock state, battery, and signal status |
| **ConnectionPanel** | `components/ConnectionPanel.jsx` | Mode toggle, base URL input, demo story |
| **Tabs** | `components/Tabs.jsx` | Navigation tabs for content sections |
| **ControlTab** | `components/ControlTab.jsx` | Camera placeholder and quick action buttons |
| **UsersTab** | `components/UsersTab.jsx` | Enroll/remove users (sim mode only) |
| **LogsTab** | `components/LogsTab.jsx` | Event log viewer with clear button |
| **SettingsTab** | `components/SettingsTab.jsx` | Core settings (re-lock, liveness, lockout) |

## Hooks Reference

| Hook | Location | Purpose |
|------|----------|---------|
| **useAppState** | `hooks/useAppState.js` | Central state management (mode, sim, status, settings, etc.) + localStorage sync |
| **useAppActions** | `hooks/useAppActions.js` | Business logic (refresh, unlock, enroll, delete user, save settings) |
| **useApi** | `hooks/useApi.js` | API / simulation layer (handles device vs. sim mode) |

## Utils Reference

| Utility | Location | Purpose |
|---------|----------|---------|
| **clamp** | `utils/helpers.js` | Clamp a number between min and max |
| **genId** | `utils/helpers.js` | Generate unique IDs for toasts, users, logs |
| **fmt** | `utils/helpers.js` | Format timestamp to locale string |

## Key Features

-  **Dual Mode**: Simulation for demo, Device API for production
-  **Persistent State**: All data saved to localStorage
-  **Component-Based**: Reusable, testable UI components
-  **Lightweight App**: `App.jsx` is a pure container (~87 lines)
-  **Modular Logic**: State and actions separated into custom hooks
-  **Tab Navigation**: Control, Users, Logs, Settings sections

## Quick Start

```bash
npm install
npm run dev
```

Open [http://localhost:5173](http://localhost:5173) in your browser.
