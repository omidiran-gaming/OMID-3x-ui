#!/bin/sh
# Generates Nginx listen directives and custom inbound routes at container start.
# OMID_RAILWAY_PORTS is a comma-separated list of additional public HTTP ports.
# Example: OMID_RAILWAY_PORTS="2053,8443,9443"
# PORT is always added so Railway's default domain target remains reachable.
# Ports occupied by the panel, subscription server, or loopback inbounds are
# intentionally rejected to avoid bind conflicts: 8080, 2096, and 8081-8090.

LISTENERS_OUT="${OMID_LISTENERS_OUT:-/etc/nginx/omid-listeners.conf}"
LISTENERS_TMP="${LISTENERS_OUT}.tmp"
mkdir -p "$(dirname "$LISTENERS_OUT")"
: > "$LISTENERS_TMP"

add_listener_port() {
    raw_port=$(printf '%s' "$1" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
    case "$raw_port" in
        ''|*[!0-9]*)
            echo "omid-listeners: skipped '$1' (port must be numeric)" >&2
            return
            ;;
    esac

    # Strip leading zeroes before numeric comparisons (portable POSIX shell).
    port=$(printf '%s' "$raw_port" | sed 's/^0*//')
    [ -n "$port" ] || port=0
    if [ "$port" -lt 1 ] || [ "$port" -gt 65535 ]; then
        echo "omid-listeners: skipped '$1' (port must be between 1 and 65535)" >&2
        return
    fi

    case "$port" in
        8080|2096|808[1-9]|8090)
            echo "omid-listeners: skipped '$port' (reserved by panel, subscription, or inbound service)" >&2
            return
            ;;
    esac

    directive="    listen $port;"
    if grep -Fqx "$directive" "$LISTENERS_TMP"; then
        return
    fi

    count=$(wc -l < "$LISTENERS_TMP" | tr -d ' ')
    if [ "$count" -ge 32 ]; then
        echo "omid-listeners: skipped '$port' (maximum 32 public ports)" >&2
        return
    fi

    printf '%s\n' "$directive" >> "$LISTENERS_TMP"
    echo "omid-listeners: Nginx will listen on $port" >&2
}

# Add the primary target first so it is easy to spot in detected-port lists.
PRIMARY_PORT="${PORT:-2053}"
case "$PRIMARY_PORT" in
    ''|*[!0-9]*)
        echo "omid-listeners: invalid PORT='$PRIMARY_PORT'; falling back to 2053" >&2
        PRIMARY_PORT=2053
        ;;
esac
PRIMARY_PORT=$(printf '%s' "$PRIMARY_PORT" | sed 's/^0*//')
[ -n "$PRIMARY_PORT" ] || PRIMARY_PORT=0
if [ "$PRIMARY_PORT" -lt 1 ] || [ "$PRIMARY_PORT" -gt 65535 ]; then
    echo "omid-listeners: PORT is out of range; falling back to 2053" >&2
    PRIMARY_PORT=2053
fi
case "$PRIMARY_PORT" in
    8080|2096|808[1-9]|8090)
        echo "omid-listeners: PORT=$PRIMARY_PORT is reserved; falling back to 2053" >&2
        PRIMARY_PORT=2053
        ;;
esac
add_listener_port "$PRIMARY_PORT"

# Add optional public listeners selected by the operator.
PORTS_RAW="${OMID_RAILWAY_PORTS:-$PRIMARY_PORT}"
printf '%s\n' "$PORTS_RAW" | tr ',' '\n' | while IFS= read -r item; do
    [ -n "$item" ] && add_listener_port "$item"
done

# Keep a usable default if every configured value was rejected.
if [ ! -s "$LISTENERS_TMP" ]; then
    echo "omid-listeners: no valid port configured; falling back to 2053" >&2
    printf '    listen 2053;\n' > "$LISTENERS_TMP"
fi
mv "$LISTENERS_TMP" "$LISTENERS_OUT"

# Generates /etc/nginx/omid-routes.conf from OMID_ROUTES.
#   OMID_ROUTES="/my-secret-path=8081,/api/v2/stream=8082"
# Each entry maps a custom public path to an inbound on 127.0.0.1:PORT.
# Invalid entries are skipped with a warning; nginx still starts.
OUT="${OMID_ROUTES_OUT:-/etc/nginx/omid-routes.conf}"
: > "$OUT"
[ -z "${OMID_ROUTES:-}" ] && exit 0

echo "$OMID_ROUTES" | tr ',' '\n' | while IFS= read -r entry; do
    entry=$(echo "$entry" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
    [ -z "$entry" ] && continue
    path="${entry%=*}"
    port="${entry##*=}"
    path="${path%/}"
    if ! echo "$path" | grep -Eq '^/[A-Za-z0-9._~/-]+$' || echo "$path" | grep -q '\.\.' || echo "$path" | grep -q '//'; then
        echo "omid-routes: skipped '$entry' (invalid path)" >&2; continue
    fi
    if ! echo "$port" | grep -Eq '^(808[1-9]|8090)$'; then
        echo "omid-routes: skipped '$entry' (port must be 8081-8090)" >&2; continue
    fi
    seg=$(echo "$path" | cut -d/ -f2 | tr 'A-Z' 'a-z')
    case "$seg" in
        sub|json|clash|mihomo|clash-legacy|healthz)
            echo "omid-routes: skipped '$entry' (reserved path)" >&2; continue ;;
    esac
    cat >> "$OUT" << CONF
location = $path { proxy_pass http://127.0.0.1:$port; include /etc/nginx/omid-proxy-common.conf; }
location ^~ $path/ { proxy_pass http://127.0.0.1:$port; include /etc/nginx/omid-proxy-common.conf; }
CONF
    echo "omid-routes: $path -> 127.0.0.1:$port" >&2
done
exit 0
