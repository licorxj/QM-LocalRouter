import asyncio
import random
import time
from collections import defaultdict
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.database import async_session
from app.models.strategy import Strategy, StrategyRule
from app.models.api_key import ApiKey
from app.models.rotation_state import RotationState
from app.utils.key_health import revive_expired_cooldown


class KeyUsageTracker:
    """Track per-key usage for threshold-based switching."""

    def __init__(self):
        self._rpm_times: dict[int, list[float]] = defaultdict(list)
        self._total_counts: dict[int, int] = defaultdict(int)

    def record_usage(self, key_id: int):
        now = time.time()
        self._rpm_times[key_id].append(now)
        self._total_counts[key_id] += 1
        # Clean old entries (older than 60s)
        cutoff = now - 60
        self._rpm_times[key_id] = [t for t in self._rpm_times[key_id] if t > cutoff]

    def get_rpm(self, key_id: int) -> int:
        now = time.time()
        cutoff = now - 60
        return sum(1 for t in self._rpm_times[key_id] if t > cutoff)

    def get_total(self, key_id: int) -> int:
        return self._total_counts[key_id]

    def is_over_rpm(self, key_id: int, threshold: int) -> bool:
        if threshold <= 0:
            return False
        return self.get_rpm(key_id) >= threshold

    def is_over_count(self, key_id: int, threshold: int) -> bool:
        if threshold <= 0:
            return False
        return self.get_total(key_id) >= threshold


# Global tracker instance
_key_tracker = KeyUsageTracker()


class RuleTokenTracker:
    """Track per-rule token usage for threshold-based switching with time windows."""

    PERIOD_SECONDS = {
        "per_minute": 60,
        "per_5min": 300,
        "per_day": 86400,
        "per_month": 2592000,
    }

    def __init__(self):
        # key: (strategy_id, rule_id), value: {"amount": int, "window_start": float}
        self._usage: dict[tuple[int, int], dict] = {}

    def _get_window(self, period: str) -> float:
        return self.PERIOD_SECONDS.get(period, 86400)

    def is_over_threshold(self, strategy_id: int, rule_id: int, threshold: int, period: str) -> bool:
        if threshold <= 0:
            return False
        key = (strategy_id, rule_id)
        entry = self._usage.get(key)
        if not entry:
            return False
        window = self._get_window(period)
        if time.time() - entry["window_start"] >= window:
            return False  # Window expired
        return entry["amount"] >= threshold

    def record_usage(self, strategy_id: int, rule_id: int, tokens: int, period: str):
        key = (strategy_id, rule_id)
        entry = self._usage.get(key)
        window = self._get_window(period)
        now = time.time()
        if not entry or (now - entry["window_start"]) >= window:
            self._usage[key] = {"amount": tokens, "window_start": now}
        else:
            entry["amount"] += tokens


# Global rule token tracker instance
_rule_token_tracker = RuleTokenTracker()


