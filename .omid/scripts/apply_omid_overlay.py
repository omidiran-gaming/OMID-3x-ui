#!/usr/bin/env python3
"""Apply OMID reverse-proxy and subscription customizations to fresh Sanaei source."""
from __future__ import annotations
import re
import shutil
import sys
from pathlib import Path


def fail(message: str) -> None:
    raise SystemExit(f"apply_omid_overlay: ERROR: {message}")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        return text
    count = text.count(old)
    if count != 1:
        fail(f"{label}: expected exactly one upstream anchor, found {count}")
    return text.replace(old, new, 1)


def copy_runtime(overlay: Path, upstream: Path) -> None:
    # Copy only OMID-owned configs. The entrypoint is modified in place so Sanaei's
    # current Fail2ban/ACME startup code remains present after every upstream sync.
    copies = {
        overlay / "runtime" / "supervisord.conf": upstream / "supervisord.conf",
        overlay / "runtime" / "nginx" / "default.conf": upstream / "nginx" / "default.conf",
        overlay / "runtime" / "nginx" / "nginx.conf": upstream / "nginx" / "nginx.conf",
    }
    for src, dst in copies.items():
        if not src.is_file():
            fail(f"overlay file missing: {src}")
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


def patch_entrypoint(repo: Path) -> None:
    """Keep Sanaei's startup code; redirect the final process to Supervisor."""
    path = repo / "DockerEntrypoint.sh"
    text = path.read_text()

    panel_line = "    PANEL_PORT=$(/app/x-ui setting -show true 2>/dev/null | grep -Eo 'port: .+' | awk '{print $2}')"
    if '[ -n "${XUI_PORT:-}" ] && PANEL_PORT="$XUI_PORT"' not in text:
        text = replace_once(
            text,
            panel_line,
            panel_line + '\n    # Panel backend is behind Nginx in OMID public HTTPS mode.\n    [ -n "${XUI_PORT:-}" ] && PANEL_PORT="$XUI_PORT"',
            "DockerEntrypoint.sh panel port override",
        )
    if 'EXEMPT_PORTS="$EXEMPT_PORTS,2053"' not in text:
        exempt_line = '    [ -n "$PANEL_PORT" ] && EXEMPT_PORTS="$EXEMPT_PORTS,$PANEL_PORT"'
        text = replace_once(
            text,
            exempt_line,
            exempt_line + '\n    EXEMPT_PORTS="$EXEMPT_PORTS,2053" # Nginx public port',
            "DockerEntrypoint.sh Nginx port exemption",
        )

    supervisor_command = "exec /usr/bin/supervisord -n -c /etc/supervisord.conf"
    if supervisor_command not in text:
        text = replace_once(
            text,
            "# Run x-ui\nexec /app/x-ui",
            "# Run panel and Nginx under Supervisor for the OMID reverse-proxy image.\n" + supervisor_command,
            "DockerEntrypoint.sh final command",
        )
    path.write_text(text)


