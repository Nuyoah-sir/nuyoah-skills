#!/usr/bin/env bash
set -euo pipefail

# ---- Model A: PO Line Fulfill ----

WORKDIR="/tmp/poline_eval"
rm -rf "$WORKDIR"
mkdir -p "$WORKDIR"
cd "$WORKDIR"

# ---- Start PostgreSQL (Docker) ----
echo "[1/5] Starting PostgreSQL..."
docker rm -f poline-pg 2>/dev/null || true
docker run -d --name poline-pg \
    -e POSTGRES_USER=poline \
    -e POSTGRES_PASSWORD=poline123 \
    -e POSTGRES_DB=poline \
    -p 5432:5432 \
    postgres:16-alpine

echo "   Waiting for PostgreSQL..."
for i in $(seq 1 60); do
    if docker exec poline-pg pg_isready -U poline >/dev/null 2>&1; then
        echo "   PostgreSQL ready"; break
    fi
    [ "$i" -eq 60 ] && { echo "ERROR: PostgreSQL not ready"; exit 1; }
    sleep 2
done

# ---- Start Kafka (Docker) ----
echo "[2/5] Starting Kafka..."
docker rm -f poline-kafka 2>/dev/null || true
docker run -d --name poline-kafka \
    -p 9092:9092 \
    kafka-local:latest
echo "   Waiting for Kafka..."
sleep 5
docker exec poline-kafka /opt/kafka/bin/kafka-topics.sh --create \
    --topic procurement.po_line.lifecycle.v1 \
    --partitions 3 --replication-factor 1 \
    --bootstrap-server localhost:9092 2>/dev/null || true
echo "   Kafka ready"

# ---- Write source files ----
echo "[3/5] Writing source files..."

cat > '.gitignore' << 'EOF_a084b794'
target/
*.class
*.jar
*.log
.idea/
*.iml
.mvn/wrapper/maven-wrapper.jar
EOF_a084b794

mkdir -p ".mvn/wrapper"
cat > '.mvn/wrapper/maven-wrapper.properties' << 'EOF_de2bfeb3'
# Maven Wrapper (下载脚本)
distributionUrl=https://repo.maven.apache.org/maven2/org/apache/maven/apache-maven/3.9.6/apache-maven-3.9.6-bin.zip
wrapperUrl=https://repo.maven.apache.org/maven2/org/apache/maven/wrapper/maven-wrapper/3.2.0/maven-wrapper-3.2.0.jar
EOF_de2bfeb3

cat > 'README.md' << 'EOF_04c6e90f'
# PO Line Fulfill — 采购中台 PO 行履约状态追踪

> Java + Spring Boot + PostgreSQL + Kafka 事件驱动  
> WMS / 供应商 → Kafka → 状态机 → HTTP 查询

## 架构概览

```
管理口 (Admin)                业务方 (Buyer)
  │  POST /api/admin/events     │  GET /api/po-lines/...
  │  X-Admin-Token              │  X-Buyer-Id (仅查自己家)
  ▼                             ▼
┌──────────────────────────────────────┐
│   Spring Boot                        │
│   AdminController → KafkaTemplate    │
│   PoLineController → Service → Repo  │
│   FulfillEventConsumer → Service     │
│   PostgreSQL                         │
└──────────────────────────────────────┘
  ▲  Kafka Consumer         ▲  Kafka Producer
  │                          │
┌───────────────────────────┐
│   Kafka Topic              │
│   po-line-fulfill-events   │
└───────────────────────────┘
  ▲  SHIP / DELAY / RECEIVE / CANCEL
  │
WMS / 供应商
```

## Kafka 消费确认 (Ack) 机制

**核心原则: 数据库提交成功后再 ack Kafka 消息。**

Spring Kafka 默认模式 (`ack-mode: AUTO`) 是 listener 方法正常返回就 ack——但 `@Transactional` 的 DB 事务在方法返回后才 commit, 存在窗口:

| 场景 | AUTO 模式风险 | 本系统 (MANUAL) 保证 |
|-----|-------------|-------------------|
| handleEvent 返回后、DB commit 前进程崩溃 | Kafka 已 ack, DB 未 commit → 消息丢了, 数据没写 | 未 ack → Kafka 重投 |
| DataIntegrityViolationException (幂等 UNIQUE 冲突) | try-catch 吞掉 → 方法正常返回 → ack 但 DB 回滚了 | 不 ack → 重投后幂等查到已存在 → 正常 ack |
| 一般异常 | try-catch 吞掉 → ack 但 DB 回滚了 | 不 ack → Kafka 重投 |

**改动方案:**

1. `application.yml`: `spring.kafka.listener.ack-mode: manual`
2. Consumer 注入 `Acknowledgment` 参数, **不在 listener 里直接调 `ack()`**
3. `handleEvent()` 内注册 `TransactionSynchronization.afterCommit` 回调, 回调里执行 `ack.acknowledge()`
4. 异常路径 (UNIQUE 冲突 / 一般异常): 事务回滚 → afterCommit 不触发 → 不 ack → Kafka 重投

**乱序幂等冲突的自愈**: UNIQUE 冲突 → 事务回滚 → 不 ack → 重投 → 此时幂等表已有记录 → `findByBizId` 查到 → 返回 duplicate → 事务正常 commit → afterCommit ack → 完成。

## 幂等机制

Kafka consumer rebalance / 重放会导致同一事件重复到达。**已收货的行如果再记一次入库，qty 就对不上账。**

本系统按 **全局业务单号 `bizId`** 做幂等:

1. Consumer 收到事件 → 先查 `idempotent_event` 表
2. **bizId 已存在** → 重复到达, 返回第一次处理结果, PO 行不动
3. **bizId 不存在** → **同一笔事务内**:
   - 先 INSERT 幂等记录 (占位, 防止并发进入)
   - 再写 `po_line_event` 日志
   - 再改 `po_line` 状态
4. 事务提交 → 幂等记录和 PO 行变更原子落地

**不存在「先改行再补表」的中间态**: 如果幂等 INSERT 和改 PO 行之间挂了, 整笔事务回滚, PO 行不会半生效。并发场景下, 两个线程同时 INSERT 幂等记录 → `biz_id UNIQUE` 冲突 → 第二个线程事务回滚 → PO 行不变。

第一次处理结果和重复到达都能查: `idempotent_event` 表记录了 `result` + `created_at`。

### bizId 语义

`bizId` 是 WMS / 供应商回传的事件唯一标识, 格式建议: `{来源}-{事件类型}-{时间戳}-{序号}`

示例: `WMS-SHIP-20260623-001`, `SUP-RECEIVE-20260623-050`

管理口 POST 事件时必须带 `bizId`; 如果不带会返回 400 校验错误。

## 种子数据

两家采购方, 各一条 **PLACED** 行:

| po_number    | line | buyer_id      | sku      | qty_ordered | supplier_id |
|-------------|------|---------------|----------|-------------|-------------|
| PO-2026-001 | 1    | BUYER-ACME    | SKU-A100 | 100         | SUP-001     |
| PO-2026-002 | 1    | BUYER-GLOBEX  | SKU-C300 | 50          | SUP-001     |

## 状态流转

| 事件类型   | Kafka eventType | 行状态变更            |
|-----------|-----------------|---------------------|
| 发运       | `SHIP`          | → `SHIPPED`         |
| 延误       | `DELAY`         | → `DELAYED`         |
| 入库       | `RECEIVE`       | → `PARTIAL_RCVD` 或 `RECEIVED` |
| 行取消     | `CANCEL`        | → `CANCELLED`       |

初始 `PLACED`; CANCELLED 后忽略后续事件.

## 鉴权规则

| 端口         | 认证头           | 规则                          |
|-------------|-----------------|-------------------------------|
| `/api/admin/**` | `X-Admin-Token` | token 必须匹配配置 `admin.token` (默认 `poline-admin-secret`) |
| `/api/po-lines/**` | `X-Buyer-Id` | 必须非空; 只返回属于此采购方的行; 别家的行 → 404 / 空列表 |

**别家来查 → 当他不存在, 不露权限问题**: 返回 404 (单行) 或空列表 (集合), 不返回 403.

## 统一错误格式

所有校验/鉴权失败都返回 JSON, 不弹框架默认页面:

```json
{
  "status": 401,
  "error": "Unauthorized",
  "message": "管理口需要有效的 X-Admin-Token",
  "path": "/api/admin/events",
  "timestamp": "2026-06-23T18:30:00"
}
```

| 场景                     | HTTP 状态 | message 示例                  |
|-------------------------|----------|------------------------------|
| 缺/错 X-Admin-Token      | 401      | 管理口需要有效的 X-Admin-Token   |
| 缺/空 X-Buyer-Id         | 401      | 业务查询需要 X-Buyer-Id 头      |
| 请求体缺少 bizId          | 400      | bizId: 不能为空               |
| 请求体校验失败            | 400      | poNumber: 不能为空; ...        |

## 构建 & 发布

```bash
# 编译打包
mvn package -DskipTests

# 产物: target/po-line-fulfill-1.0.0.jar
```

部署时只需 JDK 17 + jar 文件 + 外部 PG 和 Kafka/Redpanda, 不需 Maven 环境:

```bash
java -jar po-line-fulfill-1.0.0.jar \
  --spring.datasource.url=jdbc:postgresql://PROD_PG:5432/poline \
  --spring.kafka.bootstrap-servers=PROD_KAFKA:9092 \
  --admin.token=生产token
```

## 快速启动 (本地)

```bash
chmod +x start.sh
./start.sh
```

`start.sh` 依次: 启动 Docker PG → 启动 Docker Redpanda → 创建 topic → `mvn package` → `java -jar` 启动服务。

