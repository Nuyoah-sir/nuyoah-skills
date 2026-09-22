#!/usr/bin/env bash
# =====================================================================
# solve_{{MODEL_NAME}}.sh — 模型 {{MODEL_NAME}} 构建与启动
# 由 multi-turn-eval skill Phase 2 自动生成
# Part 0: 环境检测 | Part 1: 宿主机模式 | Part 2: Docker 模式 (--docker)
# =====================================================================
set -euo pipefail

# ---- Mode detection ----
DOCKER_MODE=false
if [ "${1:-}" = "--docker" ]; then DOCKER_MODE=true; fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
OUTPUT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# Convert Git Bash /tmp paths to Windows paths for Docker commands.
# On Linux/macOS, cygpath is absent → no-op fallback.
to_win() {
    if command -v cygpath >/dev/null 2>&1; then
        cygpath -w "$1"
    else
        echo "$1"
    fi
}

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
pass() { echo "${GREEN}[OK]${NC} $*"; }
warn() { echo "${YELLOW}[WARN]${NC} $*"; }
fail() { echo "${RED}[FAIL]${NC} $*"; }

echo "========================================="
echo "  Multi-Turn Eval — solve_{{MODEL_NAME}}"
echo "  Mode: $($DOCKER_MODE && echo 'Docker' || echo 'Host')"
echo "========================================="
echo ""

# =====================================================================
# Part 0: 环境检测 (always runs)
# =====================================================================
echo "--- Part 0.1: Network ---"

if curl -s --connect-timeout 5 https://hub.docker.com >/dev/null 2>&1; then
    pass "Docker Hub reachable (international)"
    NET_TYPE="international"
elif curl -s --connect-timeout 5 https://registry.cn-hangzhou.aliyuncs.com >/dev/null 2>&1; then
    pass "Aliyun registry reachable (domestic)"
    NET_TYPE="domestic"
else
    warn "Cannot determine network type; will try both"
    NET_TYPE="unknown"
fi

