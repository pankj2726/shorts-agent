"""
agents — Shorts Agent multi-agent team package.
"""
from agent.modules.orchestration import (
    BaseAgent,
    CentralOrchestrator,
    CentralOrchestratorAgent,
    ResearchAgent,
    AntiSlopAgent,
    SocialAgent,
    ProvidersAgent,
    InnovationAgent,
    get_orchestrator,
)

__all__ = [
    "BaseAgent",
    "CentralOrchestrator",
    "CentralOrchestratorAgent",
    "ResearchAgent",
    "AntiSlopAgent",
    "SocialAgent",
    "ProvidersAgent",
    "InnovationAgent",
    "get_orchestrator",
]