## HTTP 自测

服务启动后, 跑自测脚本验证核心语义:

```bash
chmod +x self-test.sh
./self-test.sh
```

自测覆盖 4 条场景, 全部通过输出 `🎉 全部通过!`, 否则报告失败项:

| # | 场景 | 验证点 |
|---|------|-------|
| 1 | **同 bizId 重放不重复改状态** | 同一 bizId 的 SHIP/RECEIVE 重放 → 状态不变, qty 不叠加 |
| 2 | **旧事件乱序不回退新状态** | 行已 RECEIVED, 乱序 SHIP/DELAY 晚到 → 状态保持 RECEIVED |
| 3 | **非法状态迁移被拒** | CANCELLED 后再来 SHIP/RECEIVE → 状态/qty 不变 |
| 4 | **延误→恢复在途→收货合法链** | PLACED → DELAYED → SHIPPED → PARTIAL_RCVD → RECEIVED, qty 累加正确 |

每条测试前自动重置种子数据 (清幂等表 + 回退种子行到 PLACED/qtyReceived=0), 保证独立可重复。

## 手动启动

### PostgreSQL

```bash
docker run -d --name poline-postgres \
  -e POSTGRES_USER=poline \
  -e POSTGRES_PASSWORD=poline123 \
  -e POSTGRES_DB=poline \
  -p 5432:5432 \
  postgres:16-alpine
```

### Kafka / Redpanda

```bash
docker run -d --name poline-redpanda \
  -p 9092:9092 \
  redpanda-data/redpanda:latest \
  redpanda start --mode dev --smp 1 --memory 256M \
  --overprovisioned --node-id 0 \
  --kafka-addr internal:0.0.0.0,external:0.0.0.0 \
  --advertise-kafka-addr internal://localhost:9092,external://localhost:9092

docker exec poline-redpanda \
  rpk topic create po-line-fulfill-events \
  --partitions 3 --replication-factor 1
```

### 编译 & 运行

```bash
mvn package -DskipTests
java -jar target/po-line-fulfill-1.0.0.jar
```

## 连接信息 (默认)

| 组件       | 地址               | 用户/密码            |
|-----------|-------------------|--------------------|
| PostgreSQL | `localhost:5432`  | `poline / poline123` |
| Kafka     | `localhost:9092`  | 无认证               |
| Admin Token | 配置项 `admin.token` | `poline-admin-secret` |

## Kafka 消息格式

Topic: `po-line-fulfill-events`

```json
{
  "bizId":      "WMS-SHIP-20260623-001",
  "poNumber":   "PO-2026-001",
  "lineNumber": 1,
  "eventType":  "SHIP",
  "qty":        null,
  "reason":     null,
  "source":     "WMS"
}
```

- **`bizId`** (必填): 全局业务单号, 幂等键. WMS/供应商回传的事件唯一标识
- `eventType`: `SHIP` | `DELAY` | `RECEIVE` | `CANCEL`
- `qty`: 仅 `RECEIVE` 时必填, 本次入库数量
- `reason`: `DELAY` / `CANCEL` 原因
- `source`: `WMS` 或 `SUPPLIER`

## REST API

### 管理口 — 发送测试事件 (需 X-Admin-Token)

| 方法 | 路路                  | 说明              |
|-----|----------------------|-------------------|
| POST | `/api/admin/events` | 往 Kafka 丢测试事件 |

```bash
curl -X POST http://localhost:8080/api/admin/events \
  -H 'Content-Type: application/json' \
  -H 'X-Admin-Token: poline-admin-secret' \
  -d '{
    "bizId": "WMS-SHIP-20260623-001",
    "poNumber": "PO-2026-001",
    "lineNumber": 1,
    "eventType": "SHIP",
    "source": "WMS"
  }'
```

**bizId 必填**, 否则 400. 建议格式: `{来源}-{事件类型}-{日期}-{序号}`

### 业务口 — 查 PO 行状态 (需 X-Buyer-Id)

| 方法 | 路路                                      | 说明            |
|-----|------------------------------------------|----------------|
| GET | `/api/po-lines/{poNumber}/{lineNumber}`   | 查某行 (别家 → 404) |
| GET | `/api/po-lines/{poNumber}`                | 查某 PO 下自己的行 |
| GET | `/api/po-lines/status/{status}`           | 按状态筛选自己的行 |

```bash
# BUYER-ACME 查自己的行
curl -H 'X-Buyer-Id: BUYER-ACME' \
  http://localhost:8080/api/po-lines/PO-2026-001/1

# BUYER-ACME 查别家的行 → 404
curl -H 'X-Buyer-Id: BUYER-ACME' \
  http://localhost:8080/api/po-lines/PO-2026-002/1
```

响应示例:

```json
{
  "poNumber":   "PO-2026-001",
  "lineNumber": 1,
  "status":     "SHIPPED",
  "qtyOrdered": 100,
  "qtyReceived": 0,
  "sku":        "SKU-A100",
  "supplierId": "SUP-001",
  "buyerId":    "BUYER-ACME",
  "updatedAt":  "2026-06-23T18:30:00"
}
```

## 完整演练 (含幂等验证)

```bash
# 1. 启动
./start.sh

# 2. BUYER-ACME 查初始状态 → PLACED
curl -H 'X-Buyer-Id: BUYER-ACME' \
  http://localhost:8080/api/po-lines/PO-2026-001/1

# 3. 发运事件 (bizId: WMS-SHIP-001)
curl -X POST http://localhost:8080/api/admin/events \
  -H 'Content-Type: application/json' \
  -H 'X-Admin-Token: poline-admin-secret' \
  -d '{"bizId":"WMS-SHIP-001","poNumber":"PO-2026-001","lineNumber":1,"eventType":"SHIP","source":"WMS"}'

# 4. 再查 → SHIPPED
curl -H 'X-Buyer-Id: BUYER-ACME' \
  http://localhost:8080/api/po-lines/PO-2026-001/1

# 5. 重复发运 (同一 bizId) → 幂等拦截, PO 行不变
curl -X POST http://localhost:8080/api/admin/events \
  -H 'Content-Type: application/json' \
  -H 'X-Admin-Token: poline-admin-secret' \
  -d '{"bizId":"WMS-SHIP-001","poNumber":"PO-2026-001","lineNumber":1,"eventType":"SHIP","source":"WMS"}'
# consumer log: 幂等拦截: bizId=WMS-SHIP-001 已处理过, 结果=SHIPPED, 本次忽略

# 6. 入库 50 (bizId: WMS-RCV-001)
curl -X POST http://localhost:8080/api/admin/events \
  -H 'Content-Type: application/json' \
  -H 'X-Admin-Token: poline-admin-secret' \
  -d '{"bizId":"WMS-RCV-001","poNumber":"PO-2026-001","lineNumber":1,"eventType":"RECEIVE","qty":50,"source":"WMS"}'
# → PARTIAL_RCVD

# 7. 重复入库 50 (同一 bizId) → 幂等拦截, qty 不再累加
curl -X POST http://localhost:8080/api/admin/events \
  -H 'Content-Type: application/json' \
  -H 'X-Admin-Token: poline-admin-secret' \
  -d '{"bizId":"WMS-RCV-001","poNumber":"PO-2026-001","lineNumber":1,"eventType":"RECEIVE","qty":50,"source":"WMS"}'
# consumer log: 幂等拦截, qty 仍然是 50, 不是 100!

# 8. 入库剩余 50 (新 bizId: WMS-RCV-002) → RECEIVED
curl -X POST http://localhost:8080/api/admin/events \
  -H 'Content-Type: application/json' \
  -H 'X-Admin-Token: poline-admin-secret' \
  -d '{"bizId":"WMS-RCV-002","poNumber":"PO-2026-001","lineNumber":1,"eventType":"RECEIVE","qty":50,"source":"WMS"}'

# 9. 查幂等记录
docker exec -it poline-postgres psql -U poline -d poline \
  -c "SELECT biz_id, event_type, result, created_at FROM idempotent_event ORDER BY created_at;"
```

## 数据库

Flyway 管理, 三张表:

- **po_line**: PK `(po_number, line_number)`, 含 `buyer_id` 列做采购方隔离
- **po_line_event**: 事件日志, `payload` JSONB
- **idempotent_event**: 幂等记录, PK `biz_id` (UNIQUE), 记录每次事件第一次处理结果

```bash
docker exec -it poline-postgres psql -U poline -d poline
```

```sql
SELECT * FROM po_line;
SELECT * FROM po_line_event ORDER BY id DESC LIMIT 10;
SELECT biz_id, event_type, result, created_at FROM idempotent_event ORDER BY created_at;
```

## 项目结构

```
src/main/java/com/poline/
  PoLineFulfillApplication.java
  model/
    LineStatus.java, EventType.java, PoLine.java, PoLineId.java
    PoLineEvent.java, IdempotentEvent.java
  dto/
    FulfillEvent.java             # Kafka 消息体 (含 bizId + @Valid)
    PoLineStatusResponse.java     # HTTP 响应 (含 buyerId)
    ApiError.java                 # 统一错误响应
    IdempotentResult.java         # 幂等处理结果 DTO
  repository/
    PoLineRepository.java         # 含 buyerId 过滤查询
    PoLineEventRepository.java
    IdempotentEventRepository.java # 幂等记录 + updateResult
  service/
    PoLineService.java            # 幂等状态机 + buyerId 过滤 + sendTestEvent
  consumer/
    FulfillEventConsumer.java     # Kafka @KafkaListener (含幂等回显)
  controller/
    PoLineController.java         # 业务口 (X-Buyer-Id 隔离)
    AdminController.java          # 管理口 (X-Admin-Token)
  exception/
    GlobalExceptionHandler.java   # 统一 JSON 错误
    AuthException.java            # 鉴权异常
  web/
    AdminAuthInterceptor.java     # 管理口 token 验证
    AdminTokenProvider.java       # token 校验逻辑
    BuyerAuthInterceptor.java     # 业务口 buyerId 提取
  config/
    KafkaConfig.java
    WebMvcConfig.java             # 拦截器注册
src/main/resources/
  application.yml
  db/migration/
    V1__create_po_line_tables.sql
    V3__add_buyer_id_and_reseed.sql
    V4__create_idempotent_event.sql
start.sh
self-test.sh
```

