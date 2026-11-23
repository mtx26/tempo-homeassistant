# Tempo — Home Assistant integration

**Status: scaffold only (milestone 6 not started). Not installable or functional yet.**

Home Assistant custom integration (HACS-compatible layout) for Tempo, a self-hosted personal
planner. The planning backend (Tempo Core) is a separate private service; this repository only
contains the Home Assistant side: config flow, calendar, to-do, sensors, services, approvals.

## Before publication (do not publish without the owner's approval)
- Replace `OWNER` in `custom_components/tempo/manifest.json` (`codeowners`, `documentation`,
  `issue_tracker`).
- Choose a licence, add `brand/icon.png`, GitHub description and topics, first GitHub release.
- Pass `hacs/action` (category `integration`) and hassfest in CI.
