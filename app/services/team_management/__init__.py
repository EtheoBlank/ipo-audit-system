"""Team management services subpackage.

Re-exports the high-level service classes for convenient imports:
    from app.services.team_management import TeamManagementService
"""

from app.services.team_management.service import (
    TeamManagementService,
    team_management_service,
)
from app.services.team_management.work_plan_generator import WorkPlanGenerator
from app.services.team_management.quality_assessor import MeetingQualityAssessor
from app.services.team_management.recommendation_generator import (
    ManagementRecommendationGenerator,
)
from app.services.team_management.progress_tracker import ProgressTracker

__all__ = [
    "TeamManagementService",
    "team_management_service",
    "WorkPlanGenerator",
    "MeetingQualityAssessor",
    "ManagementRecommendationGenerator",
    "ProgressTracker",
]