## 清理

```bash
docker stop poline-postgres poline-redpanda
docker rm  poline-postgres poline-redpanda
```
EOF_04c6e90f

cat > 'pom.xml' << 'EOF_600376df'
<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0"
         xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
         xsi:schemaLocation="http://maven.apache.org/POM/4.0.0
         https://maven.apache.org/xsd/maven-4.0.0.xsd">
    <modelVersion>4.0.0</modelVersion>

    <parent>
        <groupId>org.springframework.boot</groupId>
        <artifactId>spring-boot-starter-parent</artifactId>
        <version>3.3.6</version>
        <relativePath/>
    </parent>

    <groupId>com.poline</groupId>
    <artifactId>po-line-fulfill</artifactId>
    <version>1.0.0</version>
    <name>PO Line Fulfill</name>
    <description>采购中台 PO 行履约状态追踪 — Kafka 事件驱动</description>

    <properties>
        <java.version>17</java.version>
    </properties>

    <dependencies>
        <!-- Web -->
        <dependency>
            <groupId>org.springframework.boot</groupId>
            <artifactId>spring-boot-starter-web</artifactId>
        </dependency>

        <!-- JPA + PostgreSQL -->
        <dependency>
            <groupId>org.springframework.boot</groupId>
            <artifactId>spring-boot-starter-data-jpa</artifactId>
        </dependency>
        <dependency>
            <groupId>org.postgresql</groupId>
            <artifactId>postgresql</artifactId>
            <scope>runtime</scope>
        </dependency>

        <!-- Flyway -->
        <dependency>
            <groupId>org.flywaydb</groupId>
            <artifactId>flyway-core</artifactId>
        </dependency>
        <dependency>
            <groupId>org.flywaydb</groupId>
            <artifactId>flyway-database-postgresql</artifactId>
        </dependency>

        <!-- Kafka -->
        <dependency>
            <groupId>org.springframework.kafka</groupId>
            <artifactId>spring-kafka</artifactId>
        </dependency>

        <!-- Validation -->
        <dependency>
            <groupId>org.springframework.boot</groupId>
            <artifactId>spring-boot-starter-validation</artifactId>
        </dependency>

        <!-- Lombok -->
        <dependency>
            <groupId>org.projectlombok</groupId>
            <artifactId>lombok</artifactId>
            <optional>true</optional>
        </dependency>

        <!-- Test -->
        <dependency>
            <groupId>org.springframework.boot</groupId>
            <artifactId>spring-boot-starter-test</artifactId>
            <scope>test</scope>
        </dependency>
        <dependency>
            <groupId>org.springframework.kafka</groupId>
            <artifactId>spring-kafka-test</artifactId>
            <scope>test</scope>
        </dependency>
    </dependencies>

    <build>
        <plugins>
            <plugin>
                <groupId>org.springframework.boot</groupId>
                <artifactId>spring-boot-maven-plugin</artifactId>
                <configuration>
                    <excludes>
                        <exclude>
                            <groupId>org.projectlombok</groupId>
                            <artifactId>lombok</artifactId>
                        </exclude>
                    </excludes>
                </configuration>
            </plugin>
        </plugins>
    </build>
</project>
EOF_600376df

cat > 'self-test.sh' << 'EOF_505c1432'
#!/usr/bin/env bash
set -euo pipefail

# ──────────────────────────────────────────────
# PO Line Fulfill — HTTP 自测脚本
# 前置: 服务已启动 (start.sh), PG + Redpanda 运行中
# 用法: chmod +x self-test.sh && ./self-test.sh
# ──────────────────────────────────────────────

BASE="http://localhost:8080"
ADMIN_TOKEN="poline-admin-secret"
BUYER="BUYER-GLOBEX"
PO="PO-2026-002"
LINE=1
PASS=0
FAIL=0

# ── helper ──

send_event() {
  local bizId="$1" eventType="$2" qty="${3:-}"
  local body="{\"bizId\":\"$bizId\",\"poNumber\":\"$PO\",\"lineNumber\":$LINE,\"eventType\":\"$eventType\",\"source\":\"SELFTEST\""
  if [ -n "$qty" ]; then body="$body,\"qty\":$qty"; fi
  body="$body}"

  curl -s -X POST "$BASE/api/admin/events" \
    -H 'Content-Type: application/json' \
    -H "X-Admin-Token: $ADMIN_TOKEN" \
    -d "$body" > /dev/null
}

query_status() {
  curl -s "$BASE/api/po-lines/$PO/$LINE" \
    -H "X-Buyer-Id: $BUYER" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['status']);" 2>/dev/null \
    || curl -s "$BASE/api/po-lines/$PO/$LINE" \
       -H "X-Buyer-Id: $BUYER" | jq -r '.status' 2>/dev/null
}

query_qty_received() {
  curl -s "$BASE/api/po-lines/$PO/$LINE" \
    -H "X-Buyer-Id: $BUYER" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['qtyReceived']);" 2>/dev/null \
    || curl -s "$BASE/api/po-lines/$PO/$LINE" \
       -H "X-Buyer-Id: $BUYER" | jq -r '.qtyReceived' 2>/dev/null
}

# 等待 Kafka 消费端处理完 (异步, 需等一小段时间)
wait_for() {
  local expected="$1" max_wait="${2:-5}" waited=0
  while [ "$waited" -lt "$max_wait" ]; do
    local actual
    actual=$(query_status)
    if [ "$actual" = "$expected" ]; then return 0; fi
    sleep 1
    waited=$((waited + 1))
  done
  return 1
}

assert_status() {
  local label="$1" expected="$2" actual
  actual=$(query_status)
  if [ "$actual" = "$expected" ]; then
    echo "  ✅ $label: status=$actual (expected=$expected)"
    PASS=$((PASS + 1))
  else
    echo "  ❌ $label: status=$actual (expected=$expected)"
    FAIL=$((FAIL + 1))
  fi
}

assert_qty() {
  local label="$1" expected="$2" actual
  actual=$(query_qty_received)
  if [ "$actual" = "$expected" ]; then
    echo "  ✅ $label: qtyReceived=$actual (expected=$expected)"
    PASS=$((PASS + 1))
  else
    echo "  ❌ $label: qtyReceived=$actual (expected=$expected)"
    FAIL=$((FAIL + 1))
  fi
}

# ── 重置种子数据 ──
# 每次 self-test 前重置, 保证起点一致
reset_seed() {
  echo "  重置种子数据..."
  docker exec poline-postgres psql -U poline -d poline -c "
    DELETE FROM idempotent_event;
    DELETE FROM po_line_event WHERE po_number='$PO';
    UPDATE po_line SET status='PLACED', qty_received=0, updated_at=NOW() WHERE po_number='$PO' AND line_number=$LINE;
  " > /dev/null 2>&1
}

# ════════════════════════════════════════════════
#  TEST 1: 同 bizId 重放不重复改状态
# ════════════════════════════════════════════════
echo ""
echo "=== TEST 1: 同 bizId 重放不重复改状态 ==="
reset_seed

send_event "ST1-SHIP-001" "SHIP"
sleep 2
assert_status "首次 SHIP" "SHIPPED"

# 重放同一 bizId
send_event "ST1-SHIP-001" "SHIP"
sleep 2
assert_status "重放同一 bizId" "SHIPPED"

# 入库 25, 再重放同一 bizId — qty 不应叠加
send_event "ST1-RCV-001" "RECEIVE" 25
sleep 2
assert_status "首次 RECEIVE 25" "PARTIAL_RCVD"
assert_qty  "首次 RECEIVE qty" 25

send_event "ST1-RCV-001" "RECEIVE" 25
sleep 2
assert_qty  "重放 RECEIVE qty 不叠加" 25

echo ""

# ════════════════════════════════════════════════
#  TEST 2: 旧事件乱序不把新状态打回
# ════════════════════════════════════════════════
echo "=== TEST 2: 旧事件乱序不把新状态打回 ==="
reset_seed

# 推行到 RECEIVED: SHIP → RECEIVE(全量 50)
send_event "ST2-SHIP-001" "SHIP"
sleep 2
send_event "ST2-RCV-FULL" "RECEIVE" 50
sleep 2
assert_status "行已全量收货" "RECEIVED"

# 乱序: 发运晚到 → 不应把 RECEIVED 打回 SHIPPED
send_event "ST2-SHIP-LATE" "SHIP"
sleep 2
assert_status "乱序 SHIP 不回退状态" "RECEIVED"

# 乱序: 延误晚到 → 同理
send_event "ST2-DELAY-LATE" "DELAY"
sleep 2
assert_status "乱序 DELAY 不回退状态" "RECEIVED"

echo ""

# ════════════════════════════════════════════════
#  TEST 3: 非法状态迁移被拒 (CANCELLED 后来事件)
# ════════════════════════════════════════════════
echo "=== TEST 3: 非法状态迁移被拒 ==="
reset_seed

send_event "ST3-CANCEL-001" "CANCEL"
sleep 2
assert_status "行取消" "CANCELLED"