{{#IF CHINA_MIRROR}}
{{MIRROR_SETUP}}
{{/IF CHINA_MIRROR}}

echo "--- Part 0.2: Language Runtimes ---"

{{LANG_CHECKS}}

echo "--- Part 0.3: Docker ---"

if ! command -v docker >/dev/null 2>&1; then
    fail "docker not found. Please install Docker Desktop."
    exit 1
fi
pass "docker CLI found"

if ! docker info >/dev/null 2>&1; then
    fail "docker daemon not running. Start Docker Desktop first."
    exit 1
fi
pass "docker daemon running"

{{DOCKER_CHECKS}}

echo "--- Part 0.4: Port Availability ---"

check_port() {
    local port="$1" name="$2"
    # Linux: ss or netstat -tln
    if command -v ss >/dev/null 2>&1; then
        if ss -tln | grep -q ":$port "; then
            warn "Port $port ($name) is in use. Free it before running."
            return 1
        fi
    # Windows Git Bash: netstat -ano
    elif netstat -ano 2>/dev/null | grep "LISTENING" | grep -q ":$port "; then
        warn "Port $port ($name) is in use. Free it before running."
        return 1
    elif netstat -tln 2>/dev/null | grep -q ":$port "; then
        warn "Port $port ($name) is in use. Free it before running."
        return 1
    fi
    pass "Port $port ($name) free"
    return 0
}

{{PORT_CHECKS}}

echo ""
echo "--- Environment check complete ---"
echo ""

# =====================================================================
# Part 1: 宿主机模式 (default)
# =====================================================================
if ! $DOCKER_MODE; then
    echo "=== Part 1: Host Mode ==="

    WORKDIR="{{WORKDIR}}"
    rm -rf "$WORKDIR"
    mkdir -p "$WORKDIR"
    cd "$WORKDIR"

{{#IF HAS_DB}}
    # ---- Database ----
    echo "[DB] Starting {{DB_CONTAINER_NAME}}..."
    docker rm -f {{DB_CONTAINER_NAME}} 2>/dev/null || true
    docker run -d --name {{DB_CONTAINER_NAME}} \
        {{DB_ENV}} \
        -p {{DB_PORT}}:{{DB_PORT}} \
        {{DB_IMAGE}}

    echo "   Waiting for database..."
    {{DB_HEALTH_CHECK}}

{{/IF HAS_DB}}
{{#IF HAS_MQ}}
    # ---- Message Queue ----
    echo "[MQ] Starting {{MQ_CONTAINER_NAME}}..."
    docker rm -f {{MQ_CONTAINER_NAME}} 2>/dev/null || true
    docker run -d --name {{MQ_CONTAINER_NAME}} -p {{MQ_PORT}}:{{MQ_PORT}} {{MQ_IMAGE}}
    echo "   Waiting for message queue..."
    sleep 5
    {{MQ_TOPIC_SETUP}}
    echo "   Message queue ready"

{{/IF HAS_MQ}}
    # ---- Write source files ----
    echo "[SRC] Writing source files..."
    # NOTE: for projects with 20+ files, prefer base64-encoded tar.gz:
    #   base64 -d << 'TARBALL_EOF' | tar -xz
    #   <base64 content>
    #   TARBALL_EOF
    {{SOURCE_FILES}}

    # ---- Build ----
    echo "[BUILD] Building..."
    {{BUILD_CMD}}

    # ---- Start app ----
    echo "[START] Starting app..."
    {{START_CMD}}

    # ---- Wait for health ----
    echo "   Waiting for health (max 120s)..."
    for i in $(seq 1 120); do
        HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" "{{HEALTH_URL}}" 2>/dev/null || echo "000")
        if echo "$HTTP_CODE" | grep -qE "^(200|401|403|404)$"; then
            echo "   [OK] Service ready (HTTP $HTTP_CODE): {{BASE_URL}}"
            curl -s "{{HEALTH_URL}}" 2>/dev/null || true
            exit 0
        fi
        sleep 1
    done

    echo "   [ERR] Service failed to start. Last 50 lines:"
    {{LOG_TAIL_CMD}}
    exit 1
fi

# =====================================================================
# Part 2: Docker 模式 (--docker)
# =====================================================================
echo "=== Part 2: Docker Mode ==="

DOCKER_BUILD_DIR="/tmp/{{DOCKER_IMAGE_NAME}}-build"
rm -rf "$DOCKER_BUILD_DIR"
mkdir -p "$DOCKER_BUILD_DIR/app"
cd "$DOCKER_BUILD_DIR"

# ---- Step 2.1: Write entrypoint.sh ----
cat > entrypoint.sh << 'ENTRYPOINT_EOF'
#!/usr/bin/env bash
set -euo pipefail

{{#IF HAS_DB}}
PG_VERSION=$(ls /usr/lib/postgresql/ 2>/dev/null | sort -n | tail -1)
if [ -z "$PG_VERSION" ]; then
    PG_VERSION=$(pg_lsclusters -h 2>/dev/null | awk 'NR>1{print $1; exit}')
fi
if [ -z "$PG_VERSION" ]; then
    echo "[entrypoint] ERROR: Cannot detect PostgreSQL version"
    exit 1
fi
echo "[entrypoint] Starting PostgreSQL (version $PG_VERSION)..."
pg_ctlcluster "$PG_VERSION" main start 2>/dev/null || {
    echo "[entrypoint] pg_ctlcluster failed, trying pg_createcluster..."
    pg_createcluster "$PG_VERSION" main 2>/dev/null || true
    pg_ctlcluster "$PG_VERSION" main start
}

for i in $(seq 1 30); do
    if su postgres -c "pg_isready -q" 2>/dev/null; then
        echo "[entrypoint] PostgreSQL ready"
        break
    fi
    [ "$i" -eq 30 ] && { echo "[entrypoint] ERROR: PostgreSQL not ready"; exit 1; }
    sleep 1
done

echo "[entrypoint] Setting up users and databases..."
su postgres -c "psql -c \"ALTER USER postgres PASSWORD 'postgres';\"" 2>/dev/null || true

{{DB_USERS_SETUP}}

{{DB_NAMES_SETUP}}

{{/IF HAS_DB}}
{{#IF HAS_MQ}}
echo "[entrypoint] Starting Redpanda (v26)..."
rpk redpanda start --smp 1 --reserve-memory 0M \
    > /var/log/redpanda.log 2>&1 &

for i in $(seq 1 30); do
    if rpk cluster health 2>/dev/null | grep -q 'Healthy'; then
        echo "[entrypoint] Redpanda ready"
        break
    fi
    [ "$i" -eq 30 ] && { echo "[entrypoint] WARNING: Redpanda may not be ready"; }
    sleep 1
done

echo "[entrypoint] Kafka(Redpanda): localhost:{{KAFKA_PORT}}"

{{/IF HAS_MQ}}
echo "[entrypoint] Environment ready. Container running."

exec tail -f /dev/null
ENTRYPOINT_EOF
chmod +x entrypoint.sh

{{#IF HAS_MQ}}
# ---- Step 2.2: Write redpanda.yaml ----
cat > redpanda.yaml << 'REDPANDA_EOF'
redpanda:
  data_directory: /var/lib/redpanda/data
  developer_mode: true
  seed_servers: []

  kafka_api:
    - address: "127.0.0.1"
      port: {{KAFKA_PORT}}
      name: kafka

  admin:
    - address: "127.0.0.1"
      port: {{ADMIN_PORT}}
      name: admin

rpk:
  kafka_api:
    brokers:
      - "127.0.0.1:{{KAFKA_PORT}}"
  admin_api:
    addresses:
      - "127.0.0.1:{{ADMIN_PORT}}"

pandaproxy: {}
schema_registry: {}
REDPANDA_EOF

{{/IF HAS_MQ}}
# ---- Step 2.3: Write source files into app/ ----
cd "$DOCKER_BUILD_DIR/app"
{{SOURCE_FILES}}
cd "$DOCKER_BUILD_DIR"

# ---- Step 2.4: Write Dockerfile ----
# Base image my-dev-tools:latest already includes:
#   JDK 21, Maven, curl, wget, jq, python3, pip, git, Go, Node.js 20,
#   gnupg, gnupg2, ca-certificates, postgresql-client, mysql-client, build-essential
# Only project-specific packages (postgresql server, redpanda) are installed here.
cat > Dockerfile << 'DOCKERFILE_EOF'
FROM my-dev-tools:latest

{{#IF CHINA_MIRROR}}
RUN mkdir -p /root/.m2 && echo '{{MAVEN_MIRROR_XML}}' > /root/.m2/settings.xml
{{/IF CHINA_MIRROR}}

{{#IF HAS_MQ}}
RUN curl -1sLf 'https://dl.redpanda.com/nzc4ZYQK3WRGd9sy/redpanda/cfg/setup/bash.deb.sh' | \
    bash -s -- '' && \
    apt-get install -y redpanda && \
    rm -rf /var/lib/apt/lists/*

RUN mkdir -p /etc/redpanda /var/lib/redpanda/data
COPY redpanda.yaml /etc/redpanda/redpanda.yaml

{{/IF HAS_MQ}}
{{#IF HAS_DB}}
RUN apt-get update && apt-get install -y postgresql && \
    rm -rf /var/lib/apt/lists/*

RUN PG_VER=$(ls /usr/lib/postgresql/ | sort -n | tail -1) && \
    pg_ctlcluster "$PG_VER" main start 2>/dev/null || true && \
    su postgres -c "psql -c \"ALTER USER postgres PASSWORD 'postgres';\"" 2>/dev/null || true && \
{{DB_INIT_COMMANDS}}    pg_ctlcluster "$PG_VER" main stop 2>/dev/null || true

{{/IF HAS_DB}}
COPY app/ /workspace/
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

WORKDIR /workspace
ENTRYPOINT ["/entrypoint.sh"]
DOCKERFILE_EOF

# ---- Step 2.5: Build image ----
echo "[docker] Building image {{DOCKER_IMAGE_NAME}}..."
docker build -t {{DOCKER_IMAGE_NAME}} "$(to_win "$DOCKER_BUILD_DIR")" 2>&1 | tail -20
pass "Image built: {{DOCKER_IMAGE_NAME}}"

# ---- Step 2.6: Run container ----
echo "[docker] Starting container {{DOCKER_CONTAINER_NAME}}..."
docker rm -f {{DOCKER_CONTAINER_NAME}} 2>/dev/null || true
docker run -d --name {{DOCKER_CONTAINER_NAME}} \
    -v maven-cache:/root/.m2/repository \
    -v npm-cache:/root/.npm \
    -v go-cache:/root/go/pkg/mod \
{{#IF HAS_DB}}    -p {{DB_PORT}}:5432 \
{{/IF HAS_DB}}{{#IF HAS_MQ}}    -p {{KAFKA_PORT}}:{{KAFKA_PORT}} \
{{/IF HAS_MQ}}    -p {{APP_PORT}}:{{APP_PORT}} \
    {{DOCKER_IMAGE_NAME}}

# ---- Step 2.7: Wait for infrastructure ----
{{#IF HAS_DB_OR_MQ}}
echo "[docker] Waiting for infrastructure (max 60s)..."
for i in $(seq 1 60); do
    READY=true
{{#IF HAS_DB}}    docker exec {{DOCKER_CONTAINER_NAME}} bash -c "su postgres -c 'pg_isready -q'" 2>/dev/null || READY=false
{{/IF HAS_DB}}
{{#IF HAS_MQ}}    docker exec {{DOCKER_CONTAINER_NAME}} bash -c "rpk cluster health 2>/dev/null | grep -q Healthy" 2>/dev/null || READY=false
{{/IF HAS_MQ}}
    if $READY; then
        echo "[docker] Infrastructure ready"
        break
    fi
    [ "$i" -eq 60 ] && { echo "[docker] ERROR: Infrastructure not ready"; docker logs {{DOCKER_CONTAINER_NAME}} | tail -30; exit 1; }
    sleep 1
done
{{/IF HAS_DB_OR_MQ}}

# ---- Step 2.8: Build app inside container ----
echo "[docker] Building app inside container..."
docker exec {{DOCKER_CONTAINER_NAME}} bash -c "cd /workspace && {{BUILD_CMD}}"

# ---- Step 2.9: Start app (detached) ----
echo "[docker] Starting app..."
docker exec -d {{DOCKER_CONTAINER_NAME}} bash -c "cd /workspace && {{START_CMD}}"

# ---- Step 2.10: Wait for app health (docker exec bypasses Windows port forwarding) ----
echo "[docker] Waiting for app to build and start (max 600s)..."
for i in $(seq 1 600); do
    HTTP_CODE=$(docker exec {{DOCKER_CONTAINER_NAME}} curl -s -o /dev/null -w "%{http_code}" "{{HEALTH_URL}}" 2>/dev/null || echo "000")
    if echo "$HTTP_CODE" | grep -qE "^(200|401|403|404)$"; then
        echo "   [OK] Service ready inside container"
        break
    fi
    [ "$i" -eq 600 ] && { echo "   [ERR] App failed to start"; docker logs {{DOCKER_CONTAINER_NAME}} | tail -30; exit 1; }
    sleep 1
done

# ---- Step 2.11: Run tests (inside container to avoid Windows port forwarding issues) ----
echo ""
echo "[docker] Running tests inside container..."
TEST_EXIT=0
TEST_SCRIPT="{{TEST_SH_PATH}}"
if [ -f "$TEST_SCRIPT" ]; then
    MSYS_NO_PATHCONV=1 docker cp "$(to_win "$TEST_SCRIPT")" "{{DOCKER_CONTAINER_NAME}}:/tmp/test.sh" 2>/dev/null || true
    docker exec {{DOCKER_CONTAINER_NAME}} bash /tmp/test.sh || TEST_EXIT=$?
else
    echo "[docker] WARNING: test.sh not found at $TEST_SCRIPT"
    TEST_EXIT=1
fi

# ---- Step 2.12: Cleanup (always runs, even on test failure) ----
echo "[docker] Stopping container {{DOCKER_CONTAINER_NAME}}..."
docker stop {{DOCKER_CONTAINER_NAME}} 2>/dev/null || true
docker rm -f {{DOCKER_CONTAINER_NAME}} 2>/dev/null || true
rm -rf "$DOCKER_BUILD_DIR"
echo "[docker] Done. Exit code: $TEST_EXIT"
exit $TEST_EXIT