class RotationIndex:
    """Cross-request round-robin counter, persisted to DB so restarts resume.

    In-memory cache avoids a DB read per request; dirty counters are flushed
    to the rotation_states table in one batch on a 5s debounce.

    落库节流：计数器每请求 +1（纯内存），DB 写按 5s 窗口合并后台刷盘。
    之前每请求在全局锁内 SELECT+UPSERT+commit，SQLite 一次 commit 在
    Windows 上可达几十毫秒，是转发热路径的主要延迟与并发串行化来源之一。
    代价仅是重启后轮询起点可能落后几秒（本就允许：最坏情况从较旧计数恢复）。
    """

    FLUSH_DELAY_SECONDS = 5.0

    def __init__(self):
        self._cache: dict[str, int] = {}
        self._loaded: set[str] = set()
        self._lock = asyncio.Lock()
        self._dirty: set[str] = set()
        self._flush_task: asyncio.Task | None = None

    async def next(self, scope: str, length: int) -> int:
        if length <= 0:
            return 0
        async with self._lock:
            if scope not in self._loaded:
                try:
                    await self._load(scope)
                except Exception:
                    pass  # fall back to 0; retry load on next call
                else:
                    self._loaded.add(scope)
            counter = self._cache.get(scope, 0)
            self._cache[scope] = counter + 1
            self._dirty.add(scope)
            self._schedule_flush()
        return counter % length

    def _schedule_flush(self):
        if self._flush_task is None or self._flush_task.done():
            try:
                self._flush_task = asyncio.create_task(self._flush())
            except RuntimeError:
                pass  # 无运行中事件循环（如脚本场景）：仅内存计数

    async def _flush(self):
        await asyncio.sleep(self.FLUSH_DELAY_SECONDS)
        async with self._lock:
            dirty = list(self._dirty)
            self._dirty.clear()
        if not dirty:
            return
        try:
            async with async_session() as session:
                for scope in dirty:
                    value = self._cache.get(scope, 0)
                    result = await session.execute(
                        select(RotationState).where(RotationState.scope == scope)
                    )
                    state = result.scalar_one_or_none()
                    if state is None:
                        session.add(RotationState(scope=scope, rr_index=value))
                    else:
                        state.rr_index = value
                await session.commit()
        except Exception:
            pass  # rotation still works; worst case restart resumes from an older counter

    async def _load(self, scope: str):
        async with async_session() as session:
            result = await session.execute(
                select(RotationState).where(RotationState.scope == scope)
            )
            state = result.scalar_one_or_none()
            if state:
                self._cache[scope] = state.rr_index or 0


# Global rotation index instance
_rotation_index = RotationIndex()


