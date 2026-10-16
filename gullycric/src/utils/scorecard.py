from typing import Dict, Any, Optional

def format_video_style_scorecard(
    p1_name: str,
    p1_runs: int,
    p1_balls: int,
    p1_fours: int,
    p1_sixes: int,
    p1_wickets: int,
    p1_conceded: int,
    p1_overs: str,
    p1_eco: float,
    p1_out: bool,
    p2_name: str,
    p2_runs: int,
    p2_balls: int,
    p2_fours: int,
    p2_sixes: int,
    p2_wickets: int,
    p2_conceded: int,
    p2_overs: str,
    p2_eco: float,
    p2_out: bool,
) -> str:
    sr1 = (p1_runs / p1_balls * 100) if p1_balls > 0 else 0.0
    sr2 = (p2_runs / p2_balls * 100) if p2_balls > 0 else 0.0

    return (
        "🏏 <b>SOLO SCOREBOARD</b>\n"
        "┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈\n"
        "┄ Status: 🏁 Final ┄\n"
        f"👤 1. <b>{p1_name}</b>\n"
        f"  ↳ 🏏 {p1_runs}({p1_balls}) · SR {sr1:.1f}\n"
        f"     4s {p1_fours} · 6s {p1_sixes}\n"
        f"  ↳ 🥎 {p1_wickets}/{p1_conceded} · {p1_overs} ov\n"
        f"     Eco {p1_eco:.2f}\n"
        f"     🎯 Spell 1 {p1_wickets}/{p1_conceded}\n"
        f"👤 2. <b>{p2_name}</b>\n"
        f"  ↳ 🏏 {p2_runs}({p2_balls}) · SR {sr2:.1f}\n"
        f"     4s {p2_fours} · 6s {p2_sixes}\n"
        f"  ↳ 🥎 {p2_wickets}/{p2_conceded} · {p2_overs} ov\n"
        f"     Eco {p2_eco:.2f}\n"
        f"     🎯 Spell 1 {p2_wickets}/{p2_conceded}\n"
        "🏁 Match complete · tap Play Again for another innings."
    )

def format_player_of_match(winner_name: str, runs: int, balls: int, wickets: int, runs_conceded: int) -> str:
    return (
        "🏅 <b>PLAYER OF THE MATCH</b>\n"
        f"<b>{winner_name}</b> — {runs} ({balls}b) · {wickets}/{runs_conceded}\n"
        "A performance to remember. 🏆"
    )

def format_over_summary(
    over_num: int,
    bowler_name: str,
    over_runs: int,
    over_wkts: int,
    fours: int,
    sixes: int,
    ball_history: list,
    batter_name: str,
    batter_runs: int,
    batter_balls: int
) -> str:
    sr = (batter_runs / max(1, batter_balls)) * 100
    balls_str = " | ".join(str(b) for b in ball_history) if ball_history else "0"
    return (
        f"🏏 <b>OVER {over_num} COMPLETE</b>\n"
        "┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈\n"
        f"➤ 🎳 <b>{bowler_name}</b>\n"
        f"  ↳ Runs {over_runs} · Wkts {over_wkts}\n"
        "➤ 👀 THIS OVER\n"
        f"  ↳ {over_runs}R · {over_wkts}W · 4s {fours} · 6s {sixes}\n"
        f"  ↳ 🎯 {balls_str}\n"
        "➤ 🏏 BATTERS THIS OVER\n"
        f"  ↳ ⭐ <b>{batter_name}</b> · {batter_runs}({batter_balls}) SR:{sr:.0f}"
    )

def format_text_scorecard(
    batting_team_name: str,
    bowling_team_name: str,
    total_runs: int,
    wickets: int,
    overs: str,
    target: Optional[int] = None,
    batter_stats: Optional[Dict[str, Any]] = None,
    bowler_stats: Optional[Dict[str, Any]] = None,
    summary: Optional[str] = None
) -> str:
    lines = [
        "🏏 <b>SOLO SCOREBOARD</b>",
        "┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈",
        f"<b>{batting_team_name}</b>: <code>{total_runs}/{wickets}</code> ({overs} ov)",
    ]

    if target is not None:
        lines.append(f"🎯 Target: <b>{target}</b> runs")

    if batter_stats:
        b_name = batter_stats.get("name", "Striker")
        b_runs = batter_stats.get("runs", 0)
        b_balls = batter_stats.get("balls", 0)
        b_4s = batter_stats.get("fours", 0)
        b_6s = batter_stats.get("sixes", 0)
        sr = (b_runs / b_balls * 100) if b_balls > 0 else 0.0
        lines.append(f"👤 {b_name}: {b_runs} ({b_balls}b) · SR {sr:.1f}")

    if bowler_stats:
        bw_name = bowler_stats.get("name", "Bowler")
        bw_w = bowler_stats.get("wickets", 0)
        bw_r = bowler_stats.get("runs", 0)
        lines.append(f"🎯 {bw_name}: {bw_w}/{bw_r}")

    lines.append("┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈")
    if summary:
        lines.append(f"🏆 <b>{summary}</b>")

    return "\n".join(lines)
