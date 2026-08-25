"""AnalysisService 包入口(镜像 gold-bot apps/app-server/src/services/analysis)。"""

from __future__ import annotations

from backend.services.analysis.index import AnalysisService, create_analysis_service

__all__ = [
    "AnalysisService",
    "create_analysis_service",
]