class Balancer:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def select_rule(self, strategy: Strategy, exclude_ids: set[int] | None = None) -> StrategyRule | None:
        """Select a rule. ``exclude_ids`` holds rules already tried in this
        request so failover/priority can advance to the next rule by priority."""
        result = await self.db.execute(
            select(StrategyRule)
            .where(StrategyRule.strategy_id == strategy.id, StrategyRule.is_active == True)
            .order_by(StrategyRule.priority, StrategyRule.id)
        )
        rules = result.scalars().all()
        if exclude_ids:
            rules = [r for r in rules if r.id not in exclude_ids]
        if not rules:
            return None

        method = strategy.lb_strategy

        if method == "round_robin":
            idx = await _rotation_index.next(f"rule_{strategy.id}", len(rules))
            return rules[idx]

        elif method == "weighted":
            total_weight = sum(r.weight for r in rules)
            if total_weight == 0:
                return rules[0]
            pick = random.uniform(0, total_weight)
            current = 0
            for r in rules:
                current += r.weight
                if pick <= current:
                    return r
            return rules[-1]

        elif method == "random":
            return random.choice(rules)

        elif method == "failover":
            return rules[0]

        elif method == "priority":
            return rules[0]

        elif method == "token_threshold":
            threshold = strategy.rule_token_threshold
            period = strategy.rule_token_period or "per_day"
            eligible = [r for r in rules if not _rule_token_tracker.is_over_threshold(
                strategy.id, r.id, threshold, period
            )]
            if not eligible:
                eligible = rules  # All over threshold, fallback
            return eligible[0]

        return rules[0]

    async def select_key(
        self,
        provider_id: int,
        strategy: Strategy | None = None,
        key_policy: Strategy | None = None,
    ) -> ApiKey | None:
        """Select an API key based on key strategy and switch thresholds.

        ``key_policy`` supplies the key_* settings (e.g. the Provider's own policy
        in strategy routing); when omitted it falls back to ``strategy``. This lets
        the model layer obey its own platform's key strategy while retries stay
        governed by the composite strategy.

        Keys in rate-limit cooldown are skipped for round_robin/random/weighted;
        once the cooldown elapses they are automatically revived to ``active``.
        For ``failover`` the cooling keys remain eligible as last-resort candidates
        and are ranked by cooldown so the healthiest key always wins.
        """
        result = await self.db.execute(
            select(ApiKey)
            .where(
                ApiKey.provider_id == provider_id,
                ApiKey.status.in_(["active", "untested", "rate_limited"]),
            )
            .order_by(ApiKey.id)
        )
        all_keys = result.scalars().all()
        keys = []
        cooling_keys: list[ApiKey] = []  # 仍在冷却窗口内，仅 failover 作为末位候选
        revived = False
        now = int(time.time())
        for k in all_keys:
            if k.status == "rate_limited":
                if (k.cooldown_until or 0) > now:
                    cooling_keys.append(k)
                    continue  # 仍在冷却窗口内，暂不参与常规轮询
                if revive_expired_cooldown(k, now):
                    revived = True
            keys.append(k)
        if revived:
            await self.db.commit()
        if not keys and not cooling_keys:
            return None

        if not strategy and not key_policy:
            # Fallback: weighted random（冷却中的 key 不参与，保持原行为）
            return self._weighted_random(keys)

        policy = key_policy if key_policy is not None else strategy
        key_method = policy.key_strategy
        switch_mode = policy.key_switch_mode
        rpm_threshold = policy.key_rpm_threshold
        count_threshold = policy.key_count_threshold

        # Filter out keys that have hit their threshold
        eligible = []
        for k in keys:
            over_rpm = switch_mode in ("rpm_threshold", "both") and _key_tracker.is_over_rpm(k.id, rpm_threshold)
            over_count = switch_mode in ("count_threshold", "both") and _key_tracker.is_over_count(k.id, count_threshold)
            if not over_rpm and not over_count:
                eligible.append(k)

        # If all keys are throttled, fall back to all keys (best effort)
        if not eligible:
            eligible = keys

        # Apply key strategy
        selected: ApiKey | None = None
        if key_method == "round_robin":
            selected = await self._round_robin_key(eligible, provider_id)
        elif key_method == "random":
            selected = random.choice(eligible) if eligible else None
        elif key_method == "failover":
            # 按冷却排序：未冷却/已解冻优先，冷却中的按解冻时间排后；
            # 全部冷却时选最早解冻的那个，不再返回 None。
            pool = list(eligible) + cooling_keys
            selected = self._failover_by_cooldown(pool, now)
        else:
            selected = self._weighted_random(eligible)

        # Record usage
        if selected:
            _key_tracker.record_usage(selected.id)

        return selected

    @staticmethod
    def _failover_by_cooldown(keys: list[ApiKey], now: int) -> ApiKey | None:
        """Pick the healthiest key for failover, ranked by cooldown then failures.

        Rank (ascending, lowest wins):
          1. still in rate-limit cooldown (0 = usable, 1 = cooling);
          2. cooldown_until (earlier unlock first, 0 for usable keys);
          3. fail_count (fewer consecutive auth failures first);
          4. id (stable tiebreak — reproduces the original ``eligible[0]``
             behaviour when every key is equally healthy).
        """
        if not keys:
            return None

        def rank(k: ApiKey):
            cooling = 1 if (k.status == "rate_limited" and int(k.cooldown_until or 0) > now) else 0
            until = int(k.cooldown_until or 0) if cooling else 0
            return (cooling, until, int(k.fail_count or 0), int(k.id or 0))

        return min(keys, key=rank)

    async def _round_robin_key(self, keys: list[ApiKey], provider_id: int) -> ApiKey | None:
        """Round-robin across keys of one provider, persisted across requests/restarts."""
        if not keys:
            return None
        idx = await _rotation_index.next(f"key_{provider_id}", len(keys))
        return keys[idx]

    def _weighted_random(self, keys: list[ApiKey]) -> ApiKey:
        if not keys:
            return None
        total = sum(k.weight for k in keys)
        if total == 0:
            return keys[0]
        pick = random.uniform(0, total)
        current = 0
        for k in keys:
            current += k.weight
            if pick <= current:
                return k
        return keys[-1]

    def get_key_usage(self, key_id: int) -> dict:
        """Get usage stats for a key (for debugging/display)."""
        return {
            "rpm": _key_tracker.get_rpm(key_id),
            "total": _key_tracker.get_total(key_id),
        }
