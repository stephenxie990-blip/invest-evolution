from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_human_entrypoint_contract_is_frozen_to_commander() -> None:
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    compatibility = (PROJECT_ROOT / "docs" / "COMPATIBILITY_SURFACE.md").read_text(
        encoding="utf-8"
    )

    assert "Commander` 作为**唯一人类入口**使用" in readme
    assert "Commander 是唯一推荐的人类入口" in compatibility
    assert "Web/API 只保留可视化、状态读取、SSE 与 API 命令路由" in compatibility
    assert "`invest-train` 保留为协议化训练/调试入口" in compatibility
    assert "`invest-data` 保留为数据底座维护入口" in compatibility


def test_data_access_doc_points_at_current_market_data_owners() -> None:
    data_doc = (PROJECT_ROOT / "docs" / "DATA_ACCESS_ARCHITECTURE.md").read_text(
        encoding="utf-8"
    )

    assert "`market_data/repository.py`" in data_doc
    assert "`market_data/manager.py`" in data_doc
    assert "`market_data/datasets.py`" in data_doc
    assert "`invest_evolution.market_data`" in data_doc
    assert (
        "不再维护 `market_data/ingestion.py`、`market_data/quality.py`、`market_data/gateway.py` 作为独立公共 owner"
        in data_doc
    )
    assert "直接拼 SQL" in data_doc
    assert "第二份可写事实源" in data_doc


def test_config_governance_doc_freezes_minimal_writable_surfaces() -> None:
    governance_doc = (PROJECT_ROOT / "docs" / "CONFIG_GOVERNANCE.md").read_text(
        encoding="utf-8"
    )
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")

    assert "当前最小可写配置面只有四类" in governance_doc
    assert "`/api/evolution_config`：训练与 Web 运行参数" in governance_doc
    assert "`/api/control_plane`：LLM provider / model / api_key 绑定" in governance_doc
    assert "`/api/runtime_paths`：训练输出与工件目录" in governance_doc
    assert "`/api/agent_prompts`：角色 prompt baseline" in governance_doc
    assert "`/api/runtime_paths`：训练输出与工件目录" in readme


def test_docs_freeze_explainability_and_layer_boundaries() -> None:
    runtime_doc = (PROJECT_ROOT / "docs" / "RUNTIME_STATE_DESIGN.md").read_text(
        encoding="utf-8"
    )
    agent_doc = (PROJECT_ROOT / "docs" / "AGENT_INTERACTION.md").read_text(
        encoding="utf-8"
    )
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")

    assert "最小 explainability 工件集" in runtime_doc
    assert "cycle_result_path" in runtime_doc
    assert "selection_artifact_json_path" in runtime_doc
    assert "manager_review_artifact_json_path" in runtime_doc
    assert "allocation_review_artifact_json_path" in runtime_doc
    assert "Agent Runtime Platform" in agent_doc
    assert "ManagerAgent" in agent_doc
    assert "Capability Hub" in agent_doc
    assert "Governance Layer" in agent_doc
    assert "Cognitive Assist" in agent_doc
    assert "平台核心 vs 投资域核心" in readme
    assert "平台核心：" in readme
    assert "投资域核心：" in readme


def test_readme_and_docs_index_freeze_public_story_and_active_ops_docs() -> None:
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    docs_index = (PROJECT_ROOT / "docs" / "README.md").read_text(encoding="utf-8")
    onboarding = (PROJECT_ROOT / "docs" / "ONBOARDING_HANDOFF.md").read_text(
        encoding="utf-8"
    )

    assert "当前对外只讲三件事" in readme
    assert "Commander control surface" in readme
    assert "Training Lab + governance loop" in readme
    assert "Stateless Web/API deploy surface" in readme
    assert "ONBOARDING_HANDOFF.md" in docs_index
    assert "RELEASE_READINESS.md" in docs_index
    assert "第一小时阅读路径" in onboarding
    assert "Handoff Checklist / 交接清单" in onboarding
