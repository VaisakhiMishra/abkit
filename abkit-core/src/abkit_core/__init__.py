"""abkit-core — shared schemas and business logic for the A/B Testing Productivity Kit."""

__version__ = "0.1.0"

# Public surface — import these names from abkit_core directly.
from abkit_core.schemas import (
    AnalysisResult,
    CupedReadiness,
    CupedRecommendation,
    DecisionMemo,
    DecisionRecommendation,
    DiagnosticIssue,
    DurationPlan,
    ExperimentConfig,
    IssueSeverity,
    MetricEstimate,
    MetricType,
    QualityChecks,
    ResultPayload,
    RunStatus,
    SpecValidation,
    SrmCheck,
    SrmSeverity,
)
from abkit_core.design import validate_experiment_spec
from abkit_core.quality import (
    check_assignment_quality,
    check_join_integrity,
    check_metric_quality,
)
from abkit_core.variance import (
    apply_cuped_adjustment,
    assess_cuped_readiness,
)
from abkit_core.analysis import run_analysis

__all__ = [
    # schemas
    "AnalysisResult",
    "CupedReadiness",
    "CupedRecommendation",
    "DecisionMemo",
    "DecisionRecommendation",
    "DiagnosticIssue",
    "DurationPlan",
    "ExperimentConfig",
    "IssueSeverity",
    "MetricEstimate",
    "MetricType",
    "QualityChecks",
    "ResultPayload",
    "RunStatus",
    "SpecValidation",
    "SrmCheck",
    "SrmSeverity",
    # design
    "validate_experiment_spec",
    # quality
    "check_assignment_quality",
    "check_join_integrity",
    "check_metric_quality",
    # variance
    "apply_cuped_adjustment",
    "assess_cuped_readiness",
    # analysis
    "run_analysis",
]