# CANCELLED 后来的 SHIP → 应被忽略, 状态不变
send_event "ST3-SHIP-AFTER-CANCEL" "SHIP"
sleep 2
assert_status "CANCELLED+SHIP 仍 CANCELLED" "CANCELLED"

# CANCELLED 后来的 RECEIVE → 同理
send_event "ST3-RCV-AFTER-CANCEL" "RECEIVE" 10
sleep 2
assert_status "CANCELLED+RECEIVE 仍 CANCELLED" "CANCELLED"

assert_qty  "CANCELLED+RECEIVE qty 不变" 0

echo ""

# ════════════════════════════════════════════════
#  TEST 4: 延误→恢复在途→收货 (合法链)
# ════════════════════════════════════════════════
echo "=== TEST 4: 延误→恢复在途→收货 ==="
reset_seed

# PLACED → DELAYED
send_event "ST4-DELAY-001" "DELAY"
sleep 2
assert_status "延误" "DELAYED"

# DELAYED → SHIPPED (同级切换, phase 2→2, canTransitionTo 允许)
send_event "ST4-SHIP-RECOVER" "SHIP"
sleep 2
assert_status "恢复在途" "SHIPPED"

# SHIPPED → PARTIAL_RCVD (部分入库 30)
send_event "ST4-RCV-001" "RECEIVE" 30
sleep 2
assert_status "部分收货" "PARTIAL_RCVD"
assert_qty  "部分收货 qty" 30

# PARTIAL_RCVD → RECEIVED (剩余入库 20)
send_event "ST4-RCV-002" "RECEIVE" 20
sleep 2
assert_status "全量收货" "RECEIVED"
assert_qty  "全量收货 qty" 50

echo ""

# ── 汇总 ──
echo "═══════════════════════════════════════════"
TOTAL=$((PASS + FAIL))
echo "自测完成: $PASS/$TOTAL 通过, $FAIL 失败"
if [ "$FAIL" -eq 0 ]; then
  echo "🎉 全部通过!"
else
  echo "⚠️  有 $FAIL 项失败, 请检查服务日志"
fi
echo "═══════════════════════════════════════════"

# ── 验证幂等记录 ──
echo ""
echo "幂等记录:"
docker exec poline-postgres psql -U poline -d poline \
  -c "SELECT biz_id, event_type, result FROM idempotent_event WHERE po_number='$PO' ORDER BY created_at;" 2>/dev/null

exit $FAIL
EOF_505c1432

mkdir -p "src/main/java/com/poline"
cat > 'src/main/java/com/poline/PoLineFulfillApplication.java' << 'EOF_c7e15ba9'
package com.poline;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;

@SpringBootApplication
public class PoLineFulfillApplication {
    public static void main(String[] args) {
        SpringApplication.run(PoLineFulfillApplication.class, args);
    }
}
EOF_c7e15ba9

mkdir -p "src/main/java/com/poline/config"
cat > 'src/main/java/com/poline/config/KafkaConfig.java' << 'EOF_17f8d848'
package com.poline.config;

import org.springframework.context.annotation.Configuration;
import org.springframework.kafka.support.converter.JsonMessageConverter;

@Configuration
public class KafkaConfig {

    /**
     * 让 Spring Kafka 用 JSON 反序列化消息值
     */
    // Spring Boot 自动配置 MappingJackson2MessageConverter when spring.kafka.consumer.value-deserializer
    // = org.springframework.kafka.support.serializer.JsonDeserializer, 这里不需要额外 Bean
}
EOF_17f8d848

mkdir -p "src/main/java/com/poline/config"
cat > 'src/main/java/com/poline/config/WebMvcConfig.java' << 'EOF_56dfcd27'
package com.poline.config;

import com.poline.web.AdminAuthInterceptor;
import com.poline.web.BuyerAuthInterceptor;
import lombok.RequiredArgsConstructor;
import org.springframework.context.annotation.Configuration;
import org.springframework.web.servlet.config.annotation.InterceptorRegistry;
import org.springframework.web.servlet.config.annotation.WebMvcConfigurer;

@Configuration
@RequiredArgsConstructor
public class WebMvcConfig implements WebMvcConfigurer {

    private final AdminAuthInterceptor adminAuthInterceptor;
    private final BuyerAuthInterceptor buyerAuthInterceptor;

    @Override
    public void addInterceptors(InterceptorRegistry registry) {
        // 管理口: /api/admin/** → 需要 X-Admin-Token
        registry.addInterceptor(adminAuthInterceptor)
                .addPathPatterns("/api/admin/**");

        // 业务口: /api/po-lines/** → 需要 X-Buyer-Id
        registry.addInterceptor(buyerAuthInterceptor)
                .addPathPatterns("/api/po-lines/**");
    }
}
EOF_56dfcd27

mkdir -p "src/main/java/com/poline/consumer"
cat > 'src/main/java/com/poline/consumer/FulfillEventConsumer.java' << 'EOF_91566fd4'
package com.poline.consumer;

import com.poline.dto.FulfillEvent;
import com.poline.dto.IdempotentResult;
import com.poline.service.PoLineService;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.kafka.annotation.KafkaListener;
import org.springframework.kafka.support.Acknowledgment;
import org.springframework.stereotype.Component;

/**
 * Kafka 消费者 — 手动 ack, DB 事务提交后才确认.
 *
 * 设计要点:
 * 1. ack-mode=manual → listener 必须接收 Acknowledgment 参数, 调用 ack() 才确认消费
 * 2. handleEvent() 是 @Transactional — DB 事务在方法返回后才 commit
 * 3. 在 handleEvent() 内注册 TransactionSynchronization.afterCommit 回调, 回调里调 ack()
 * 4. 保证: 只有 DB commit 成功后才 ack → 进程半路挂不会丢消息 (未 ack → Kafka 重投)
 *
 * 异常处理:
 * - DataIntegrityViolationException (幂等 UNIQUE 冲突): 事务回滚, 不 ack → 重投后幂等查到已存在 → 返回 duplicate → 正常 ack
 * - 其他异常: 事务回滚, 不 ack → Kafka 重投
 * - 正常业务拦截 (重复/乱序/已取消): 事务正常 commit → afterCommit ack → 消息确认
 */
@Slf4j
@Component
@RequiredArgsConstructor
public class FulfillEventConsumer {

    private final PoLineService poLineService;

    @KafkaListener(
        topics = "${kafka.topic.fulfill-events:po-line-fulfill-events}",
        groupId = "${spring.kafka.consumer.group-id:po-line-fulfill-service}"
    )
    public void onEvent(FulfillEvent event, Acknowledgment ack) {
        log.info("收到 Kafka 事件: eventType={}, poNumber={}, lineNumber={}, bizId={}, source={}",
                event.getEventType(), event.getPoNumber(), event.getLineNumber(),
                event.getBizId(), event.getSource());

        try {
            IdempotentResult result = poLineService.handleEvent(event, ack);
            if (result.isDuplicate()) {
                log.info("幂等拦截: bizId={}, 重复到达, 第一次结果={}, 第一次时间={}",
                        event.getBizId(), result.getResult(), result.getFirstCreatedAt());
            } else {
                log.info("首次处理: bizId={}, 结果={}", event.getBizId(), result.getResult());
            }
            // ack 在 handleEvent 内的 afterCommit 回调里执行 — 这里不需要再调
        } catch (DataIntegrityViolationException e) {
            // biz_id UNIQUE 冲突 → 并发幂等, 事务回滚, 不 ack
            // 下次重投: 幂等查到已存在 → 返回 duplicate → 正常 commit → ack
            log.info("并发幂等拦截: bizId={}, UNIQUE 冲突, 事务回滚, 下次重投会走重复路径",
                    event.getBizId());
            // 不调 ack → Kafka 会重投这条消息
        } catch (Exception e) {
            // 处理失败, 事务回滚, 不 ack → Kafka 重投
            log.error("处理事件失败: bizId={}, 事务回滚, 等待重投", event.getBizId(), e);
            // 不调 ack → Kafka 会重投这条消息
        }
    }
}
EOF_91566fd4

mkdir -p "src/main/java/com/poline/controller"
cat > 'src/main/java/com/poline/controller/AdminController.java' << 'EOF_d0b98752'
package com.poline.controller;

import com.poline.dto.FulfillEvent;
import com.poline.service.PoLineService;
import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.*;

/**
 * 管理口 — 需要 X-Admin-Token, 往 Kafka 丢测试事件
 */
@RestController
@RequestMapping("/api/admin")
@RequiredArgsConstructor
public class AdminController {

    private final PoLineService poLineService;

    @PostMapping("/events")
    public FulfillEvent sendEvent(@Valid @RequestBody FulfillEvent event) {
        return poLineService.sendTestEvent(event);
    }
}
EOF_d0b98752

mkdir -p "src/main/java/com/poline/controller"
cat > 'src/main/java/com/poline/controller/PoLineController.java' << 'EOF_04789853'
package com.poline.controller;

import com.poline.dto.PoLineStatusResponse;
import com.poline.model.LineStatus;
import com.poline.service.PoLineService;
import jakarta.servlet.http.HttpServletRequest;
import lombok.RequiredArgsConstructor;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.util.List;

/**
 * 业务方查 PO 行状态 — 必须带 X-Buyer-Id, 只能查自己家的行
 */
@RestController
@RequestMapping("/api/po-lines")
@RequiredArgsConstructor
public class PoLineController {

    private final PoLineService poLineService;

