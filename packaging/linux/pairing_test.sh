#!/usr/bin/env bash
# CI: a Linux computer pairs with a Linux Lifetime Server by its code alone, as people do (no --server).
#   pairing_test.sh ARTIFACTS_DIR [IMAGE]       on the CI host, with Docker; IMAGE defaults to ubuntu:24.04
#
# Two containers on their own Docker network stand for two computers on one LAN. The smoke test pairs with --server,
# so the two things that broke in 1.2.1 were never run; here they are:
#  - each container's own name resolves only to 127.0.1.1, as on Debian and Ubuntu (Docker's line with the real
#    address is removed), so a client that looks for its network through its host name finds none;
#  - the server's address is a name nobody can resolve (https://lifetime.school.lan:8443), so the client has to find
#    it by local discovery (5353/udp) or the network search and use the address it answered from.
# The client enrolls with the code only, is approved, and syncs. It may find the server either way; a second client
# then enrolls with multicast blocked in its network namespace (set from the CI host with sudo, so the container gets
# no privileges), which leaves only the network search: that path is proven on its own.
set -euo pipefail
artifacts="$(cd -- "$1" && pwd)"
image="${2:-ubuntu:24.04}"
arch="$(uname -m)"
network=lifetime-pairing
server=lifetime-pairing-server
client=lifetime-pairing-client
second=lifetime-pairing-client-2
work="$(mktemp -d)"
tar -xzf "$artifacts/Lifetime-Server-Linux-$arch.tar.gz" -C "$work"
tar -xzf "$artifacts/Lifetime-Linux-$arch.tar.gz" -C "$work"
runtime="/lifetime/Lifetime-Server-Linux-$arch/LifetimeServerRuntime"
agent="/lifetime/Lifetime-Linux-$arch/LifetimeAgent"

finish() {
    status=$?
    if [[ $status != 0 ]]; then
        echo '--- server log'; docker logs "$server" 2>&1 | tail -n 50 || true
        echo '--- client log'; docker exec "$client" tail -n 50 /tmp/agent.log 2>/dev/null || true
    fi
    docker rm -f "$server" "$client" "$second" >/dev/null 2>&1 || true
    docker network rm "$network" >/dev/null 2>&1 || true
    exit "$status"
}
trap finish EXIT

# Ubuntu's /etc/hosts: this computer's name is 127.0.1.1. /etc/hosts is bind-mounted by Docker, so it is rewritten in
# place (sed -i would replace the file and fail).
ubuntu_hosts='name="$(cat /proc/sys/kernel/hostname)"
{ printf "127.0.0.1 localhost\n127.0.1.1 %s\n" "$name"; grep -v -w -e "$name" -e localhost /etc/hosts || true; } > /tmp/hosts
cat /tmp/hosts > /etc/hosts
[ "$(getent ahostsv4 "$name" | awk "{print \$1}" | sort -u)" = 127.0.1.1 ] || { echo "$name does not resolve to 127.0.1.1 only"; exit 1; }'

docker network create "$network" >/dev/null
echo "--- server ($image)"
docker run -d --name "$server" --hostname lifetime-server --network "$network" -v "$work:/lifetime:ro" "$image" \
    bash -c "$ubuntu_hosts
exec $runtime console --directory /var/lib/lifetime-server --address https://lifetime.school.lan:8443 --host 0.0.0.0 --port 8443 --admin-port 8444" >/dev/null
admin() { docker exec "$server" "$runtime" admin --directory /var/lib/lifetime-server --admin-port 8444 "$@"; }
for _ in $(seq 90); do
    admin --operation server_info --field fingerprint >/dev/null 2>&1 && break
    sleep 1
done
admin --operation server_info --field fingerprint >/dev/null || { echo 'The server did not start'; exit 1; }
[[ "$(admin --operation server_info --field discovery)" == true ]] || { echo 'The server does not announce itself (discovery)'; exit 1; }

echo "--- client ($image): pairs with the code only"
docker run -d --name "$client" --hostname pc-01 --network "$network" -v "$work:/lifetime:ro" "$image" sleep infinity >/dev/null
docker exec "$client" bash -c "$ubuntu_hosts"
code="$(admin --operation invitation --field code)"
# No --server: discovery, then the search of this computer's network. Waiting for approval is the expected state
# (exit 0); anything else fails here: invalid input (2), no server found (3), a refused code (4) or a crash.
enrolled=0
printf '%s' "$code" | docker exec -i "$client" "$agent" --directory /agent --connection-stdin --display-name pairing-client \
    || enrolled=$?
[[ "$enrolled" == 0 ]] || { echo "Pairing by code only failed: LifetimeAgent exit $enrolled (2 invalid input, 3 no server found, 4 code refused)"; exit 1; }
request="$(admin --operation pending_enrollments --field request_id)"
device="$(admin --operation pending_enrollments --field device_id)"
admin --operation approve --arguments "{\"request_id\": \"$request\"}" >/dev/null
docker exec -d "$client" bash -c "$agent --directory /agent --run > /tmp/agent.log 2>&1"
records=0
for _ in $(seq 90); do
    records="$(admin --operation device --arguments "{\"device_id\": \"$device\"}" --field record_count || echo 0)"
    [[ "$records" =~ ^[1-9] ]] && break
    sleep 2
done
[[ "$records" =~ ^[1-9] ]] || { echo 'The client did not sync'; exit 1; }
echo "client synced $records record(s)"

echo "--- second client ($image): discovery blocked, so only the network search can find the server"
docker run -d --name "$second" --hostname pc-02 --network "$network" -v "$work:/lifetime:ro" "$image" sleep infinity >/dev/null
docker exec "$second" bash -c "$ubuntu_hosts"
pid="$(docker inspect -f '{{.State.Pid}}' "$second")"
sudo nsenter -t "$pid" -n ip route add unreachable 224.0.0.0/4          # its discovery query cannot be sent
if sudo nsenter -t "$pid" -n ip route get 224.0.0.251 >/dev/null 2>&1; then echo 'Multicast is not blocked'; exit 1; fi
code="$(admin --operation invitation --field code)"
enrolled=0
printf '%s' "$code" | docker exec -i "$second" "$agent" --directory /agent --connection-stdin --display-name pairing-search \
    || enrolled=$?
[[ "$enrolled" == 0 ]] || { echo "The network search did not find the server: LifetimeAgent exit $enrolled"; exit 1; }
request="$(admin --operation pending_enrollments --field request_id)" || { echo 'No enrollment arrived from the network search'; exit 1; }
admin --operation approve --arguments "{\"request_id\": \"$request\"}" >/dev/null
echo "=== OK: paired by code only (any path) and synced $records record(s); paired again through the network search alone"
