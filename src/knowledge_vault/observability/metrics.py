from prometheus_client import Counter, Gauge, Histogram

HTTP_REQUESTS = Counter(
    "knowledge_vault_http_requests_total", "HTTP requests", ("method", "route", "status")
)
HTTP_LATENCY = Histogram(
    "knowledge_vault_http_request_duration_seconds", "HTTP latency", ("method", "route")
)
TOOL_CALLS = Counter("knowledge_vault_mcp_tool_calls_total", "MCP tool calls", ("tool", "outcome"))
BATCH_TRANSITIONS = Counter(
    "knowledge_vault_batch_transitions_total", "Batch state transitions", ("state",)
)
ASSERTION_OUTCOMES = Counter(
    "knowledge_vault_assertion_outcomes_total", "Assertion ingestion outcomes", ("outcome",)
)
SEARCH_LATENCY = Histogram(
    "knowledge_vault_search_duration_seconds", "Knowledge search latency", ("mode",)
)
EMBEDDING_QUEUE = Gauge(
    "knowledge_vault_embedding_queue_depth", "Embedding jobs by state", ("state",)
)
EMBEDDING_OUTCOMES = Counter(
    "knowledge_vault_embedding_jobs_total", "Embedding job outcomes", ("outcome",)
)
HEARTBEAT_AGE = Gauge(
    "knowledge_vault_heartbeat_age_seconds",
    "Seconds since a background duty last succeeded",
    ("name",),
)
DB_POOL = Gauge(
    "knowledge_vault_database_pool_connections", "Database pool connections", ("state",)
)
