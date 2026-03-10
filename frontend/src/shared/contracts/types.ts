import { z } from 'zod'

export const FlatErrorSchema = z.object({
  error: z.string(),
}).passthrough()

export const StatusErrorSchema = z.object({
  status: z.literal('error'),
  error: z.string(),
}).passthrough()

export const RuntimeStatusSchema = z.object({
  ts: z.string(),
  detail_mode: z.enum(['fast', 'slow']).catch('fast'),
  instance_id: z.string().optional(),
  workspace: z.string().optional(),
  strategy_dir: z.string().optional(),
  model: z.string().optional(),
  autopilot_enabled: z.boolean().optional(),
  heartbeat_enabled: z.boolean().optional(),
  training_interval_sec: z.number().int().optional(),
  heartbeat_interval_sec: z.number().int().optional(),
  runtime: z.record(z.string(), z.unknown()),
  brain: z.record(z.string(), z.unknown()),
  body: z.record(z.string(), z.unknown()),
  memory: z.record(z.string(), z.unknown()),
  bridge: z.record(z.string(), z.unknown()),
  plugins: z.record(z.string(), z.unknown()),
  strategies: z.record(z.string(), z.unknown()),
  config: z.record(z.string(), z.unknown()),
  data: z.record(z.string(), z.unknown()),
  training_lab: z.record(z.string(), z.unknown()),
}).passthrough()

export const LabStatusEnvelopeSchema = z.object({
  mode: z.enum(['quick', 'deep']),
  snapshot: RuntimeStatusSchema,
}).passthrough()

export const ArtifactRowSchema = z.record(z.string(), z.unknown())

export const ArtifactListSchema = z.object({
  count: z.number().int(),
  items: z.array(ArtifactRowSchema),
}).passthrough()

export const TrainingPlanSchema = z.record(z.string(), z.unknown())

export const TrainingExecutionSchema = z.record(z.string(), z.unknown())

export const RuntimePathsSchema = z.object({
  status: z.string(),
  config: z.record(z.string(), z.unknown()),
}).passthrough()

export const EvolutionConfigSchema = z.object({
  status: z.string(),
  config: z.record(z.string(), z.unknown()),
}).passthrough()

export const InvestmentModelsSchema = z.object({
  items: z.array(z.string()),
  active_model: z.string(),
  active_config: z.string(),
}).passthrough()

export const LeaderboardEntrySchema = z.record(z.string(), z.unknown())

export const LeaderboardSchema = z.object({
  generated_at: z.string().optional(),
  total_records: z.number().int().optional(),
  total_models: z.number().int().optional(),
  entries: z.array(LeaderboardEntrySchema).optional(),
  best_model: LeaderboardEntrySchema.nullable().optional(),
  regime_leaderboards: z.record(z.string(), z.array(LeaderboardEntrySchema)).optional(),
}).passthrough()

export const AllocationSchema = z.object({
  as_of_date: z.string().optional(),
  regime: z.string().optional(),
  active_models: z.array(z.string()).optional(),
  model_weights: z.record(z.string(), z.number()).optional(),
  selected_configs: z.record(z.string(), z.string()).optional(),
  cash_reserve: z.number().optional(),
  confidence: z.number().optional(),
  reasoning: z.string().optional(),
  metadata: z.record(z.string(), z.unknown()).optional(),
}).passthrough()

export const AllocatorSchema = z.object({
  leaderboard_generated_at: z.string().nullable().optional(),
  allocation: AllocationSchema,
}).passthrough()

export const StrategyGeneSchema = z.object({
  gene_id: z.string(),
  name: z.string(),
  kind: z.string(),
  path: z.string(),
  enabled: z.boolean().optional(),
  priority: z.number().int().optional(),
  description: z.string().optional(),
  metadata: z.record(z.string(), z.unknown()).optional(),
}).passthrough()

export const StrategyGeneListSchema = z.object({
  count: z.number().int(),
  items: z.array(StrategyGeneSchema),
}).passthrough()

export const StrategyReloadSchema = z.object({
  count: z.number().int(),
  genes: z.array(StrategyGeneSchema.partial().passthrough()),
}).passthrough()

export const CountedItemsSchema = z.object({
  count: z.number().int(),
  items: z.array(z.record(z.string(), z.unknown())),
}).passthrough()

export const DataStatusSchema = z.object({
  db_path: z.string(),
  size_mb: z.number(),
  stock_count: z.number().int(),
  kline_count: z.number().int(),
  latest_date: z.string(),
  schema: z.string(),
  quality: z.record(z.string(), z.unknown()),
  detail_mode: z.enum(['fast', 'slow']).catch('fast'),
  financial_count: z.number().int().optional(),
  calendar_count: z.number().int().optional(),
  status_count: z.number().int().optional(),
  factor_count: z.number().int().optional(),
  capital_flow_count: z.number().int().optional(),
  dragon_tiger_count: z.number().int().optional(),
  intraday_60m_count: z.number().int().optional(),
  index_count: z.number().int().optional(),
  index_kline_count: z.number().int().optional(),
  index_latest_date: z.string().optional(),
}).passthrough()