def patch_dockerfile(repo: Path) -> None:
    path = repo / "Dockerfile"
    text = path.read_text()

    # Add Nginx and Supervisor to the final Alpine image; don't replace upstream's
    # builder commands, dependencies, architecture logic, or version metadata.
    if not re.search(r"(?m)^\s*nginx\s*\\?\s*$", text):
        anchor = re.search(r"(?m)^(?P<indent>\s*)openssl\s*$", text)
        if not anchor:
            fail("Dockerfile changed: final-image openssl package anchor not found")
        indent = anchor.group("indent")
        replacement = "\n".join([f"{indent}openssl " + "\\", f"{indent}nginx " + "\\", f"{indent}supervisor"])
        text = text[:anchor.start()] + replacement + text[anchor.end():]

    def ensure_after(anchor: str, line: str, label: str) -> None:
        nonlocal text
        if line in text:
            return
        idx = text.find(anchor)
        if idx < 0:
            fail(f"Dockerfile changed: expected anchor not found for {label}")
        end = text.find("\n", idx)
        text = text[:end + 1] + line + "\n" + text[end + 1:]

    translation_copy = "COPY --from=builder /app/internal/web/translation /app/internal/web/translation"
    if translation_copy not in text:
        # Fail closed rather than guessing if Sanaei changes the Docker build layout.
        fail("Dockerfile changed: upstream translation COPY anchor not found")
    ensure_after(translation_copy, "COPY nginx/default.conf /etc/nginx/http.d/default.conf", "Nginx virtual host copy")
    ensure_after("COPY nginx/default.conf /etc/nginx/http.d/default.conf", "COPY nginx/nginx.conf /etc/nginx/nginx.conf", "Nginx main config copy")
    ensure_after("COPY nginx/nginx.conf /etc/nginx/nginx.conf", "COPY supervisord.conf /etc/supervisord.conf", "Supervisor config copy")

    # These are normal writable temp directories used by OMID's existing Nginx config.
    if "/tmp/nginx/client_body" not in text:
        anchor = 'ENV XUI_IN_DOCKER="true"'
        if anchor not in text:
            fail("Dockerfile changed: XUI_IN_DOCKER anchor not found for Nginx temp paths")
        text = text.replace(anchor, "RUN mkdir -p /tmp/nginx/client_body /tmp/nginx/proxy /tmp/nginx/fastcgi /tmp/nginx/uwsgi /tmp/nginx/scgi\n" + anchor, 1)

    base_env = (
        'ENV XUI_IN_DOCKER="true"\n'
        'ENV XUI_MAIN_FOLDER="/app"\n'
        'ENV XUI_ENABLE_FAIL2BAN="true"\n'
        'ENV XUI_DB_TYPE=""\n'
        'ENV XUI_DB_DSN=""'
    )
    new_env = (
        'ENV XUI_IN_DOCKER="true"\n'
        'ENV XUI_MAIN_FOLDER="/app"\n'
        'ENV XUI_ENABLE_FAIL2BAN="true"\n'
        '# OMID public HTTPS proxy: Nginx fronts the panel and subscription server.\n'
        'ENV XUI_PORT="8080"\n'
        'ENV XUI_SKIP_HSTS="true"\n'
        'ENV XUI_SUB_PUBLIC_HTTPS="true"\n'
        'ENV XUI_DB_TYPE=""\n'
        'ENV XUI_DB_DSN=""'
    )
    if 'ENV XUI_SUB_PUBLIC_HTTPS="true"' not in text:
        text = replace_once(text, base_env, new_env, "Dockerfile environment block")

    ignore = repo / ".dockerignore"
    contents = ignore.read_text() if ignore.exists() else ""
    if not re.search(r"(?m)^\.omid/?$", contents):
        ignore.write_text(contents.rstrip() + ("\n" if contents.strip() else "") + ".omid/\n")
    path.write_text(text)


def patch_database(repo: Path) -> None:
    path = repo / "internal/database/db.go"
    text = path.read_text()
    if "func seedDefaultSubscriptionPaths() error" not in text:
        match = re.search(r"func seedRandomSubscriptionPaths\(\) error \{.*?\n\}", text, re.S)
        if not match:
            fail("db.go changed: seedRandomSubscriptionPaths() not found")
        replacement = '''func seedDefaultSubscriptionPaths() error {
	settings := []model.Setting{
		{Key: "subPath", Value: "/sub/"},
		{Key: "subJsonPath", Value: "/json/"},
		{Key: "subClashPath", Value: "/clash/"},
	}
	return db.Transaction(func(tx *gorm.DB) error {
		for i := range settings {
			if err := tx.Where("key = ?", settings[i].Key).FirstOrCreate(&settings[i]).Error; err != nil {
				return err
			}
		}
		return nil
	})
}

// OMID OVERLAY: migrate legacy randomized routes to the fixed public Nginx routes.
func isLegacyRandomSubscriptionPath(value string) bool {
	if len(value) != 18 || value[0] != '/' || value[len(value)-1] != '/' {
		return false
	}
	for _, r := range value[1 : len(value)-1] {
		if !((r >= '0' && r <= '9') || (r >= 'a' && r <= 'z')) {
			return false
		}
	}
	return true
}

func migrateLegacyLucitySubscriptionPaths() error {
	if os.Getenv("XUI_SUB_PUBLIC_HTTPS") != "true" {
		return nil
	}
	defaults := map[string]string{
		"subPath":      "/sub/",
		"subJsonPath":  "/json/",
		"subClashPath": "/clash/",
	}
	return db.Transaction(func(tx *gorm.DB) error {
		for key, target := range defaults {
			var setting model.Setting
			if err := tx.Where("key = ?", key).First(&setting).Error; err != nil {
				if errors.Is(err, gorm.ErrRecordNotFound) {
					continue
				}
				return err
			}
			legacyDefault := strings.EqualFold(setting.Value, target) || isLegacyRandomSubscriptionPath(setting.Value)
			if legacyDefault && setting.Value != target {
				if err := tx.Model(&model.Setting{}).Where("key = ?", key).Update("value", target).Error; err != nil {
					return err
				}
			}
		}
		return nil
	})
}'''

        text = text[:match.start()] + replacement + text[match.end():]

    text = text.replace("seedRandomSubscriptionPaths()", "seedDefaultSubscriptionPaths()")
    init_start = text.find("func InitDB(")
    if init_start < 0:
        fail("db.go changed: InitDB() not found")
    if "migrateLegacyLucitySubscriptionPaths()" not in text[init_start:]:
        migration_call = (
            "\tif err := migrateLegacyLucitySubscriptionPaths(); err != nil {\n"
            "\t\treturn err\n"
            "\t}\n\n"
        )
        # Sanaei v3.11 moved first-user creation out of InitDB into
        # database.EnsureFirstUser(), called from main.go. The stable point
        # common to old and new InitDB implementations is immediately before
        # runSeeders; retain the legacy initUser anchor as a fallback.
        seeders_return = "\treturn runSeeders(isUsersEmpty)"
        if text.count(seeders_return) == 1:
            text = text.replace(seeders_return, migration_call + seeders_return, 1)
        elif text.count(seeders_return) > 1:
            fail("db.go migration call: multiple runSeeders return anchors found")
        else:
            legacy_init_user = "\tif err := initUser(); err != nil {"
            text = replace_once(
                text,
                legacy_init_user,
                migration_call + legacy_init_user,
                "db.go migration call",
            )

    if "random." not in text:
        text = re.sub(r'(?m)^\s*"github.com/mhsanaei/3x-ui/v3/internal/util/random"\n', "", text)
    path.write_text(text)