    /** 查某一行状态 (别家的行 → 404) */
    @GetMapping("/{poNumber}/{lineNumber}")
    public ResponseEntity<PoLineStatusResponse> getByLine(
            @PathVariable String poNumber,
            @PathVariable Integer lineNumber,
            HttpServletRequest req) {
        String buyerId = (String) req.getAttribute("buyerId");
        return poLineService.getStatus(poNumber, lineNumber, buyerId)
                .map(ResponseEntity::ok)
                .orElse(ResponseEntity.notFound().build());
    }

    /** 查某 PO 下所有行 (只返回自己家的) */
    @GetMapping("/{poNumber}")
    public List<PoLineStatusResponse> getByPo(
            @PathVariable String poNumber,
            HttpServletRequest req) {
        String buyerId = (String) req.getAttribute("buyerId");
        return poLineService.getByPo(poNumber, buyerId);
    }

    /** 按状态筛选 (只返回自己家的) */
    @GetMapping("/status/{status}")
    public List<PoLineStatusResponse> getByStatus(
            @PathVariable LineStatus status,
            HttpServletRequest req) {
        String buyerId = (String) req.getAttribute("buyerId");
        return poLineService.getByStatus(status, buyerId);
    }
}
EOF_04789853

mkdir -p "src/main/java/com/poline/dto"
cat > 'src/main/java/com/poline/dto/ApiError.java' << 'EOF_8cee8d8c'
package com.poline.dto;

import com.fasterxml.jackson.annotation.JsonInclude;
import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.time.LocalDateTime;

/**
 * 统一 JSON 错误响应格式
 */
@Data @Builder @NoArgsConstructor @AllArgsConstructor
@JsonInclude(JsonInclude.Include.NON_NULL)
public class ApiError {
    private int status;
    private String error;
    private String message;
    private String path;
    private LocalDateTime timestamp;
}
EOF_8cee8d8c

mkdir -p "src/main/java/com/poline/dto"
cat > 'src/main/java/com/poline/dto/FulfillEvent.java' << 'EOF_89a52fff'
package com.poline.dto;

import com.poline.model.EventType;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Positive;
import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Data;
import lombok.NoArgsConstructor;

/**
 * Kafka 消息体 — 采购中台统一事件格式
 */
@Data @Builder @NoArgsConstructor @AllArgsConstructor
public class FulfillEvent {
    @NotBlank(message = "bizId 不能为空")
    private String bizId;            // 全局业务单号, WMS/供应商回传的事件唯一标识, 幂等键

    @NotBlank(message = "poNumber 不能为空")
    private String poNumber;

    @NotNull(message = "lineNumber 不能为空")
    private Integer lineNumber;

    @NotNull(message = "eventType 不能为空")
    private EventType eventType;

    @Positive(message = "qty 必须为正数")
    private Integer qty;          // RECEIVE 时表示本次入库数量，其它类型可 null
    private String reason;        // DELAY/CANCEL 原因
    private String source;        // 来源标识: WMS / SUPPLIER
}
EOF_89a52fff

mkdir -p "src/main/java/com/poline/dto"
cat > 'src/main/java/com/poline/dto/IdempotentResult.java' << 'EOF_6ecc46fe'
package com.poline.dto;

import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Data;
import lombok.NoArgsConstructor;

/**
 * 幂等处理结果 — 第一次处理 和 重复到达 都返回此 DTO
 */
@Data @Builder @NoArgsConstructor @AllArgsConstructor
public class IdempotentResult {
    private boolean duplicate;     // true = 重复到达 (已处理过, 本次不改行)
    private String result;         // 第一次处理结果, e.g. "SHIPPED", "PARTIAL_RCVD+50", "IGNORED_CANCELLED"
    private String firstCreatedAt; // 第一次处理的时间 (重复到达时返回首次时间)

    public static IdempotentResult first(String result) {
        return IdempotentResult.builder().duplicate(false).result(result).firstCreatedAt(null).build();
    }

    public static IdempotentResult duplicate(String result, String firstCreatedAt) {
        return IdempotentResult.builder().duplicate(true).result(result).firstCreatedAt(firstCreatedAt).build();
    }
}
EOF_6ecc46fe

mkdir -p "src/main/java/com/poline/dto"
cat > 'src/main/java/com/poline/dto/PoLineStatusResponse.java' << 'EOF_c75a5847'
package com.poline.dto;

import com.poline.model.LineStatus;
import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Data;
import lombok.NoArgsConstructor;

@Data @Builder @NoArgsConstructor @AllArgsConstructor
public class PoLineStatusResponse {
    private String poNumber;
    private Integer lineNumber;
    private LineStatus status;
    private Integer qtyOrdered;
    private Integer qtyReceived;
    private String sku;
    private String supplierId;
    private String buyerId;
    private String updatedAt;
}
EOF_c75a5847

mkdir -p "src/main/java/com/poline/exception"
cat > 'src/main/java/com/poline/exception/AuthException.java' << 'EOF_5f45f17f'
package com.poline.exception;

import lombok.Getter;
import org.springframework.http.HttpStatus;

@Getter
public class AuthException extends RuntimeException {
    private final HttpStatus status;

    public AuthException(HttpStatus status, String message) {
        super(message);
        this.status = status;
    }

    public static AuthException unauthorized(String msg) {
        return new AuthException(HttpStatus.UNAUTHORIZED, msg);
    }

    public static AuthException forbidden(String msg) {
        return new AuthException(HttpStatus.FORBIDDEN, msg);
    }
}
EOF_5f45f17f

mkdir -p "src/main/java/com/poline/exception"
cat > 'src/main/java/com/poline/exception/GlobalExceptionHandler.java' << 'EOF_020d03f0'
package com.poline.exception;

import com.poline.dto.ApiError;
import jakarta.servlet.http.HttpServletRequest;
import lombok.extern.slf4j.Slf4j;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.http.converter.HttpMessageNotReadableException;
import org.springframework.web.bind.MethodArgumentNotValidException;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;
import org.springframework.web.method.annotation.MethodArgumentTypeMismatchException;

import java.time.LocalDateTime;

@Slf4j
@RestControllerAdvice
public class GlobalExceptionHandler {

    @ExceptionHandler(MethodArgumentNotValidException.class)
    public ResponseEntity<ApiError> onValidation(MethodArgumentNotValidException ex, HttpServletRequest req) {
        String msg = ex.getBindingResult().getFieldErrors().stream()
                .map(f -> f.getField() + ": " + f.getDefaultMessage())
                .reduce((a, b) -> a + "; " + b)
                .orElse("参数校验失败");
        return build(HttpStatus.BAD_REQUEST, msg, req);
    }

    @ExceptionHandler(MethodArgumentTypeMismatchException.class)
    public ResponseEntity<ApiError> onTypeMismatch(MethodArgumentTypeMismatchException ex, HttpServletRequest req) {
        return build(HttpStatus.BAD_REQUEST, "参数类型错误: " + ex.getPropertyName(), req);
    }

    @ExceptionHandler(HttpMessageNotReadableException.class)
    public ResponseEntity<ApiError> onBadBody(HttpMessageNotReadableException ex, HttpServletRequest req) {
        return build(HttpStatus.BAD_REQUEST, "请求体格式错误", req);
    }

    @ExceptionHandler(AuthException.class)
    public ResponseEntity<ApiError> onAuth(AuthException ex, HttpServletRequest req) {
        return build(ex.getStatus(), ex.getMessage(), req);
    }

    @ExceptionHandler(Exception.class)
    public ResponseEntity<ApiError> onFallback(Exception ex, HttpServletRequest req) {
        log.error("未处理异常", ex);
        return build(HttpStatus.INTERNAL_SERVER_ERROR, "服务器内部错误", req);
    }

    private ResponseEntity<ApiError> build(HttpStatus status, String message, HttpServletRequest req) {
        return ResponseEntity.status(status).body(ApiError.builder()
                .status(status.value())
                .error(status.getReasonPhrase())
                .message(message)
                .path(req.getRequestURI())
                .timestamp(LocalDateTime.now())
                .build());
    }
}
EOF_020d03f0

mkdir -p "src/main/java/com/poline/model"
cat > 'src/main/java/com/poline/model/EventType.java' << 'EOF_9e236469'
package com.poline.model;

/**
 * Kafka 事件类型
 */
public enum EventType {
    SHIP,       // 发运
    DELAY,      // 延误
    RECEIVE,    // 入库
    CANCEL      // 行取消
}
EOF_9e236469

mkdir -p "src/main/java/com/poline/model"
cat > 'src/main/java/com/poline/model/IdempotentEvent.java' << 'EOF_498c2d72'
package com.poline.model;

import jakarta.persistence.*;
import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.time.LocalDateTime;

/**
 * 幂等记录 — 每条 Kafka 事件按 biz_id 做全局幂等
 * biz_id 是 WMS/供应商回传的事件唯一标识 (如: WMS-SHIP-20260623-001)
 * 同一事务内先 INSERT 此记录, 再改 PO 行;
 * 重复到达时 UNIQUE 冲突 → 事务回滚, PO 行不会被二次改动
 */
@Entity
@Table(name = "idempotent_event")
@Data @Builder @NoArgsConstructor @AllArgsConstructor
public class IdempotentEvent {

    @Id
    @Column(name = "biz_id", length = 64)
    private String bizId;

    @Column(name = "po_number", length = 32)
    private String poNumber;

    @Column(name = "line_number")
    private Integer lineNumber;

    @Column(name = "event_type", length = 16)
    private String eventType;

    @Column(name = "result", length = 32)
    private String result;

    @Column(name = "payload", columnDefinition = "jsonb")
    private String payload;

    @Column(name = "created_at")
    private LocalDateTime createdAt;
}
EOF_498c2d72