export const DataDownloadSchema = z.object({
  status: z.enum(['started', 'running']),
  message: z.string(),
}).passthrough()

export const ConnectedEventSchema = z.object({
  status: z.literal('connected'),
}).passthrough()

export const CycleStartEventSchema = z.object({
  cycle_id: z.number().int(),
  cutoff_date: z.string(),
  phase: z.string(),
  requested_data_mode: z.string(),
  llm_mode: z.string(),
  timestamp: z.string(),
}).passthrough()

export const CycleCompleteEventSchema = z.object({
  cycle_id: z.number().int(),
  cutoff_date: z.string(),
  return_pct: z.number(),
  is_profit: z.boolean(),
  selected_count: z.number().int(),
  selected_stocks: z.array(z.string()).optional(),
  trade_count: z.number().int(),
  final_value: z.number(),
  review_applied: z.boolean(),
  selection_mode: z.string(),
  requested_data_mode: z.string(),
  effective_data_mode: z.string(),
  llm_mode: z.string(),
  degraded: z.boolean(),
  degrade_reason: z.string(),
  timestamp: z.string(),
}).passthrough()

export const CycleSkippedEventSchema = z.object({
  status: z.literal('no_data'),
  cycle_id: z.number().int(),
  cutoff_date: z.string(),
  stage: z.string(),
  reason: z.string(),
  timestamp: z.string(),
}).passthrough()

export const AgentStatusEventSchema = z.object({
  timestamp: z.string(),
  cycle_id: z.number().int().optional(),
  cutoff_date: z.string().optional(),
  agent: z.string(),
  status: z.string(),
  message: z.string(),
  stage: z.string().optional(),
  progress_pct: z.number().int().optional(),
  step: z.number().int().optional(),
  total_steps: z.number().int().optional(),
  thinking: z.string().optional(),
  selected_stocks: z.array(z.string()).optional(),
  details: z.unknown().optional(),
}).passthrough()

export const ModuleLogEventSchema = z.object({
  timestamp: z.string(),
  cycle_id: z.number().int().optional(),
  cutoff_date: z.string().optional(),
  module: z.string(),
  title: z.string(),
  message: z.string().optional().default(''),
  kind: z.string(),
  level: z.string(),
  details: z.unknown().optional(),
  metrics: z.record(z.string(), z.unknown()).optional(),
}).passthrough()

export const MeetingSpeechEventSchema = z.object({
  timestamp: z.string(),
  cycle_id: z.number().int().optional(),
  cutoff_date: z.string().optional(),
  meeting: z.string(),
  speaker: z.string(),
  speech: z.string(),
  role: z.string().optional(),
  picks: z.array(z.union([z.record(z.string(), z.unknown()), z.string()])).optional(),
  suggestions: z.array(z.string()).optional(),
  decision: z.record(z.string(), z.unknown()).optional(),
  confidence: z.union([z.number(), z.string(), z.null()]).optional(),
}).passthrough()

export const RuntimeEventPayloadSchemas = {
  connected: ConnectedEventSchema,
  cycle_start: CycleStartEventSchema,
  cycle_complete: CycleCompleteEventSchema,
  cycle_skipped: CycleSkippedEventSchema,
  agent_status: AgentStatusEventSchema,
  agent_progress: AgentStatusEventSchema,
  module_log: ModuleLogEventSchema,
  meeting_speech: MeetingSpeechEventSchema,
} as const

export type FlatError = z.infer<typeof FlatErrorSchema>
export type StatusError = z.infer<typeof StatusErrorSchema>
export type RuntimeStatus = z.infer<typeof RuntimeStatusSchema>
export type LabStatusEnvelope = z.infer<typeof LabStatusEnvelopeSchema>
export type ArtifactList = z.infer<typeof ArtifactListSchema>
export type TrainingPlan = z.infer<typeof TrainingPlanSchema>
export type TrainingExecution = z.infer<typeof TrainingExecutionSchema>
export type RuntimePathsResponse = z.infer<typeof RuntimePathsSchema>
export type EvolutionConfigResponse = z.infer<typeof EvolutionConfigSchema>
export type InvestmentModelsResponse = z.infer<typeof InvestmentModelsSchema>
export type LeaderboardResponse = z.infer<typeof LeaderboardSchema>
export type AllocatorResponse = z.infer<typeof AllocatorSchema>
export type StrategyGeneListResponse = z.infer<typeof StrategyGeneListSchema>
export type StrategyReloadResponse = z.infer<typeof StrategyReloadSchema>
export type CountedItemsResponse = z.infer<typeof CountedItemsSchema>
export type DataStatusResponse = z.infer<typeof DataStatusSchema>
export type DataDownloadResponse = z.infer<typeof DataDownloadSchema>
export type KnownRuntimeEventType = keyof typeof RuntimeEventPayloadSchemas
