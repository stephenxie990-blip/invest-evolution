# 数据管理与调用接口整体规划

## 目标
- 明确项目数据分层边界与职责
- 设计统一且可扩展的数据访问接口
- 在不破坏现有日频主链路的前提下落地核心实现
- 为日频增强、事件型数据、日内数据提供清晰入口

## 阶段
- [x] 盘点当前数据层与调用链
- [x] 确定目标分层与接口设计
- [x] 实施核心接口与服务
- [x] 补充测试与运行验证
- [x] 输出部署与使用说明

## 已落地分层
- 日频主链：`TrainingDatasetBuilder`
- 日频增强：`CapitalFlowDatasetService`
- 事件层：`EventDatasetService`
- 日内层：`IntradayDatasetBuilder`
- 统一高层入口：`DataManager`

## 约束
- 保持现有 `DataManager` 日频主入口稳定
- 避免把事件型 / 高频数据强塞进通用日频 frame
- 新接口优先复用 `repository` 现有查询能力
