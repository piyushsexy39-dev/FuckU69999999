from typing import Optional, Dict

def check_milestones(runs_scored: int, previous_runs: int) -> Optional[str]:
    """Detects half-century or century milestones."""
    if previous_runs < 50 <= runs_scored:
        return "🌟 HALF-CENTURY! Spectacular 50 runs milestone reached in the mohalla!"
    if previous_runs < 100 <= runs_scored:
        return "👑 CENTURY! Extraordinary 100 runs! The whole gully is chanting!"
    return None

def check_hat_trick(consecutive_wickets: int) -> Optional[str]:
    if consecutive_wickets == 3:
        return "🎩 HAT-TRICK! 3 Wickets in 3 consecutive balls! Absolute mayhem!"
    return None