mkdir -p "src/main/java/com/poline/model"
cat > 'src/main/java/com/poline/model/LineStatus.java' << 'EOF_8b07a0c7'
package com.poline.model;

/**
 * PO 行生命周期状态
 *
 * PLACED      → 下单初始状态
 * SHIPPED     → 供应商/WMS 回报「发运」
 * DELAYED     → 供应商/WMS 回报「延误」
 * PARTIAL_RCVD→ WMS 回报「入库」(部分收货)
 * RECEIVED    → WMS 回报「入库」(全量收货)
 * CANCELLED   → 供应商/业务回报「行取消」
 *
 * phase 值定义推进方向: 只允许同 phase 或更高 phase 的状态变更.
 * SHIPPED 和 DELAYED 都是 phase 2 (发运/延误同级, 可互切);
 * PARTIAL_RCVD 和 RECEIVED 是 phase 3+, 不允许被 phase 1 或 2 的事件打回.
 * CANCELLED 是终态, 任何事件都不能再改.
 */
public enum LineStatus {
    PLACED(1),
    SHIPPED(2),
    DELAYED(2),
    PARTIAL_RCVD(3),
    RECEIVED(4),
    CANCELLED(99);

    private final int phase;

    LineStatus(int phase) {
        this.phase = phase;
    }

    public int getPhase() {
        return phase;
    }

    /**
     * 只允许状态推进 (target.phase >= current.phase) 或同级切换 (同 phase).
     * 不允许回退: e.g. RECEIVED 不能被打回 SHIPPED.
     * CANCELLED 是终态, 任何推进都不允许 (在 handleEvent 里单独判断).
     */
    public boolean canTransitionTo(LineStatus target) {
        return target.phase >= this.phase;
    }
}
EOF_8b07a0c7

mkdir -p "src/main/java/com/poline/model"
cat > 'src/main/java/com/poline/model/PoLine.java' << 'EOF_f812f522'
package com.poline.model;

import jakarta.persistence.*;
import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.time.LocalDateTime;

@Entity
@Table(name = "po_line")
@IdClass(PoLineId.class)
@Data @Builder @NoArgsConstructor @AllArgsConstructor
public class PoLine {

    @Id
    @Column(name = "po_number", length = 32)
    private String poNumber;

    @Id
    @Column(name = "line_number")
    private Integer lineNumber;

    @Enumerated(EnumType.STRING)
    @Column(name = "status", length = 16)
    private LineStatus status;

    @Column(name = "qty_ordered")
    private Integer qtyOrdered;

    @Column(name = "qty_received")
    private Integer qtyReceived;

    @Column(name = "sku", length = 64)
    private String sku;

    @Column(name = "supplier_id", length = 32)
    private String supplierId;

    @Column(name = "buyer_id", length = 32)
    private String buyerId;

    @Column(name = "updated_at")
    private LocalDateTime updatedAt;
}
EOF_f812f522

mkdir -p "src/main/java/com/poline/model"
cat > 'src/main/java/com/poline/model/PoLineEvent.java' << 'EOF_42071e87'
package com.poline.model;

import jakarta.persistence.*;
import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.time.LocalDateTime;

@Entity
@Table(name = "po_line_event")
@Data @Builder @NoArgsConstructor @AllArgsConstructor
public class PoLineEvent {

    @Id @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(name = "po_number", length = 32)
    private String poNumber;

    @Column(name = "line_number")
    private Integer lineNumber;

    @Enumerated(EnumType.STRING)
    @Column(name = "event_type", length = 16)
    private EventType eventType;

    @Column(name = "payload", columnDefinition = "jsonb")
    private String payload;

    @Column(name = "created_at")
    private LocalDateTime createdAt;
}
EOF_42071e87

mkdir -p "src/main/java/com/poline/model"
cat > 'src/main/java/com/poline/model/PoLineId.java' << 'EOF_8fec7f7a'
package com.poline.model;

import jakarta.persistence.Column;
import lombok.AllArgsConstructor;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.io.Serializable;

@Data @NoArgsConstructor @AllArgsConstructor
public class PoLineId implements Serializable {
    @Column(name = "po_number", length = 32)
    private String poNumber;

    @Column(name = "line_number")
    private Integer lineNumber;
}
EOF_8fec7f7a

mkdir -p "src/main/java/com/poline/repository"
cat > 'src/main/java/com/poline/repository/IdempotentEventRepository.java' << 'EOF_74be84e8'
package com.poline.repository;

import com.poline.model.IdempotentEvent;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

import java.util.Optional;

public interface IdempotentEventRepository extends JpaRepository<IdempotentEvent, String> {

    Optional<IdempotentEvent> findByBizId(String bizId);

    @Modifying
    @Query("UPDATE IdempotentEvent e SET e.result = :result WHERE e.bizId = :bizId")
    int updateResult(@Param("bizId") String bizId, @Param("result") String result);
}
EOF_74be84e8

mkdir -p "src/main/java/com/poline/repository"
cat > 'src/main/java/com/poline/repository/PoLineEventRepository.java' << 'EOF_2c59a5e7'
package com.poline.repository;

import com.poline.model.PoLineEvent;
import org.springframework.data.jpa.repository.JpaRepository;

public interface PoLineEventRepository extends JpaRepository<PoLineEvent, Long> {
}
EOF_2c59a5e7

mkdir -p "src/main/java/com/poline/repository"
cat > 'src/main/java/com/poline/repository/PoLineRepository.java' << 'EOF_50072277'
package com.poline.repository;

import com.poline.model.LineStatus;
import com.poline.model.PoLine;
import com.poline.model.PoLineId;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

import java.util.List;
import java.util.Optional;

public interface PoLineRepository extends JpaRepository<PoLine, PoLineId> {

    Optional<PoLine> findByPoNumberAndLineNumberAndBuyerId(String poNumber, Integer lineNumber, String buyerId);

    Optional<PoLine> findByPoNumberAndLineNumber(String poNumber, Integer lineNumber);

    List<PoLine> findByPoNumberAndBuyerId(String poNumber, String buyerId);

    List<PoLine> findByPoNumber(String poNumber);

    List<PoLine> findByStatusAndBuyerId(LineStatus status, String buyerId);

    List<PoLine> findByStatus(LineStatus status);

    @Modifying
    @Query("UPDATE PoLine p SET p.status = :status, p.updatedAt = NOW() " +
           "WHERE p.poNumber = :poNumber AND p.lineNumber = :lineNumber")
    int updateStatus(@Param("poNumber") String poNumber,
                     @Param("lineNumber") Integer lineNumber,
                     @Param("status") LineStatus status);

    @Modifying
    @Query("UPDATE PoLine p SET p.qtyReceived = p.qtyReceived + :qty, p.updatedAt = NOW() " +
           "WHERE p.poNumber = :poNumber AND p.lineNumber = :lineNumber")
    int addQtyReceived(@Param("poNumber") String poNumber,
                       @Param("lineNumber") Integer lineNumber,
                       @Param("qty") Integer qty);
}
EOF_50072277

mkdir -p "src/main/java/com/poline/service"
cat > 'src/main/java/com/poline/service/PoLineService.java' << 'EOF_830a86f0'
package com.poline.service;

import com.poline.dto.FulfillEvent;
import com.poline.dto.PoLineStatusResponse;
import com.poline.dto.IdempotentResult;
import com.poline.model.*;
import com.poline.repository.IdempotentEventRepository;
import com.poline.repository.PoLineEventRepository;
import com.poline.repository.PoLineRepository;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.kafka.support.Acknowledgment;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.transaction.support.TransactionSynchronization;
import org.springframework.transaction.support.TransactionSynchronizationManager;

import java.time.LocalDateTime;
import java.util.List;
import java.util.Optional;

@Slf4j
@Service
@RequiredArgsConstructor
public class PoLineService {

    private final PoLineRepository poLineRepo;
    private final PoLineEventRepository eventRepo;
    private final IdempotentEventRepository idempotentRepo;
    private final KafkaTemplate<String, FulfillEvent> kafkaTemplate;

    private final String topicName = "po-line-fulfill-events";

    /* ── 业务方查询 (带 buyerId 过滤, 别家当作不存在) ── */

    public Optional<PoLineStatusResponse> getStatus(String poNumber, Integer lineNumber, String buyerId) {
        return poLineRepo.findByPoNumberAndLineNumberAndBuyerId(poNumber, lineNumber, buyerId)
                .map(this::toResponse);
    }

    public List<PoLineStatusResponse> getByPo(String poNumber, String buyerId) {
        return poLineRepo.findByPoNumberAndBuyerId(poNumber, buyerId).stream()
                .map(this::toResponse).toList();
    }

    public List<PoLineStatusResponse> getByStatus(LineStatus status, String buyerId) {
        return poLineRepo.findByStatusAndBuyerId(status, buyerId).stream()
                .map(this::toResponse).toList();
    }

    /* ── 管理口: 往 Kafka 丢测试事件 ── */

    public FulfillEvent sendTestEvent(FulfillEvent event) {
        String key = event.getPoNumber() + "-" + event.getLineNumber();
        kafkaTemplate.send(topicName, key, event);
        log.info("管理口发送测试事件: key={}, eventType={}, bizId={}", key, event.getEventType(), event.getBizId());
        return event;
    }

    /* ── Kafka Consumer 回调: 幂等处理事件改行状态 ── */