def patch_dockerinit(repo: Path) -> None:
    """Make the mtg release lookup resilient to a transient GitHub API failure."""
    path = repo / "DockerInit.sh"
    text = path.read_text()
    marker = "# OMID OVERLAY: robust mtg-multi release resolution"
    if marker in text:
        return

    old = """MTG_MULTI_VER=$(curl -sfL "https://api.github.com/repos/mhsanaei/mtg-multi/releases/latest" | sed -n 's/.*"tag_name": *"\\([^\"]*\\)".*/\\1/p' | head -n 1)
if [ -z "$MTG_MULTI_VER" ]; then
    echo "DockerInit: could not resolve the latest mtg-multi release tag" >&2
    exit 1
fi"""
    new = """# OMID OVERLAY: robust mtg-multi release resolution
# MTG_MULTI_VER may be pinned by the build environment. If GitHub's API is
# temporarily unreachable or rate-limited, use a known published release.
MTG_MULTI_VER="${MTG_MULTI_VER:-}"
if [ -z "$MTG_MULTI_VER" ]; then
    MTG_MULTI_VER=$(curl -fsSL --retry 3 --retry-delay 2 --connect-timeout 10 \\
        "https://api.github.com/repos/mhsanaei/mtg-multi/releases/latest" \\
        2>/dev/null | sed -n 's/.*"tag_name"[[:space:]]*:[[:space:]]*"\\([^\"]*\\)".*/\\1/p' | head -n 1)
fi
if [ -z "$MTG_MULTI_VER" ]; then
    echo "DockerInit: GitHub API unavailable; using fallback mtg-multi v1.16.0" >&2
    MTG_MULTI_VER="v1.16.0"
fi"""
    text = replace_once(text, old, new, "DockerInit.sh mtg-multi release lookup")
    path.write_text(text)


def patch_setting_service(repo: Path) -> None:
    path = repo / "internal/web/service/setting.go"
    text = path.read_text()
    old_paths = '''\t\tpaths := []model.Setting{
\t\t\t{Key: "subPath", Value: "/" + random.NumLower(16) + "/"},
\t\t\t{Key: "subJsonPath", Value: "/" + random.NumLower(16) + "/"},
\t\t\t{Key: "subClashPath", Value: "/" + random.NumLower(16) + "/"},
\t\t}'''
    new_paths = '''\t\tpaths := []model.Setting{
\t\t\t{Key: "subPath", Value: "/" + random.NumLower(16) + "/"},
\t\t\t{Key: "subJsonPath", Value: "/" + random.NumLower(16) + "/"},
\t\t\t{Key: "subClashPath", Value: "/" + random.NumLower(16) + "/"},
\t\t}
\t\t// OMID OVERLAY: Nginx exposes stable public routes on restricted hosts.
\t\tif os.Getenv("XUI_SUB_PUBLIC_HTTPS") == "true" {
\t\t\tpaths = []model.Setting{
\t\t\t\t{Key: "subPath", Value: "/sub/"},
\t\t\t\t{Key: "subJsonPath", Value: "/json/"},
\t\t\t\t{Key: "subClashPath", Value: "/clash/"},
\t\t\t}
\t\t}'''
    if "OMID OVERLAY: Nginx exposes stable public routes" not in text:
        text = replace_once(text, old_paths, new_paths, "setting.go ResetSettings paths")

    start = text.find("func (s *SettingService) BuildSubURIBase(host string) string {")
    if start < 0:
        fail("setting.go changed: BuildSubURIBase(host string) not found")
    next_func = text.find("\nfunc ", start + 1)
    if next_func < 0:
        next_func = len(text)
    segment = text[start:next_func]
    if 'if os.Getenv("XUI_SUB_PUBLIC_HTTPS") == "true" {' not in segment:
        needle = '\tif subDomain == "" {\n\t\tsubDomain = extractHostname(host)\n\t}\n'
        repl = needle + '\n\t// OMID OVERLAY: public HTTPS terminates at the platform proxy.\n\tif os.Getenv("XUI_SUB_PUBLIC_HTTPS") == "true" {\n\t\treturn "https://" + subDomain\n\t}\n'
        segment = replace_once(segment, needle, repl, "setting.go BuildSubURIBase")
        text = text[:start] + segment + text[next_func:]
    path.write_text(text)

