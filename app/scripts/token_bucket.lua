-- Token Bucket rate limiter
-- KEYS[1] = bucket key
-- ARGV[1] = capacity (max tokens)
-- ARGV[2] = refill_rate (tokens per second)
-- ARGV[3] = now_ms
-- ARGV[4] = requested tokens (usually 1)
-- ARGV[5] = ttl_seconds (key expiry, prevents unbounded memory growth for idle clients)

local key = KEYS[1]
local capacity = tonumber(ARGV[1])
local refill_rate = tonumber(ARGV[2])
local now_ms = tonumber(ARGV[3])
local requested = tonumber(ARGV[4])
local ttl = tonumber(ARGV[5])

local bucket = redis.call('HMGET', key, 'tokens', 'ts')
local tokens = tonumber(bucket[1])
local last_ts = tonumber(bucket[2])

if tokens == nil then
  tokens = capacity
  last_ts = now_ms
end

local elapsed_ms = math.max(0, now_ms - last_ts)
local refill = (elapsed_ms / 1000.0) * refill_rate
tokens = math.min(capacity, tokens + refill)

local allowed = 0
if tokens >= requested then
  tokens = tokens - requested
  allowed = 1
end

redis.call('HMSET', key, 'tokens', tokens, 'ts', now_ms)
redis.call('EXPIRE', key, ttl)

-- retry_after_ms: time until enough tokens accumulate for 1 request
local retry_after_ms = 0
if allowed == 0 then
  local deficit = requested - tokens
  retry_after_ms = math.ceil((deficit / refill_rate) * 1000)
end

return {allowed, math.floor(tokens), retry_after_ms}