    /**
     * 幂等逻辑:
     * 1. 先查 idempotent_event 表 — 如果 biz_id 已存在, 说明是重复到达, 直接返回第一次结果, 不再改 PO 行
     * 2. 同一笔事务内: INSERT 幂等记录 → 写事件日志 → 改 PO 行状态
     * 3. 如果中途 INSERT 幂等记录时 UNIQUE 冲突 (并发场景), 事务回滚, PO 行不动
     *
     * 关键: 幂等记录和改 PO 行在同一笔 @Transactional 里, 不存在「先改行再补表」的中间态
     *
     * Ack 时机: 在当前事务的 afterCommit 回调里执行 acknowledgment.ack()
     *          → 保证数据库提交成功后才确认 Kafka 消息
     *          → 进程在 commit 和 ack 之间崩溃, 消息未被确认, Kafka 会重投
     */
    @Transactional
    public IdempotentResult handleEvent(FulfillEvent event, Acknowledgment ack) {
        // 注册事务同步: afterCommit 时 ack → DB 提交成功后才确认消费
        if (ack != null && TransactionSynchronizationManager.isSynchronizationActive()) {
            TransactionSynchronizationManager.registerSynchronization(new TransactionSynchronization() {
                @Override
                public void afterCommit() {
                    log.debug("DB 事务提交成功, 执行 Kafka ack: bizId={}", event.getBizId());
                    ack.acknowledge();
                }
            });
        }
        String bizId = event.getBizId();

        // ── 0) 幂等判定: biz_id 已存在 → 重复到达, 返回第一次结果 ──
        Optional<IdempotentEvent> existing = idempotentRepo.findByBizId(bizId);
        if (existing.isPresent()) {
            IdempotentEvent prev = existing.get();
            log.info("幂等拦截: bizId={} 已处理过, 结果={}, 本次忽略", bizId, prev.getResult());
            return IdempotentResult.duplicate(prev.getResult(), prev.getCreatedAt().toString());
        }

        String po = event.getPoNumber();
        Integer line = event.getLineNumber();

        // ── 1) 查 PO 行 ──
        Optional<PoLine> opt = poLineRepo.findByPoNumberAndLineNumber(po, line);
        if (opt.isEmpty()) {
            log.warn("PO行不存在: poNumber={}, lineNumber={}, 忽略事件 {}", po, line, event.getEventType());
            // 即使 PO 行不存在也写幂等记录, 防止重复到达时反复打日志
            saveIdempotentRecord(bizId, po, line, event.getEventType().name(), "IGNORED_NO_PO_LINE", event);
            return IdempotentResult.first("IGNORED_NO_PO_LINE");
        }
        PoLine poLine = opt.get();

        if (poLine.getStatus() == LineStatus.CANCELLED) {
            log.info("PO行已取消, 忽略事件: poNumber={}, lineNumber={}", po, line);
            saveIdempotentRecord(bizId, po, line, event.getEventType().name(), "IGNORED_CANCELLED", event);
            return IdempotentResult.first("IGNORED_CANCELLED");
        }

        // ── 1.5) 推进校验: 乱序晚到 (e.g. 已收货时来发运) → 不允许回退 ──
        LineStatus targetStatus = resolveTargetStatus(event.getEventType());
        if (!poLine.getStatus().canTransitionTo(targetStatus)) {
            log.info("乱序拦截: bizId={}, 当前={}, 目标={}, 不允许回退, 忽略",
                    bizId, poLine.getStatus(), targetStatus);
            saveIdempotentRecord(bizId, po, line, event.getEventType().name(),
                    "IGNORED_OUT_OF_ORDER:" + poLine.getStatus().name() + ">" + targetStatus.name(), event);
            return IdempotentResult.first("IGNORED_OUT_OF_ORDER:" + poLine.getStatus().name());
        }

        // ── 2) 同一事务: 先写幂等记录 → 再写事件日志 → 再改 PO 行 ──
        String result = null;
        switch (event.getEventType()) {
            case SHIP -> {
                result = LineStatus.SHIPPED.name();
                saveIdempotentRecord(bizId, po, line, EventType.SHIP.name(), result, event);
                eventRepo.save(toPoLineEvent(event));
                poLineRepo.updateStatus(po, line, LineStatus.SHIPPED);
                log.info("发运 → SHIPPED: {} / {} (bizId={})", po, line, bizId);
            }
            case DELAY -> {
                result = LineStatus.DELAYED.name();
                saveIdempotentRecord(bizId, po, line, EventType.DELAY.name(), result, event);
                eventRepo.save(toPoLineEvent(event));
                poLineRepo.updateStatus(po, line, LineStatus.DELAYED);
                log.info("延误 → DELAYED: {} / {} (bizId={})", po, line, bizId);
            }
            case RECEIVE -> {
                Integer qty = event.getQty() != null ? event.getQty() : 0;
                // 先 INSERT 幂等占位 (result 暂填 TBD, 防止并发重复进入)
                saveIdempotentRecord(bizId, po, line, EventType.RECEIVE.name(), "RECEIVE_TBD", event);
                // 再改 PO 行
                poLineRepo.addQtyReceived(po, line, qty);
                PoLine refreshed = poLineRepo.findByPoNumberAndLineNumber(po, line).orElseThrow();
                LineStatus newStatus = refreshed.getQtyReceived() >= refreshed.getQtyOrdered()
                        ? LineStatus.RECEIVED : LineStatus.PARTIAL_RCVD;
                poLineRepo.updateStatus(po, line, newStatus);
                // 最后更新幂等记录的 result 为真实值
                result = newStatus.name() + "+" + qty;
                idempotentRepo.updateResult(bizId, result);
                eventRepo.save(toPoLineEvent(event));
                log.info("入库 +{} → {}: {} / {} (bizId={})", qty, newStatus, po, line, bizId);
            }
            case CANCEL -> {
                result = LineStatus.CANCELLED.name();
                saveIdempotentRecord(bizId, po, line, EventType.CANCEL.name(), result, event);
                eventRepo.save(toPoLineEvent(event));
                poLineRepo.updateStatus(po, line, LineStatus.CANCELLED);
                log.info("行取消 → CANCELLED: {} / {} (bizId={})", po, line, bizId);
            }
        }

        return IdempotentResult.first(result);
    }

    /* ── helpers ── */

    /**
     * 根据事件类型确定目标状态 (不含 RECEIVE 的动态判定, RECEIVE 取最低可能 PARTIAL_RCVD)
     * 用于推进校验 canTransitionTo — 只要当前状态 phase >= 目标 phase, 就拦截乱序
     */
    private LineStatus resolveTargetStatus(EventType eventType) {
        return switch (eventType) {
            case SHIP -> LineStatus.SHIPPED;
            case DELAY -> LineStatus.DELAYED;
            case RECEIVE -> LineStatus.PARTIAL_RCVD; // phase 3, 已收货的行 phase>=3 不允许被打回
            case CANCEL -> LineStatus.CANCELLED;
        };
    }

    /**
     * INSERT 幂等记录 — 如果 biz_id 已存在 (并发场景), 抛 DataIntegrityViolationException,
     * 导致整个事务回滚, PO 行不会被改动
     */
    private void saveIdempotentRecord(String bizId, String po, Integer line,
                                       String eventType, String result, FulfillEvent event) {
        try {
            idempotentRepo.save(IdempotentEvent.builder()
                    .bizId(bizId)
                    .poNumber(po)
                    .lineNumber(line)
                    .eventType(eventType)
                    .result(result)
                    .payload(toPayload(event))
                    .createdAt(LocalDateTime.now())
                    .build());
        } catch (DataIntegrityViolationException e) {
            // UNIQUE 冲突 → 事务回滚, 不改 PO 行
            log.warn("幂等记录 bizId={} UNIQUE 冲突, 事务回滚", bizId);
            throw e; // 让 @Transactional 回滚整笔事务
        }
    }

    private PoLineEvent toPoLineEvent(FulfillEvent event) {
        return PoLineEvent.builder()
                .poNumber(event.getPoNumber())
                .lineNumber(event.getLineNumber())
                .eventType(event.getEventType())
                .payload(toPayload(event))
                .createdAt(LocalDateTime.now())
                .build();
    }

    private PoLineStatusResponse toResponse(PoLine p) {
        return PoLineStatusResponse.builder()
                .poNumber(p.getPoNumber())
                .lineNumber(p.getLineNumber())
                .status(p.getStatus())
                .qtyOrdered(p.getQtyOrdered())
                .qtyReceived(p.getQtyReceived())
                .sku(p.getSku())
                .supplierId(p.getSupplierId())
                .buyerId(p.getBuyerId())
                .updatedAt(p.getUpdatedAt() != null ? p.getUpdatedAt().toString() : null)
                .build();
    }

    private String toPayload(FulfillEvent e) {
        return "{\"bizId\":\"" + e.getBizId()
             + "\",\"qty\":" + (e.getQty() != null ? e.getQty() : "null")
             + ",\"reason\":\"" + (e.getReason() != null ? e.getReason() : "")
             + "\",\"source\":\"" + (e.getSource() != null ? e.getSource() : "")
             + "\"}";
    }
}
EOF_830a86f0

mkdir -p "src/main/java/com/poline/web"
cat > 'src/main/java/com/poline/web/AdminAuthInterceptor.java' << 'EOF_44ca2504'
package com.poline.web;

import com.poline.exception.AuthException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Component;
import org.springframework.web.servlet.HandlerInterceptor;

@Component
@RequiredArgsConstructor
public class AdminAuthInterceptor implements HandlerInterceptor {

    private final AdminTokenProvider adminTokenProvider;

    @Override
    public boolean preHandle(HttpServletRequest req, HttpServletResponse resp, Object handler) {
        String token = req.getHeader("X-Admin-Token");
        if (token == null || !adminTokenProvider.isValid(token)) {
            throw AuthException.unauthorized("管理口需要有效的 X-Admin-Token");
        }
        return true;
    }
}
EOF_44ca2504

