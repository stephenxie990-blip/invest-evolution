import logging
import importlib
import random
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Tuple

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class OptimizedParams:
    """优化后的参数"""
    params: Dict[str, float]
    fitness: float
    stability_score: float  # 稳健性得分
    stage: str  # 优化阶段


class GaussianProcessModel:
    """
    高斯过程模型

    用于贝叶斯优化中的代理模型
    """

    def __init__(self):
        self.X_train = []
        self.y_train = []
        self.length_scale = 1.0
        self.noise = 0.1

    def fit(self, X: np.ndarray, y: np.ndarray):
        """训练高斯过程模型"""
        self.X_train = X
        self.y_train = y

    def predict(self, X: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        """
        预测

        Returns:
            (mean, std)
        """
        if len(self.X_train) == 0:
            return np.zeros(len(X)), np.ones(len(X))

        # 简化的RBF核函数
        X = np.array(X)
        X_train = np.array(self.X_train)

        # 计算核矩阵
        K = self._rbf_kernel(X_train, X_train) + self.noise ** 2 * np.eye(len(X_train))

        # 计算预测均值
        K_star = self._rbf_kernel(X_train, X)
        K_inv = np.linalg.pinv(K)

        # 均值
        mean = K_star.T @ K_inv @ self.y_train

        # 方差
        k_xx = self._rbf_kernel(X, X)
        var = k_xx - K_star.T @ K_inv @ K_star
        var = np.diag(var)
        var = np.maximum(var, 0)

        return mean, np.sqrt(var)

    def _rbf_kernel(self, X1: np.ndarray, X2: np.ndarray) -> np.ndarray:
        """RBF核函数"""
        X1 = np.array(X1)
        X2 = np.array(X2)

        if X1.ndim == 1:
            X1 = X1.reshape(-1, 1)
        if X2.ndim == 1:
            X2 = X2.reshape(-1, 1)

        # 计算欧氏距离
        dist = np.sum((X1[:, np.newaxis, :] - X2[np.newaxis, :, :]) ** 2, axis=2)

        return np.exp(-0.5 * dist / (self.length_scale ** 2))


class BayesianOptimizer:
    """
    贝叶斯优化器

    用于第一阶段：快速定位有效参数区间
    """

    def __init__(
        self,
        param_bounds: Dict[str, Tuple[float, float]],
        n_iter: int = 50,
        acquisition: str = "ei",  # ei, ucb
    ):
        self.param_bounds = param_bounds
        self.n_iter = n_iter
        self.acquisition = acquisition
        self.gp = GaussianProcessModel()
        self.best_params: Dict[str, float] | None = None
        self.best_fitness = float('-inf')
        self.history: list[tuple[Dict[str, float], float]] = []

    def _sample_random(self) -> Dict[str, float]:
        """随机采样参数"""
        params = {}
        for name, (low, high) in self.param_bounds.items():
            params[name] = random.uniform(low, high)
        return params

    def _to_vector(self, params: Dict[str, float]) -> np.ndarray:
        """参数转向量"""
        names = list(self.param_bounds.keys())
        return np.array([params[n] for n in names])

    def _from_vector(self, vec: np.ndarray) -> Dict[str, float]:
        """向量转参数"""
        names = list(self.param_bounds.keys())
        return {names[i]: vec[i] for i in range(len(names))}

    def _acquisition_ei(self, mean: np.ndarray, std: np.ndarray, best_y: float) -> np.ndarray:
        """Expected Improvement采集函数"""
        z = (mean - best_y) / (std + 1e-8)
        ei = (mean - best_y) * self._norm_cdf(z) + std * self._norm_pdf(z)
        return ei

    def _norm_cdf(self, x: Any) -> Any:
        """正态分布CDF (使用erf)"""
        # 使用scipy或手动实现
        try:
            stats = importlib.import_module("scipy.stats")
            return stats.norm.cdf(x)
        except ImportError:
            # 手动实现近似
            return 0.5 * (1 + np.sign(x) * np.sqrt(1 - np.exp(-2 * x * x / np.pi)))

    def _norm_pdf(self, x: Any) -> Any:
        """正态分布PDF"""
        return np.exp(-0.5 * x ** 2) / np.sqrt(2 * np.pi)

    def optimize(
        self,
        fitness_func: Callable[[Dict[str, float]], float],
    ) -> Tuple[Dict[str, float], float, Dict[str, Tuple[float, float]]]:
        """
        贝叶斯优化

        Args:
            fitness_func: 适应度函数

        Returns:
            (最优参数, 最优适应度)
        """
        logger.info(f"第一阶段：贝叶斯优化 ({self.n_iter}次评估)")

        # 初始化：随机采样
        for _ in range(10):
            params = self._sample_random()
            fitness = fitness_func(params)
            self.history.append((params.copy(), fitness))

            if fitness > self.best_fitness:
                self.best_fitness = fitness
                self.best_params = params.copy()

        # 迭代优化
        for i in range(self.n_iter):
            # 训练高斯过程
            X = np.array([self._to_vector(p) for p, _ in self.history])
            y = np.array([f for _, f in self.history])
            self.gp.fit(X, y)

            # 候选点采样
            candidates = [self._sample_random() for _ in range(100)]

            # 计算采集函数值
            best_acq = float('-inf')
            best_candidate: Dict[str, float] | None = None

            for candidate in candidates:
                x = self._to_vector(candidate).reshape(1, -1)
                mean, std = self.gp.predict(x)

                if self.acquisition == "ei":
                    acq = self._acquisition_ei(mean[0], std[0], self.best_fitness)
                else:
                    acq = mean[0] - 2 * std[0]  # UCB

                if acq > best_acq:
                    best_acq = acq
                    best_candidate = candidate

            # 评估候选点
            if best_candidate is None:
                best_candidate = self._sample_random()
            fitness = fitness_func(best_candidate)
            self.history.append((best_candidate.copy(), fitness))

            if fitness > self.best_fitness:
                self.best_fitness = fitness
                self.best_params = best_candidate.copy()

            if (i + 1) % 10 == 0:
                logger.info(f"  迭代 {i+1}/{self.n_iter}: 最优={self.best_fitness:.4f}")

        # 返回最优参数及其置信区间
        param_ranges = {}
        for name in self.param_bounds.keys():
            values = [p[name] for p, _ in self.history]
            low = np.percentile(values, 20)
            high = np.percentile(values, 80)
            param_ranges[name] = (low, high)

        logger.info(f"贝叶斯优化完成: 最优={self.best_fitness:.4f}")
        logger.info(f"参数置信区间: {param_ranges}")

        best_params = self.best_params or self._sample_random()
        return best_params, self.best_fitness, param_ranges


class GeneticOptimizer:
    """
    遗传算法优化器

    用于第二阶段：区间内精细搜索
    """

    def __init__(
        self,
        param_bounds: Dict[str, Tuple[float, float]],
        population_size: int = 50,
        n_generations: int = 50,
        mutation_rate: float = 0.1,
        crossover_rate: float = 0.8,
        elite_ratio: float = 0.1,
        regularization: float = 0.01,
    ):
        self.param_bounds = param_bounds
        self.population_size = population_size
        self.n_generations = n_generations
        self.mutation_rate = mutation_rate
        self.crossover_rate = crossover_rate
        self.elite_ratio = elite_ratio
        self.regularization = regularization

        self.best_params = None
        self.best_fitness = float('-inf')

    def _initialize_population(self) -> List[Dict[str, float]]:
        """初始化种群"""
        population = []
        for _ in range(self.population_size):
            individual = {}
            for name, (low, high) in self.param_bounds.items():
                individual[name] = random.uniform(low, high)
            population.append(individual)
        return population

    def _evaluate(
        self,
        population: List[Dict[str, float]],
        fitness_func: Callable[[Dict[str, float]], float],
    ) -> List[Tuple[Dict[str, float], float]]:
        """评估种群"""
        results = []
        for individual in population:
            # 适应度 = 收益 - λ × 参数复杂度
            fitness = fitness_func(individual)

            # 正则化惩罚：参数越极端，惩罚越大
            penalty = 0
            for name, (low, high) in self.param_bounds.items():
                value = individual[name]
                # 归一化到[0,1]
                normalized = (value - low) / (high - low)
                # 远离中心点(0.5)越多，惩罚越大
                penalty += abs(normalized - 0.5) * self.regularization

            fitness -= penalty
            results.append((individual, fitness))

        return results

    def _select(
        self,
        results: List[Tuple[Dict[str, float], float]],
    ) -> List[Dict[str, float]]:
        """选择（锦标赛）"""
        selected = []
        for _ in range(self.population_size):
            # 随机选3个
            candidates = random.sample(results, min(3, len(results)))
            # 选最好的
            best = max(candidates, key=lambda x: x[1])
            selected.append(best[0].copy())
        return selected

    def _crossover(
        self,
        parent1: Dict[str, float],
        parent2: Dict[str, float],
    ) -> Tuple[Dict[str, float], Dict[str, float]]:
        """交叉"""
        if random.random() > self.crossover_rate:
            return parent1.copy(), parent2.copy()

        child1, child2 = {}, {}
        for name in self.param_bounds.keys():
            if random.random() < 0.5:
                child1[name] = parent1[name]
                child2[name] = parent2[name]
            else:
                child1[name] = parent2[name]
                child2[name] = parent1[name]

        return child1, child2

    def _mutate(self, individual: Dict[str, float]) -> Dict[str, float]:
        """变异"""
        mutated = individual.copy()
        for name, (low, high) in self.param_bounds.items():
            if random.random() < self.mutation_rate:
                # 高斯变异
                std = (high - low) * 0.1
                mutated[name] = mutated[name] + random.gauss(0, std)
                # 边界处理
                mutated[name] = max(low, min(high, mutated[name]))
        return mutated

    def optimize(
        self,
        fitness_func: Callable[[Dict[str, float]], float],
        param_ranges: Dict[str, Tuple[float, float]] | None = None,
    ) -> Tuple[Dict[str, float], float]:
        """
        遗传算法优化

        Args:
            fitness_func: 适应度函数
            param_ranges: 参数范围（可选，用于约束搜索空间）

        Returns:
            (最优参数, 最优适应度)
        """
        # 使用给定的范围或默认范围
        bounds = param_ranges if param_ranges else self.param_bounds
        self.param_bounds = bounds

        logger.info(f"第二阶段：遗传算法 ({self.n_generations}代)")

        # 初始化
        population = self._initialize_population()

        for gen in range(self.n_generations):
            # 评估
            results = self._evaluate(population, fitness_func)

            # 记录最优
            for individual, fitness in results:
                if fitness > self.best_fitness:
                    self.best_fitness = fitness
                    self.best_params = individual.copy()

            # 排序
            results.sort(key=lambda x: x[1], reverse=True)

            # 精英保留
            elite_count = int(self.population_size * self.elite_ratio)
            new_population = [r[0].copy() for r in results[:elite_count]]

            # 选择
            selected = self._select(results)

            # 交叉和变异
            while len(new_population) < self.population_size:
                parent1, parent2 = random.sample(selected, 2)
                child1, child2 = self._crossover(parent1, parent2)
                child1 = self._mutate(child1)
                child2 = self._mutate(child2)
                new_population.append(child1)
                if len(new_population) < self.population_size:
                    new_population.append(child2)

            population = new_population

            if (gen + 1) % 10 == 0:
                logger.info(f"  代数 {gen+1}/{self.n_generations}: 最优={self.best_fitness:.4f}")

        logger.info(f"遗传算法完成: 最优={self.best_fitness:.4f}")
        best_params = self.best_params or self._initialize_population()[0]
        return best_params, self.best_fitness


class RobustnessValidator:
    """
    参数稳健性检验器

    用于第三阶段：验证参数稳定性
    """

    def __init__(
        self,
        perturbation_range: float = 0.1,  # ±10%
        min_stability_score: float = 0.7,
    ):
        self.perturbation_range = perturbation_range
        self.min_stability_score = min_stability_score

    def validate(
        self,
        params: Dict[str, float],
        fitness_func: Callable[[Dict[str, float]], float],
        param_bounds: Dict[str, Tuple[float, float]],
    ) -> Tuple[float, bool]:
        """
        验证参数稳健性

        Args:
            params: 最优参数
            fitness_func: 适应度函数
            param_bounds: 参数范围

        Returns:
            (稳健性得分, 是否通过)
        """
        logger.info("第三阶段：参数稳健性检验")

        # 评估最优参数
        best_fitness = fitness_func(params)

        # 周围采样
        n_samples = 20
        perturbed_fitness = []

        for _ in range(n_samples):
            perturbed = self._perturb_params(params, param_bounds)
            fitness = fitness_func(perturbed)
            perturbed_fitness.append(fitness)

        # 计算稳健性得分
        # 稳健性 = 周围参数的平均收益 / 最优参数收益
        avg_perturbed = float(np.mean(perturbed_fitness))
        stability_score = avg_perturbed / best_fitness if best_fitness > 0 else 0.0

        # 额外检查：参数微调后收益是否剧变
        fitness_variance = float(np.std(perturbed_fitness))
        variance_penalty = min(fitness_variance / abs(best_fitness), 1.0) if best_fitness != 0 else 1.0

        # 最终稳健性得分
        final_score = float(stability_score * (1 - variance_penalty * 0.5))

        passed = bool(final_score >= self.min_stability_score)

        logger.info(f"  最优收益: {best_fitness:.4f}")
        logger.info(f"  周围平均收益: {avg_perturbed:.4f}")
        logger.info(f"  收益方差: {fitness_variance:.4f}")
        logger.info(f"  稳健性得分: {final_score:.4f} ({'通过' if passed else '未通过'})")

        return final_score, passed

    def _perturb_params(
        self,
        params: Dict[str, float],
        param_bounds: Dict[str, Tuple[float, float]],
    ) -> Dict[str, float]:
        """微调参数"""
        perturbed = params.copy()
        for name, (low, high) in param_bounds.items():
            # 随机微调±10%
            range_size = high - low
            delta = random.uniform(-1, 1) * self.perturbation_range * range_size
            perturbed[name] = params[name] + delta
            perturbed[name] = max(low, min(high, perturbed[name]))
        return perturbed


class ThreeStageOptimizer:
    """
    三阶段优化器

    1. 贝叶斯优化 → 快速定位
    2. 遗传算法 → 精细搜索
    3. 稳健性检验 → 避免过拟合
    """

    def __init__(
        self,
        param_bounds: Dict[str, Tuple[float, float]],
        # 贝叶斯优化参数
        bayesian_n_iter: int = 50,
        # 遗传算法参数
        ga_population: int = 50,
        ga_generations: int = 50,
        # 稳健性检验参数
        perturbation_range: float = 0.1,
        min_stability: float = 0.7,
    ):
        self.param_bounds = param_bounds

        self.bayesian = BayesianOptimizer(
            param_bounds=param_bounds,
            n_iter=bayesian_n_iter,
        )

        self.genetic = GeneticOptimizer(
            param_bounds=param_bounds,
            population_size=ga_population,
            n_generations=ga_generations,
        )

        self.robustness = RobustnessValidator(
            perturbation_range=perturbation_range,
            min_stability_score=min_stability,
        )

    def optimize(
        self,
        fitness_func: Callable[[Dict[str, float]], float],
    ) -> OptimizedParams:
        """
        执行三阶段优化

        Args:
            fitness_func: 适应度函数 (参数) -> 收益

        Returns:
            优化后的参数
        """
        logger.info("=" * 60)
        logger.info("开始三阶段优化")
        logger.info("=" * 60)

        # 第一阶段：贝叶斯优化
        logger.info("\n" + "=" * 40)
        best_params_1, fitness_1, param_ranges = self.bayesian.optimize(fitness_func)

        # 第二阶段：遗传算法
        logger.info("\n" + "=" * 40)
        best_params_2, fitness_2 = self.genetic.optimize(
            fitness_func,
            param_ranges=param_ranges,
        )

        # 选择第二阶段更好的结果
        if fitness_2 > fitness_1:
            best_params = best_params_2
            best_fitness = fitness_2
        else:
            best_params = best_params_1
            best_fitness = fitness_1

        # 第三阶段：稳健性检验
        logger.info("\n" + "=" * 40)
        stability_score, passed = self.robustness.validate(
            best_params,
            fitness_func,
            self.param_bounds,
        )

        # 如果未通过，返回原始参数（保守）
        if not passed:
            logger.warning("参数稳健性检验未通过，使用保守参数")
            # 可以选择返回更保守的参数，或者降低期望

        logger.info("\n" + "=" * 60)
        logger.info("三阶段优化完成")
        logger.info(f"最优参数: {best_params}")
        logger.info(f"适应度: {best_fitness:.4f}")
        logger.info(f"稳健性得分: {stability_score:.4f}")
        logger.info("=" * 60)

        return OptimizedParams(
            params=best_params,
            fitness=best_fitness,
            stability_score=stability_score,
            stage="completed" if passed else "conservative",
        )

__all__ = [
    "OptimizedParams",
    "GaussianProcessModel",
    "BayesianOptimizer",
    "GeneticOptimizer",
    "RobustnessValidator",
    "ThreeStageOptimizer",
]
