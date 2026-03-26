# Agent Extensions

The main repository ships with built-in public-safe agent prompt defaults for the core runtime roles.

`agent_settings/` is an optional extension surface:

- `agent_settings/agents_config.json` can override built-in `system_prompt` and `llm_model` values
- `agent_settings/plugins/*.json` can define additional declarative plugins

Override behavior is additive:

- built-in defaults load first
- local JSON overrides are applied on top
- an empty `{}` file is valid and keeps the built-in baseline active

This allows the OSS repo to stay self-contained while leaving room for private or future separately distributed prompt packs that reuse the same JSON schema.