mkdir -p "src/main/java/com/poline/web"
cat > 'src/main/java/com/poline/web/AdminTokenProvider.java' << 'EOF_f5c9b1c3'
package com.poline.web;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

@Component
public class AdminTokenProvider {

    @Value("${admin.token}")
    private String adminToken;

    public boolean isValid(String token) {
        return adminToken.equals(token);
    }
}
EOF_f5c9b1c3

mkdir -p "src/main/java/com/poline/web"
cat > 'src/main/java/com/poline/web/BuyerAuthInterceptor.java' << 'EOF_f1192dd5'
package com.poline.web;

import com.poline.exception.AuthException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.springframework.stereotype.Component;
import org.springframework.web.servlet.HandlerInterceptor;

/**
 * 业务方请求必须带 X-Buyer-Id, 空或缺失 → 401;
 * 查询时仅返回属于该采购方的行, 别家的行当作不存在 (返回 404 或空列表).
 */
@Component
public class BuyerAuthInterceptor implements HandlerInterceptor {

    @Override
    public boolean preHandle(HttpServletRequest req, HttpServletResponse resp, Object handler) {
        String buyerId = req.getHeader("X-Buyer-Id");
        if (buyerId == null || buyerId.isBlank()) {
            throw AuthException.unauthorized("业务查询需要 X-Buyer-Id 头");
        }
        // 保存到 request attribute, controller/service 可以取
        req.setAttribute("buyerId", buyerId.trim());
        return true;
    }
}
EOF_f1192dd5

mkdir -p "src/main/resources"
cat > 'src/main/resources/application.yml' << 'EOF_256c57f5'
server:
  port: 8080

spring:
  datasource:
    url: jdbc:postgresql://localhost:5432/poline
    username: poline
    password: poline123
    driver-class-name: org.postgresql.Driver

  jpa:
    hibernate:
      ddl-auto: validate          # Flyway 管 schema, JPA 只做校验
    open-in-view: false
    properties:
      hibernate:
        format_sql: true

  flyway:
    enabled: true
    locations: classpath:db/migration

  kafka:
    bootstrap-servers: localhost:9092
    listener:
      ack-mode: manual              # 手动 ack: DB 事务提交后才确认
    consumer:
      group-id: po-line-fulfill-service
      auto-offset-reset: earliest   # 新 consumer 从最早开始消费
      key-deserializer: org.apache.kafka.common.serialization.StringDeserializer
      value-deserializer: org.springframework.kafka.support.serializer.JsonDeserializer
      properties:
        spring.json.trusted.packages: "com.poline.dto"
    producer:
      key-serializer: org.apache.kafka.common.serialization.StringSerializer
      value-serializer: org.springframework.kafka.support.serializer.JsonSerializer

kafka:
  topic:
    fulfill-events: po-line-fulfill-events

admin:
  token: poline-admin-secret

logging:
  level:
    com.poline: INFO
    org.springframework.kafka: INFO
EOF_256c57f5

mkdir -p "src/main/resources/db/migration"
cat > 'src/main/resources/db/migration/V1__create_po_line_tables.sql' << 'EOF_9b1dcf96'
-- PO 行状态追踪表
CREATE TABLE po_line (
    po_number    VARCHAR(32)  NOT NULL,
    line_number  INT          NOT NULL,
    status       VARCHAR(16)  NOT NULL DEFAULT 'PLACED',
    qty_ordered  INT          NOT NULL,
    qty_received INT          NOT NULL DEFAULT 0,
    sku          VARCHAR(64)  NOT NULL,
    supplier_id  VARCHAR(32)  NOT NULL,
    updated_at   TIMESTAMP    NOT NULL DEFAULT NOW(),
    PRIMARY KEY (po_number, line_number)
);

-- PO 行事件日志（审计用）
CREATE TABLE po_line_event (
    id           BIGSERIAL    PRIMARY KEY,
    po_number    VARCHAR(32)  NOT NULL,
    line_number  INT          NOT NULL,
    event_type   VARCHAR(16)  NOT NULL,
    payload      JSONB,
    created_at   TIMESTAMP    NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_event_po_line ON po_line_event (po_number, line_number);
EOF_9b1dcf96

mkdir -p "src/main/resources/db/migration"
cat > 'src/main/resources/db/migration/V3__add_buyer_id_and_reseed.sql' << 'EOF_7a3f53d1'
-- V3: 加 buyer_id 列, 数据隔离采购方
ALTER TABLE po_line ADD COLUMN buyer_id VARCHAR(32) NOT NULL DEFAULT 'BUYER-DEFAULT';

-- 安全: 先清空旧 seed, 再插入两条稳定的行 (两家采购方、各一条 OPEN 的行)
DELETE FROM po_line_event;
DELETE FROM po_line;

INSERT INTO po_line (po_number, line_number, qty_ordered, sku, supplier_id, buyer_id, status) VALUES
    ('PO-2026-001', 1, 100, 'SKU-A100', 'SUP-001', 'BUYER-ACME',  'PLACED'),
    ('PO-2026-002', 1,  50, 'SKU-C300', 'SUP-001', 'BUYER-GLOBEX', 'PLACED');
EOF_7a3f53d1

mkdir -p "src/main/resources/db/migration"
cat > 'src/main/resources/db/migration/V4__create_idempotent_event.sql' << 'EOF_96ec19e9'
-- V4: 幂等记录表 — 每条 Kafka 事件按全局业务单号做幂等
-- 同一事务内: 先 INSERT 幂等记录 → 再改 PO 行状态
-- 重复到达: biz_id UNIQUE 冲突 → 整个事务回滚, PO 行不动
CREATE TABLE idempotent_event (
    biz_id       VARCHAR(64)  PRIMARY KEY,      -- 全局业务单号 (WMS/供应商回传的事件唯一标识)
    po_number    VARCHAR(32)  NOT NULL,
    line_number  INT          NOT NULL,
    event_type   VARCHAR(16)  NOT NULL,
    result       VARCHAR(32)  NOT NULL,          -- 第一次处理结果: e.g. "SHIPPED", "PARTIAL_RCVD+50"
    payload      JSONB,                          -- 原始事件完整内容
    created_at   TIMESTAMP    NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_idempotent_po_line ON idempotent_event (po_number, line_number);
EOF_96ec19e9

cat > 'start.sh' << 'EOF_d0b2ef35'
#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "=== PO Line Fulfill — 本地启动 ==="

# ── 1. PostgreSQL ──
echo "[1] 启动 PostgreSQL (Docker)..."
if docker ps --format '{{.Names}}' | grep -q poline-postgres; then
  echo "    PostgreSQL 容器已在运行"
else
  docker run -d --name poline-postgres \
    -e POSTGRES_USER=poline \
    -e POSTGRES_PASSWORD=poline123 \
    -e POSTGRES_DB=poline \
    -p 5432:5432 \
    postgres:16-alpine
  echo "    等待 PostgreSQL 就绪..."
  sleep 5
fi

# ── 2. Kafka (Redpanda) ──
echo "[2] 启动 Redpanda (Docker)..."
if docker ps --format '{{.Names}}' | grep -q poline-redpanda; then
  echo "    Redpanda 容器已在运行"
else
  docker run -d --name poline-redpanda \
    -p 9092:9092 \
    -p 9644:9644 \
    redpanda-data/redpanda:latest \
    redpanda start \
    --mode dev \
    --smp 1 \
    --memory 256M \
    --overprovisioned \
    --node-id 0 \
    --kafka-addr internal:0.0.0.0,external:0.0.0.0 \
    --advertise-kafka-addr internal://localhost:9092,external://localhost:9092
  echo "    等待 Redpanda 就绪..."
  sleep 5
fi

# ── 3. 创建 topic ──
echo "[3] 创建 Kafka topic: po-line-fulfill-events..."
docker exec poline-redpanda \
  rpk topic create po-line-fulfill-events \
  --partitions 3 \
  --replication-factor 1 \
  2>/dev/null || echo "    topic 已存在, 跳过"

# ── 4. 编译 & 启动 Spring Boot ──
echo "[4] 编译项目..."
./mvnw package -DskipTests -q 2>/dev/null || mvn package -DskipTests -q

echo "[5] 启动 Spring Boot..."
java -jar target/po-line-fulfill-1.0.0.jar

echo "=== 完成 ==="
EOF_d0b2ef35

# ---- Build with Maven ----
echo "[4/5] Building with Maven..."
if [ -f "./mvnw" ]; then
    chmod +x ./mvnw
    ./mvnw package -DskipTests -q 2>&1 | tail -5
else
    mvn package -DskipTests -q 2>&1 | tail -5
fi

# ---- Start server ----
echo "[5/5] Starting Spring Boot..."
JAR_FILE=$(find target -maxdepth 1 -name "*.jar" -not -name "*sources*" 2>/dev/null | head -1)
if [ -z "$JAR_FILE" ]; then
    echo "ERROR: No jar found"; ls -la target/; exit 1
fi

nohup java -jar "$JAR_FILE" > "$WORKDIR/app.log" 2>&1 &
echo "$!" > "$WORKDIR/app.pid"
echo "   PID: $(cat $WORKDIR/app.pid)"

# ---- Wait for health ----
echo "   Waiting for /api/health (max 120s)..."
for i in $(seq 1 120); do
    if curl -fsS "http://127.0.0.1:8080/api/health" >/dev/null 2>&1; then
        echo "   [OK] Service ready: http://127.0.0.1:8080"
        curl -s http://127.0.0.1:8080/api/health
        exit 0
    fi
    sleep 1
done

echo "   [ERR] Service failed to start. Last 50 lines:"
tail -50 "$WORKDIR/app.log" 2>/dev/null || true
exit 1