def patch_telegram_urls(repo: Path) -> None:
    path = repo / "internal/web/service/tgbot/tgbot_client.go"
    text = path.read_text()
    func_start = text.find("func (t *Tgbot) buildSubscriptionURLs")
    start = text.find("\t// Gather settings to construct absolute URLs", func_start)
    end = text.find("\t// Ensure paths", start)
    if start < 0 or end < 0:
        if "OMID OVERLAY: shared public subscription URL base" in text:
            return
        fail("tgbot_client.go changed: subscription URL block anchors not found")
    block = '''\t// OMID OVERLAY: shared public subscription URL base
\tsubURI, _ := t.settingService.GetSubURI()
\tsubJsonURI, _ := t.settingService.GetSubJsonURI()
\tsubDomain, _ := t.settingService.GetSubDomain()
\tsubPath, _ := t.settingService.GetSubPath()
\tsubJsonPath, _ := t.settingService.GetSubJsonPath()
\tsubJsonEnable, _ := t.settingService.GetSubJsonEnable()

\tif subDomain == "" {
\t\tif d, err := t.settingService.GetWebDomain(); err == nil && d != "" {
\t\t\tsubDomain = d
\t\t} else {
\t\t\tsubDomain = "localhost"
\t\t}
\t}
\tbaseURL := t.settingService.BuildSubURIBase(subDomain)

'''
    text = text[:start] + block + text[end:]
    text = text.replace('fmt.Sprintf("%s://%s%s%s", scheme, host, subPath, client.SubID)',
                        'fmt.Sprintf("%s%s%s", baseURL, subPath, client.SubID)')
    text = text.replace('fmt.Sprintf("%s://%s%s%s", scheme, host, subJsonPath, client.SubID)',
                        'fmt.Sprintf("%s%s%s", baseURL, subJsonPath, client.SubID)')
    if ('fmt.Sprintf("%s://%s%s%s", scheme, host, subPath, client.SubID)' in text or
            'fmt.Sprintf("%s://%s%s%s", scheme, host, subJsonPath, client.SubID)' in text):
        fail("tgbot_client.go: an old subscription URL expression remains")
    path.write_text(text)


def main() -> None:
    if len(sys.argv) != 3:
        fail("usage: apply_omid_overlay.py OVERLAY_DIR UPSTREAM_REPO_DIR")
    overlay = Path(sys.argv[1]).resolve()
    repo = Path(sys.argv[2]).resolve()
    if not (repo / "go.mod").is_file() or not (repo / "Dockerfile").is_file():
        fail(f"not a 3x-ui source tree: {repo}")

    copy_runtime(overlay, repo)
    patch_entrypoint(repo)
    patch_dockerfile(repo)
    patch_database(repo)
    patch_dockerinit(repo)
    patch_setting_service(repo)
    patch_telegram_urls(repo)

    test_src = overlay / "overlays" / "tests" / "sub_uri_base_test.go"
    if test_src.is_file():
        shutil.copy2(test_src, repo / "internal/web/service/sub_uri_base_test.go")
    doc = overlay / "docs" / "LUCITY-REVERSE-PROXY-FA.md"
    if doc.is_file():
        shutil.copy2(doc, repo / "LUCITY-REVERSE-PROXY-FA.md")
    print(f"OMID overlay applied to {repo}")


if __name__ == "__main__":
    main()
