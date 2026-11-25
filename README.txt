RAMFREEMCHOSTING — V2 DEVELOPMENT BUILD
=======================================

WHAT CHANGED IN THIS BUILD
--------------------------
- Rebuilt the Account & Rewards page into a responsive neon control hub.
- Added a desktop sidebar, mobile navigation, welcome banner, account stat cards,
  streak progress display, reward center, membership cards, leaderboards,
  cosmetics, community support and file/backups sections.
- Existing element IDs and API calls in the account page were retained so its
  current account/reward interactions continue to use the same backend.
- Existing Minecraft server dashboard and server controls were preserved.
- The main server dashboard links to Account & Rewards at /platform.

RUN
---
1. Back up your existing project and Minecraft worlds.
2. Extract this ZIP into a NEW folder.
3. Install Python 3.11+ and Java (for Minecraft servers).
4. Run start.bat. It installs requirements and starts Flask.
5. Open http://127.0.0.1:5000
6. Select Account & Rewards in the left navigation to open the redesigned hub.

ACCOUNT / REWARD MODULES IN THIS DEVELOPMENT BUILD
---------------------------------------------------
- Signup/login with salted PBKDF2 password hashing.
- Per-account profile, RamPoints, Spin Points and Lucky Gems.
- Daily claim and 99-day streak reward logic (Day 7, 30 and 99 milestones).
- Free membership; Premium ₹99 and Ultimate ₹299 shown as Coming Soon.
- No payment gateway and no paid purchase.
- Daily free spins by membership tier; disclosed equal odds for 10, 20 or
  50 Spin Points. No paid spins.
- Separate RamPoints, Spin Points and VIP leaderboards; privacy opt-out.
- Lucky Gem cosmetic catalog and permanent ownership.
- Referral signup tracking (no referral payout until verification).
- Announcements and user support tickets.
- Per-account personal file storage (512 MB/account, 25 MB/upload), download,
  delete, and downloadable account-file ZIP backups.

IMPORTANT LIMITATIONS
---------------------
This is a LOCAL DEVELOPMENT BUILD, not a finished public hosting service.
The Minecraft engine still uses one global server process/configuration. It does
NOT yet provide independent Minecraft process isolation and ownership for
multiple users. Do not allow multiple accounts to control this shared legacy
server.

The file manager/backups cover account-uploaded files only. They do not yet
manage Minecraft worlds or provide automatic world backup/restore. Live CPU,
RAM and player monitoring, modpack manager, email verification/password reset,
verified referral payouts, admin reward tools and Admin Dashboard are not
implemented. Admin Dashboard is intentionally excluded from this scope.

Do not expose this build to the public internet. Keep it bound to localhost
until isolated multi-user server processes, authorization, quotas, network
controls, monitoring and a full security review are implemented and tested.

DATA LOCATIONS
--------------
- Existing Minecraft settings: panel_config.json and existing server folders.
- New account/reward database: platform_data/platform.sqlite3
- New personal account files: platform_data/users/<user_id>/
- New account ZIP backups: platform_data/backups/<user_id>/

Do not delete server_data/, servers/, panel_config.json or platform_data/.
Keep backups before replacing any older build.
