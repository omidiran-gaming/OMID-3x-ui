# OMID overlay + automatic Sanaei upstream sync

This repository was initially imported from a source ZIP, so it does not have the commit ancestry required for GitHub's **Sync fork** button. The workflow synchronizes the working source tree from `MHSanaei/3x-ui` and reapplies the OMID customizations on every scheduled run.

## Automatic update flow

- GitHub Actions checks `MHSanaei/3x-ui` `main` every day at 04:17 UTC, and can also be started manually.
- The latest upstream source is cloned fresh.
- `.omid/scripts/apply_omid_overlay.py` reapplies the small, version-checked OMID overlay: Nginx reverse proxy, stable `/sub/`, `/json/`, `/clash/` paths, public HTTPS subscription URLs, and Telegram subscription URL consistency.
- The source tree is synchronized into this repository while preserving `.github/` and `.omid/`.
- The ZIP-import workflow dispatches the sync/build workflow after it commits the extracted source, so the first image does not need to wait for the nightly schedule.
- If upstream changed, the source snapshot is committed to `main` and an updated Docker image is published to `ghcr.io/omidiran-gaming/omid-3x-ui:latest`.

## Where to put future OMID customizations

Do not edit upstream-managed Go files or `Dockerfile` directly: a sync replaces those files. Put Go patches in `.omid/scripts/apply_omid_overlay.py`, Nginx/Supervisor configs in `.omid/runtime/`, tests in `.omid/overlays/tests/`, and operator documentation in `.omid/docs/`. The entrypoint is patched in place from the latest upstream version, so future Sanaei changes to its Fail2ban/ACME setup are retained unless the upstream command layout changes; if the expected anchor changes, the workflow fails rather than publishing a silently broken overlay.

## One-time GitHub setup

In **Settings → Actions → General → Workflow permissions**, enable **Read and write permissions**. The workflow requires `contents: write` to commit the synchronized source and `packages: write` to publish to GHCR. If those permissions are disabled, the workflow will fail rather than updating the repository or image.

The workflow only publishes the new image; it does not automatically restart an external hosting service. The host must be configured to pull/redeploy `ghcr.io/omidiran-gaming/omid-3x-ui:latest` after a new image is published.
