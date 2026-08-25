-- Sliding Window Counter (approximation: weighted average of current + previous fixed window)
-- KEYS[1] = base key (windows are suffixed with the window bucket index)
-- ARGV[1] = now_ms
-- ARGV[2] = window_ms
-- ARGV[3] = limit
-- ARGV[4] = ttl_seconds

local base_key = KEYS[1]
local now_ms = tonumber(ARGV[1])
local window_ms = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])
local ttl = tonumber(ARGV[4])

local current_bucket = math.floor(now_ms / window_ms)
local previous_bucket = current_bucket - 1

local current_key = base_key .. ':' .. current_bucket
local previous_key = base_key .. ':' .. previous_bucket

local current_count = tonumber(redis.call('GET', current_key)) or 0
local previous_count = tonumber(redis.call('GET', previous_key)) or 0

local elapsed_in_current = now_ms - (current_bucket * window_ms)
local weight = (window_ms - elapsed_in_current) / window_ms

local estimated = current_count + (previous_count * weight)

local allowed = 0
if estimated < limit then
  current_count = redis.call('INCR', current_key)
  redis.call('EXPIRE', current_key, ttl)
  allowed = 1
  estimated = estimated + 1
end

local retry_after_ms = 0
if allowed == 0 then
  retry_after_ms = window_ms - elapsed_in_current
end

return {allowed, math.max(0, math.floor(limit - estimated)), retry_after_ms}
