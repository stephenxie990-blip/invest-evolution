import logging
import random
from copy import deepcopy
from dataclasses import dataclass
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class Individual:
    """遗传算法个体（策略参数组合）"""
    params:     Dict
    fitness:    float = 0.0
    generation: int   = 0


class EvolutionEngine:
    """
    遗传算法策略进化引擎

    流程：初始化种群 → 适应度评估 → 选择 → 交叉 → 变异 → 精英保留
    """

    # 参数搜索空间
    PARAM_RANGES = {
        "ma_short":       (3,    10),
        "ma_long":        (15,   60),
        "rsi_period":     (7,    21),
        "rsi_oversold":   (20,   40),
        "rsi_overbought": (60,   80),
        "stop_loss_pct":  (0.03, 0.10),
        "take_profit_pct":(0.08, 0.20),
        "position_size":  (0.10, 0.30),
    }

    def __init__(
        self,
        population_size: int  = 20,
        mutation_rate:   float = 0.10,
        crossover_rate:  float = 0.70,
        elite_size:      int   = 2,
    ):
        self.population_size = population_size
        self.mutation_rate   = mutation_rate
        self.crossover_rate  = crossover_rate
        self.elite_size      = elite_size

        self.population:       List[Individual]     = []
        self.generation:       int                   = 0
        self.best_individual:  Optional[Individual] = None

    def initialize_population(self, base_params: Optional[Dict] = None):
        """初始化种群（第一个个体使用 base_params，其余随机）"""
        self.population = []
        for i in range(self.population_size):
            params = deepcopy(base_params) if (i == 0 and base_params) else self._random_params()
            self.population.append(Individual(params=params, fitness=0.0, generation=0))
        logger.info(f"初始化种群: {self.population_size} 个个体")

    def _random_params(self) -> Dict:
        params = {}
        for name, (lo, hi) in self.PARAM_RANGES.items():
            if name in ("ma_short", "ma_long", "rsi_period"):
                params[name] = random.randint(lo, hi)
            else:
                params[name] = random.uniform(lo, hi)
        return params

    def evolve(self, fitness_scores: List[float]) -> List[Individual]:
        """
        进化一代

        Args:
            fitness_scores: 与种群等长的适应度列表（通常是收益率 %）

        Returns:
            新种群
        """
        if len(fitness_scores) != len(self.population):
            logger.warning("适应度数量与种群大小不匹配: fitness=%s population=%s，自动对齐", len(fitness_scores), len(self.population))
            if len(fitness_scores) < len(self.population):
                pad = [fitness_scores[-1] if fitness_scores else -10.0] * (len(self.population) - len(fitness_scores))
                fitness_scores = list(fitness_scores) + pad
            else:
                fitness_scores = list(fitness_scores)[:len(self.population)]

        for ind, score in zip(self.population, fitness_scores):
            ind.fitness = score

        sorted_pop = sorted(self.population, key=lambda x: x.fitness, reverse=True)
        if self.best_individual is None or sorted_pop[0].fitness > self.best_individual.fitness:
            self.best_individual = deepcopy(sorted_pop[0])

        logger.info(
            f"第 {self.generation} 代: 最优={sorted_pop[0].fitness:.2f}%, "
            f"平均={sum(fitness_scores)/len(fitness_scores):.2f}%"
        )

        parents    = self._selection()
        offspring  = self._crossover(parents)
        offspring  = self._mutation(offspring)
        elites     = sorted_pop[:self.elite_size]
        self.population = offspring[:self.population_size - self.elite_size] + elites
        self.generation += 1
        return self.population

    def _selection(self) -> List[Individual]:
        """轮盘赌选择"""
        total = sum(max(ind.fitness, 0) for ind in self.population)
        if total <= 0:
            return random.choices(self.population, k=self.population_size)

        probs = [max(ind.fitness, 0) / total for ind in self.population]
        selected = []
        for _ in range(self.population_size):
            r, cumulative = random.random(), 0.0
            for i, p in enumerate(probs):
                cumulative += p
                if r <= cumulative:
                    selected.append(deepcopy(self.population[i]))
                    break
            else:
                selected.append(deepcopy(self.population[-1]))
        return selected

    def _crossover(self, parents: List[Individual]) -> List[Individual]:
        """单点交叉"""
        offspring = list(parents[:len(parents) // 4])  # 保留部分父母
        for _ in range(len(parents) // 2):
            if random.random() < self.crossover_rate:
                p1, p2 = random.choice(parents), random.choice(parents)
                c1, c2 = deepcopy(p1.params), deepcopy(p2.params)
                common = list(set(c1) & set(c2))
                if common:
                    key = random.choice(common)
                    c1[key], c2[key] = p2.params[key], p1.params[key]
                offspring.append(Individual(params=c1, fitness=0.0, generation=self.generation))
                offspring.append(Individual(params=c2, fitness=0.0, generation=self.generation))
            else:
                offspring.append(deepcopy(random.choice(parents)))
        return offspring

    def _mutation(self, offspring: List[Individual]) -> List[Individual]:
        """高斯变异"""
        for ind in offspring:
            if random.random() < self.mutation_rate:
                name = random.choice(list(self.PARAM_RANGES))
                if name in ind.params:
                    lo, hi = self.PARAM_RANGES[name]
                    delta = (hi - lo) * 0.10
                    new_val = ind.params[name] + random.gauss(0, delta)
                    ind.params[name] = max(lo, min(hi, new_val))
        return offspring

    def get_best_params(self) -> Dict:
        if self.best_individual:
            return self.best_individual.params
        if self.population:
            return max(self.population, key=lambda x: x.fitness).params
        return {}


__all__ = ["Individual", "EvolutionEngine"]
