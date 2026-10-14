# F2 changelog (productization)

## 1. Design system foundation
- `frontend/src/ui/`: Button, Banner, Dialog, Tabs
- `tailwind.config.js` tokens + CSS variables (warm paper + dark)
- Migrated: DegradedInstallBanner, ApprovalPrompt, MainViewTabs, ActivityRail accents, Composer send/configure

## 2. Simple vs Advanced
- `workspaceMode: 'simple' | 'advanced'` in `WorkspaceSettings` (persisted, migrate v2)
- Default **simple**; existing `developerMode` users migrate to advanced
- Settings nav hides automations / MCP / Computer Use / plugins / governance in simple
- General settings selector + i18n

## 3. Threat model
- `docs/THREAT_MODEL.md` linked from README

## 4. Accessibility
- F2 manual Orca/NVDA / 200% zoom / high-contrast section in `docs/ACCESSIBILITY_CHECKLIST.md`
- Dialog primitive uses existing focus trap

## 5. Sandbox tiers
- `AETHERCHAT_SANDBOX_TIER=bwrap|docker|strong`
- `strong` = Docker-hardened today; fail closed without Docker
- Diagnostics `sandbox` snapshot; `docs/SANDBOX.md` updated
