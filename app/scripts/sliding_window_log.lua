-- Sliding Window Log rate limiter (exact, memory-heavy: one ZSET entry per request)
-- KEYS[1] = zset key
-- ARGV[1] = now_ms
-- ARGV[2] = window_ms
-- ARGV[3] = limit
-- ARGV[4] = ttl_seconds

local key = KEYS[1]
local now_ms = tonumber(ARGV[1])
local window_ms = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])
local ttl = tonumber(ARGV[4])

local window_start = now_ms - window_ms

-- drop entries outside the window
redis.call('ZREMRANGEBYSCORE', key, '-inf', window_start)

local count = redis.call('ZCARD', key)

local allowed = 0
if count < limit then
  -- member must be unique even if two requests land in the same millisecond
  local member = now_ms .. '-' .. redis.call('INCR', key .. ':seq')
  redis.call('ZADD', key, now_ms, member)
  redis.call('EXPIRE', key, ttl)
  redis.call('EXPIRE', key .. ':seq', ttl)
  count = count + 1
  allowed = 1
end

local retry_after_ms = 0
if allowed == 0 then
  local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
  if oldest[2] ~= nil then
    retry_after_ms = (tonumber(oldest[2]) + window_ms) - now_ms
  end
end

return {allowed, limit - count, retry_after_ms}
