import logging
import math
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from invest.shared import TradingPlan
from .contracts import (
    Action,
    EmergencyAction,
    EmergencyEvent,
    Position,
    SimulationResult,
    TradeRecord,
)
from ..risk.controller import EmergencyDetector, RiskController

logger = logging.getLogger(__name__)


class SimulatedTrader:
    """
    30天模拟交易引擎

    核心流程（每日 step()）：
    1. 组合级风控检查 → 必要时减仓/清仓
    2. 持仓止损/止盈/跟踪止盈检查（遵守 T+1）
    3. 持仓天数递增
    4. 空余仓位时按 TradingPlan 或内部策略买入
    5. 异常检测

    支持：
    - ATR 动态止损
    - 跟踪止盈（trailing stop）
    - A 股 T+1 规则
    - Phase 3 异常检测器
    - 真实交易成本（佣金 + 印花税 + 滑点）
    - 显式 `TradingPlan` 输入合同
    """

    def __init__(
        self,
        initial_capital: float = 100_000.0,
        max_positions: int = 5,
        position_size_pct: float = 0.20,
        commission_rate: float = 0.00025,   # 万2.5，双向
        stamp_tax_rate: float = 0.0005,     # 万5，仅卖出
        slippage_rate: float = 0.002,       # 滑点 0.2%
        enable_risk_control: bool = True,
        risk_policy: Optional[Dict[str, Any]] = None,
    ):
        self.initial_capital  = initial_capital
        self.cash             = initial_capital
        self.max_positions    = max_positions
        self.position_size_pct = position_size_pct
        self.commission_rate  = commission_rate
        self.stamp_tax_rate   = stamp_tax_rate
        self.slippage_rate    = slippage_rate
        self.enable_risk_control = enable_risk_control
        self.risk_policy = dict(risk_policy or {})

        # 风控
        if enable_risk_control:
            self.risk_controller = RiskController(policy=self.risk_policy)
            self.dynamic_stop = self.risk_controller.dynamic_stop

        self.positions:     List[Position]    = []
        self.trade_history: List[TradeRecord] = []
        self.daily_records: List[Dict]        = []

        self.stock_data: Dict[str, pd.DataFrame] = {}
        self.stock_info: Dict[str, dict]         = {}
        self.market_index_data: Optional[pd.DataFrame] = None
        self.current_date: str = ""

        self.cooldown_days: Dict[str, int] = {}
        self.hold_days:     Dict[str, int] = {}

        self.peak_value = initial_capital
        # Cache ATR by (code, date) to avoid stale volatility values across days.
        self.atr_cache: Dict[Tuple[str, str], float] = {}

        # TradingPlan
        self.trading_plan: Optional[TradingPlan] = None
        self._plan_bought: set = set()
        self.default_stop_loss_pct   = 0.05
        self.default_take_profit_pct = 0.15

        # 异常检测
        self.emergency_detector = EmergencyDetector(policy=self.risk_policy)
        self.emergency_events:  List[EmergencyEvent] = []

        if enable_risk_control:
            logger.info("✅ 风控系统已启用: ATR动态止损 + 组合风控 + 异常检测")

    @staticmethod
    def _classify_exit_trigger(reason: str) -> str:
        text = str(reason or "")
        lowered = text.lower()
        if "跟踪止盈" in text:
            return "trailing_stop"
        if "atr止损" in lowered or "止损" in text:
            return "stop_loss"
        if "atr止盈" in lowered or "止盈" in text:
            return "take_profit"
        if "风控减仓" in text:
            return "risk_reduce"
        if "风控清仓" in text:
            return "risk_close"
        if "异常卖出" in text:
            return "emergency_exit"
        if "结算平仓" in text:
            return "settlement"
        return "manual"

    # ===== 数据接口 =====

    def set_stock_data(self, stock_data: Dict[str, pd.DataFrame]):
        self.stock_data = stock_data
        self.atr_cache.clear()

    def set_stock_info(self, stock_info: Dict[str, dict]):
        self.stock_info = stock_info

    def set_market_index_data(self, index_data: Optional[pd.DataFrame]):
        self.market_index_data = index_data.copy() if index_data is not None else None

    def set_trading_plan(self, plan: TradingPlan):
        self.trading_plan  = plan
        self.max_positions = plan.max_positions
        self._plan_bought  = set()
        if plan.positions:
            self.default_stop_loss_pct   = plan.positions[0].stop_loss_pct
            self.default_take_profit_pct = plan.positions[0].take_profit_pct
        logger.info(f"📋 TradingPlan 已设置: {len(plan.positions)} 只候选, 最大持仓 {plan.max_positions}")

    # ===== 价格查询 =====

    def get_price(self, ts_code: str, date: str) -> Optional[float]:
        if ts_code not in self.stock_data:
            return None
        df = self.stock_data[ts_code]
        if df.empty:
            return None
        row = df[df["trade_date"] == date]
        if not row.empty:
            price = float(row.iloc[0]["close"])
            return price if 0 < price <= 10000 else None
        df_b = df[df["trade_date"] < date]
        if not df_b.empty:
            return float(df_b.iloc[-1]["close"])
        return None

    def get_day_metrics(self, ts_code: str, date: str) -> dict:
        if ts_code not in self.stock_data:
            return {}
        df = self.stock_data[ts_code]
        if df.empty:
            return {}
        row = df[df["trade_date"] == date]
        if row.empty:
            df_b = df[df["trade_date"] < date]
            if df_b.empty:
                return {}
            row = df_b.iloc[-1:]
        r = row.iloc[0]
        return {
            "open": float(r.get("open", 0)),
            "high": float(r.get("high", 0)),
            "low": float(r.get("low", 0)),
            "close": float(r.get("close", 0)),
            "volume": float(r.get("volume", 0)),
            "amount": float(r.get("amount", 0)),
            "pct_chg": float(r.get("pct_chg", 0)),
        }

    def get_historical_data(
        self, ts_code: str, end_date: str, days: int = 60
    ) -> Optional[pd.DataFrame]:
        if ts_code not in self.stock_data:
            return None
        df = self.stock_data[ts_code].copy()
        df = df[df["trade_date"] <= end_date]
        return df.tail(days) if len(df) > days else df

    def get_total_value(self) -> float:
        total = self.cash
        for pos in self.positions:
            price = self.get_price(pos.ts_code, self.current_date)
            if price:
                total += pos.shares * price
        return total

    # ===== 买入 / 卖出 =====

    def can_buy(self) -> bool:
        return len(self.positions) < self.max_positions

    def buy(
        self,
        ts_code: str,
        date: str,
        price: float,
        reason: str = "",
        stop_loss_pct: float = 0.05,
        take_profit_pct: float = 0.15,
        position_weight: Optional[float] = None,
        trailing_pct: Optional[float] = None,
        source: str = "algorithm",
    ) -> bool:
        if not self.can_buy():
            return False

        buy_price = price * (1 + self.slippage_rate)

        if not buy_price or buy_price <= 0 or math.isnan(self.cash) or math.isinf(self.cash) or self.cash <= 0:
            logger.warning(f"无效状态 {ts_code}: cash={self.cash}, price={buy_price}")
            return False

        weight = position_weight if position_weight is not None else self.position_size_pct
        available_cash = min(self.initial_capital * weight, self.initial_capital * 0.5, self.cash)

        shares = (int(available_cash / buy_price) // 100) * 100
        if shares <= 0:
            return False

        cost      = shares * buy_price
        commission = cost * self.commission_rate
        total_cost = cost + commission

        if total_cost > self.cash:
            shares = ((int((self.cash - commission) / buy_price)) // 100) * 100
            if shares <= 0:
                return False
            cost = shares * buy_price
            total_cost = cost + commission

        self.cash -= total_cost

        name = self.stock_info.get(ts_code, {}).get("name", ts_code)
        stop_loss = buy_price * (1 - stop_loss_pct)
        take_profit = None if (trailing_pct and trailing_pct > 0) else buy_price * (1 + take_profit_pct)

        pos = Position(
            ts_code=ts_code,
            name=name,
            entry_date=date,
            entry_price=buy_price,
            shares=shares,
            stop_loss=stop_loss,
            take_profit=take_profit,
            trailing_pct=trailing_pct,
            highest_price=buy_price,
            source=source,
            entry_reason=reason,
            plan_stop_loss_pct=stop_loss_pct,
            plan_take_profit_pct=take_profit_pct,
        )
        self.positions.append(pos)
        self.hold_days[ts_code] = 0

        metrics = self.get_day_metrics(ts_code, date)
        self.trade_history.append(TradeRecord(
            date=date, action=Action.BUY, ts_code=ts_code,
            price=buy_price, shares=shares, reason=reason,
            capital_before=self.cash + total_cost, capital_after=self.cash,
            open_price=metrics.get("open", 0), high_price=metrics.get("high", 0),
            low_price=metrics.get("low", 0), volume=metrics.get("volume", 0),
            amount=metrics.get("amount", 0), pct_chg=metrics.get("pct_chg", 0),
            source=source,
            entry_reason=reason,
            entry_date=date,
            entry_price=buy_price,
            holding_days=0,
            stop_loss_price=stop_loss or 0.0,
            take_profit_price=take_profit or 0.0,
            trailing_pct=trailing_pct,
        ))
        logger.info(
            f"买入 {ts_code} {shares}股 @ ¥{buy_price:.2f} | {date} | "
            f"涨跌幅:{metrics.get('pct_chg',0):+.2f}%"
        )
        return True

    def sell(
        self,
        position: Position,
        date: str,
        price: float,
        reason: str = "",
    ) -> float:
        return self._sell_partial(position, date, price, position.shares, reason)

    def _sell_partial(
        self,
        position: Position,
        date: str,
        price: float,
        shares: int,
        reason: str = "",
    ) -> float:
        if shares <= 0:
            return 0.0
        if shares > position.shares:
            shares = position.shares

        sell_price = price * (1 - self.slippage_rate)
        proceeds   = shares * sell_price
        sell_commission = proceeds * self.commission_rate
        stamp_tax  = proceeds * self.stamp_tax_rate
        net_proceeds = proceeds - sell_commission - stamp_tax

        buy_commission = shares * position.entry_price * self.commission_rate
        cost = shares * position.entry_price
        pnl  = net_proceeds - cost - buy_commission
        self.cash += net_proceeds

        metrics = self.get_day_metrics(position.ts_code, date)
        holding_days = int(self.hold_days.get(position.ts_code, 0) or 0)
        self.trade_history.append(TradeRecord(
            date=date, action=Action.SELL, ts_code=position.ts_code,
            price=sell_price, shares=shares, reason=reason,
            pnl=pnl, pnl_pct=pnl / cost * 100 if cost else 0,
            capital_before=self.cash - net_proceeds, capital_after=self.cash,
            open_price=metrics.get("open", 0), high_price=metrics.get("high", 0),
            low_price=metrics.get("low", 0), volume=metrics.get("volume", 0),
            amount=metrics.get("amount", 0), pct_chg=metrics.get("pct_chg", 0),
            source=position.source,
            entry_reason=position.entry_reason,
            exit_reason=reason,
            exit_trigger=self._classify_exit_trigger(reason),
            entry_date=position.entry_date,
            entry_price=position.entry_price,
            holding_days=holding_days,
            stop_loss_price=position.stop_loss or 0.0,
            take_profit_price=position.take_profit or 0.0,
            trailing_pct=position.trailing_pct,
        ))

        position.shares -= shares
        if position.shares <= 0:
            self.positions = [p for p in self.positions if p != position]
            self.cooldown_days[position.ts_code] = 3
            self.hold_days.pop(position.ts_code, None)

        logger.info(
            f"卖出 {position.ts_code} {shares}股 @ ¥{sell_price:.2f} | {date} | "
            f"盈亏: {pnl:+.2f} ({pnl/cost*100:+.2f}%)"
        )
        return pnl

    # ===== 止损止盈检查 =====

    def check_and_close_positions(self, date: str) -> List[float]:
        pnl_list = []
        for pos in self.positions[:]:
            if self.hold_days.get(pos.ts_code, 0) < 1:
                continue  # T+1

            current_price = self.get_price(pos.ts_code, date)
            if not current_price:
                # No executable market data for this symbol on/before date.
                # Never fall back to another symbol's price.
                continue
            pos.update_highest(current_price)

            # 跟踪止盈
            if pos.trailing_pct and pos.trailing_pct > 0 and pos.highest_price > 0:
                drop = (pos.highest_price - current_price) / pos.highest_price
                if drop >= pos.trailing_pct:
                    pnl_list.append(self.sell(
                        pos, date, current_price,
                        f"跟踪止盈(最高{pos.highest_price:.2f},回落{drop:.1%})"
                    ))
                    continue

            # 固定止损
            if pos.stop_loss and current_price <= pos.stop_loss:
                pnl_list.append(self.sell(pos, date, current_price, "止损"))
                continue

            # 固定止盈
            if pos.take_profit and current_price >= pos.take_profit:
                pnl_list.append(self.sell(pos, date, current_price, "止盈"))
                continue

            # ATR 动态止损
            if self.enable_risk_control:
                atr = self._get_atr(pos.ts_code, date)
                if atr and atr > 0:
                    stop_p = pos.entry_price - 2 * atr
                    take_p = pos.entry_price + 3 * atr
                    if current_price <= stop_p:
                        pnl_list.append(self.sell(pos, date, current_price, f"ATR止损({atr:.2f})"))
                        continue
                    if current_price >= take_p:
                        pnl_list.append(self.sell(pos, date, current_price, f"ATR止盈({atr:.2f})"))
                        continue

        return pnl_list

    def _get_atr(self, ts_code: str, date: str) -> Optional[float]:
        cache_key = (ts_code, date)
        if cache_key in self.atr_cache:
            return self.atr_cache[cache_key]
        df = self.stock_data.get(ts_code)
        if df is None:
            return None
        df_b = df[df["trade_date"] <= date]
        if len(df_b) < 20:
            return None
        atr = self.dynamic_stop.calculate_atr(df_b)
        if atr > 0:
            self.atr_cache[cache_key] = atr
        return atr if atr > 0 else None

    # ===== 风控动作 =====

    def check_portfolio_risk(self, date: str) -> dict:
        if not self.enable_risk_control:
            return {"action": "NONE", "reason": "", "drawdown": 0}
        current_value = self.get_total_value()
        if current_value > self.peak_value:
            self.peak_value = current_value
        drawdown = (self.peak_value - current_value) / self.peak_value if self.peak_value > 0 else 0
        if hasattr(self, "risk_controller") and self.positions:
            positions = {
                pos.ts_code: {
                    "shares": pos.shares,
                    "current_price": self.get_price(pos.ts_code, date) or pos.entry_price,
                }
                for pos in self.positions
            }
            hs300_data = None
            if self.market_index_data is not None and not self.market_index_data.empty:
                hs300_data = self.market_index_data[self.market_index_data["trade_date"] <= date].copy()
            result = self.risk_controller.check_portfolio(
                positions=positions,
                initial_capital=self.initial_capital,
                current_capital=current_value,
                hs300_data=hs300_data,
            )
            result.setdefault("drawdown", drawdown)
            return result
        if drawdown > 0.12:
            return {"action": "CLOSE_ALL", "reason": f"回撤{drawdown:.1%}>12%，清仓", "drawdown": drawdown}
        if drawdown > 0.08:
            return {"action": "REDUCE", "reason": f"回撤{drawdown:.1%}>8%，减仓50%", "drawdown": drawdown}
        return {"action": "NONE", "reason": "", "drawdown": drawdown}

    def check_can_open_position(self, ts_code: str, weight: float = 0.20) -> Tuple[bool, str]:
        if not self.enable_risk_control:
            return True, "风控未启用"
        if weight > 0.20:
            return False, f"单股仓位{weight:.1%}>20%"
        if hasattr(self, "risk_controller"):
            sector = self.risk_controller.portfolio_risk.get_industry(ts_code)
            sector_exp = weight + sum(
                pos.shares * pos.entry_price / max(self.get_total_value(), 1)
                for pos in self.positions
                if self.risk_controller.portfolio_risk.get_industry(pos.ts_code) == sector
            )
            if sector_exp > 0.30:
                return False, f"行业'{sector}'{sector_exp:.1%}>30%"
        return True, "通过"

    def force_close_all(self, date: str, reason: str = "风控清仓") -> List[float]:
        return [self.sell(pos, date, self.get_price(pos.ts_code, date) or pos.entry_price, reason)
                for pos in self.positions[:]]

    def reduce_positions(self, date: str, reduce_pct: float = 0.5) -> List[float]:
        pnl_list = []
        n = int(len(self.positions) * reduce_pct)
        for pos in self.positions[:n]:
            price = self.get_price(pos.ts_code, date)
            if not price:
                continue
            half = pos.shares // 2
            if half <= 0:
                continue
            pnl = self._sell_partial(pos, date, price, half, "风控减仓50%")
            pnl_list.append(pnl)
        return pnl_list

    # ===== 按计划执行买入 =====

    def _execute_plan_step(self, date: str):
        if not self.trading_plan:
            return
        for pos_plan in self.trading_plan.positions:
            if not self.can_buy():
                break
            code = pos_plan.code
            if code in self._plan_bought:
                continue
            if any(p.ts_code == code for p in self.positions):
                continue
            if code in self.cooldown_days:
                continue
            price = self.get_price(code, date)
            if not price or price <= 0:
                continue
            if self.enable_risk_control:
                ok, reason = self.check_can_open_position(code, pos_plan.weight)
                if not ok:
                    logger.debug(f"风控拦截 {code}: {reason}")
                    continue
            success = self.buy(
                ts_code=code, date=date, price=price, reason=pos_plan.reason,
                stop_loss_pct=pos_plan.stop_loss_pct, take_profit_pct=pos_plan.take_profit_pct,
                position_weight=pos_plan.weight, trailing_pct=pos_plan.trailing_pct,
                source=pos_plan.source,
            )
            if success:
                self._plan_bought.add(code)

    # ===== 异常处理 =====

    def _handle_emergency(self, event: EmergencyEvent, date: str):
        if event.action == EmergencyAction.TIGHTEN_STOP:
            for pos in self.positions:
                if event.affected_codes and pos.ts_code not in event.affected_codes:
                    continue
                price = self.get_price(pos.ts_code, date)
                if price:
                    new_stop = price * 0.98
                    if pos.stop_loss is None or new_stop > pos.stop_loss:
                        pos.stop_loss = new_stop

        elif event.action == EmergencyAction.SELL_WORST:
            for code in (event.affected_codes or []):
                for pos in self.positions[:]:
                    if pos.ts_code == code and self.hold_days.get(code, 0) >= 1:
                        price = self.get_price(code, date)
                        if price:
                            self.sell(pos, date, price, f"异常卖出:{event.description}")
                        break

        elif event.action == EmergencyAction.REDUCE_ALL:
            self.reduce_positions(date, 0.5)

        self.emergency_events.append(event)

    # ===== 单日步 =====

    def step(self, date: str) -> Dict:
        self.current_date = date
        trade_count_before = len(self.trade_history)

        # 冷却期递减
        for code in list(self.cooldown_days):
            self.cooldown_days[code] -= 1
            if self.cooldown_days[code] <= 0:
                del self.cooldown_days[code]

        result = {
            "date": date,
            "action": Action.HOLD,
            "risk_action": "NONE",
        }

        # 组合级风控
        if self.enable_risk_control and self.positions:
            risk = self.check_portfolio_risk(date)
            if risk["action"] == "CLOSE_ALL":
                logger.warning(f"⚠️ 风控: {risk['reason']}")
                self.force_close_all(date)
                result["risk_action"] = "CLOSE_ALL"
            elif risk["action"] == "REDUCE":
                logger.warning(f"⚠️ 风控: {risk['reason']}")
                self.reduce_positions(date)
                result["risk_action"] = "REDUCE"

        # 持仓止损/止盈
        self.check_and_close_positions(date)

        # 持仓天数 +1（T+1）
        for code in list(self.hold_days):
            self.hold_days[code] += 1

        # 买入
        if self.can_buy():
            self._execute_plan_step(date)
            if len(self.trade_history) > trade_count_before:
                result["action"] = Action.BUY

        # 异常检测
        if self.enable_risk_control and self.positions:
            events = self.emergency_detector.check(self, date)
            for e in events:
                self._handle_emergency(e, date)
            if events:
                result["emergency_events"] = [
                    {"type": e.event_type.value, "action": e.action.value, "description": e.description}
                    for e in events
                ]

        trades_today = self.trade_history[trade_count_before:]
        if trades_today:
            has_buy = any(t.action == Action.BUY for t in trades_today)
            has_sell = any(t.action == Action.SELL for t in trades_today)
            if has_buy and not has_sell:
                result["action"] = Action.BUY
            elif has_sell and not has_buy:
                result["action"] = Action.SELL
            else:
                result["action"] = Action.HOLD

        end_position_value = sum(
            pos.shares * (self.get_price(pos.ts_code, date) or pos.entry_price)
            for pos in self.positions
        )
        result["cash"] = self.cash
        result["positions"] = len(self.positions)
        result["position_value"] = end_position_value
        result["total_value"] = self.cash + end_position_value
        result["trades_today"] = len(trades_today)

        self.daily_records.append(result)
        return result

    # ===== 完整模拟 =====

    def run_simulation(
        self,
        start_date: str,
        trading_dates: List[str],
    ) -> SimulationResult:
        """
        运行完整模拟交易

        Args:
            start_date: 起始日期 YYYYMMDD
            trading_dates: 所有交易日期列表

        Returns:
            SimulationResult
        """
        logger.info(f"开始模拟: {start_date}, 共 {len(trading_dates)} 天")
        self.emergency_detector.reset()
        self.emergency_events.clear()

        start_idx = next((i for i, d in enumerate(trading_dates) if d >= start_date), 0)

        for i in range(start_idx, len(trading_dates)):
            date = trading_dates[i]
            self.step(date)
            if i % 5 == 0:
                logger.info(
                    f"第{i - start_idx + 1}天: {date}, "
                    f"现金{self.cash:.2f}, 持仓{len(self.positions)}只"
                )

        # 结算
        final_date = trading_dates[-1]
        for pos in self.positions[:]:
            price = self.get_price(pos.ts_code, final_date) or pos.entry_price
            self.sell(pos, final_date, price, "结算平仓")

        final_value = self.cash
        total_pnl   = final_value - self.initial_capital
        return_pct  = total_pnl / self.initial_capital * 100

        wins   = [t for t in self.trade_history if t.action == Action.SELL and t.pnl > 0]
        losses = [t for t in self.trade_history if t.action == Action.SELL and t.pnl <= 0]

        per_stock_pnl: Dict[str, float] = {}
        for t in self.trade_history:
            if t.action == Action.SELL:
                per_stock_pnl[t.ts_code] = per_stock_pnl.get(t.ts_code, 0) + t.pnl

        em_summary = self.emergency_detector.get_summary()
        if em_summary["total_events"] > 0:
            logger.info(f"⚠️ 异常事件: {em_summary['total_events']}次 ({em_summary['by_type']})")

        logger.info(f"模拟完成: 收益率{return_pct:.2f}%, 赢{len(wins)}/输{len(losses)}")

        return SimulationResult(
            initial_capital=self.initial_capital,
            final_capital=self.cash,
            final_value=final_value,
            return_pct=return_pct,
            total_trades=len(wins) + len(losses),
            winning_trades=len(wins),
            losing_trades=len(losses),
            win_rate=len(wins) / (len(wins) + len(losses)) if (wins or losses) else 0,
            total_pnl=total_pnl,
            positions=self.positions,
            trade_history=self.trade_history,
            daily_records=self.daily_records,
            per_stock_pnl=per_stock_pnl,
        )

# ============================================================
# day_by_day_simulator.py

__all__ = ["SimulatedTrader", "run_simulation_with_plan"]



def run_simulation_with_plan(
    stock_data: Dict[str, pd.DataFrame],
    trading_plan: TradingPlan,
    trading_dates: List[str],
    *,
    initial_capital: float,
    max_positions: int,
    position_size_pct: float,
):
    trader = SimulatedTrader(
        initial_capital=initial_capital,
        max_positions=max_positions,
        position_size_pct=position_size_pct,
    )
    trader.set_stock_data(stock_data)
    trader.set_trading_plan(trading_plan)
    return trader.run_simulation(trading_dates[0], trading_dates)
