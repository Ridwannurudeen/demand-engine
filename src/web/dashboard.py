from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select

from src.data.models import AsyncSessionLocal, Job, JobStatus, Revenue

router = APIRouter()


async def _get_stats() -> dict:
    """Aggregate dashboard statistics."""
    async with AsyncSessionLocal() as session:
        # Total jobs
        total = await session.scalar(select(func.count(Job.id)))

        # Jobs by status
        status_counts = {}
        for status in JobStatus:
            count = await session.scalar(
                select(func.count(Job.id)).where(Job.status == status)
            )
            if count:
                status_counts[status.value] = count

        # Revenue
        total_revenue = await session.scalar(
            select(func.sum(Revenue.client_paid))
        ) or 0.0
        total_margin = await session.scalar(
            select(func.sum(Revenue.net_margin))
        ) or 0.0
        job_count_paid = await session.scalar(
            select(func.count(Revenue.id))
        ) or 0

        # Recent jobs
        result = await session.execute(
            select(Job).order_by(Job.created_at.desc()).limit(20)
        )
        recent_jobs = result.scalars().all()

        # Free trials used
        free_trials = await session.scalar(
            select(func.count(Job.id)).where(Job.is_free_trial == True)
        ) or 0

    return {
        "total_jobs": total or 0,
        "status_counts": status_counts,
        "total_revenue": total_revenue,
        "total_margin": total_margin,
        "paid_jobs": job_count_paid,
        "free_trials": free_trials,
        "recent_jobs": recent_jobs,
    }


def _render_dashboard(stats: dict) -> str:
    """Render dashboard HTML."""
    # Build jobs table rows
    job_rows = ""
    for j in stats["recent_jobs"]:
        price = f"${j.client_price:.2f}" if j.client_price else "—"
        cat = j.category.value if j.category else "—"
        platform = j.client_platform or "—"
        trial = "Yes" if j.is_free_trial else ""
        status_color = {
            "delivered": "#22c55e",
            "completed": "#22c55e",
            "in_progress": "#f59e0b",
            "quoted": "#3b82f6",
            "pending": "#6b7280",
            "failed": "#ef4444",
            "cancelled": "#6b7280",
        }.get(j.status.value, "#6b7280")
        created = j.created_at.strftime("%b %d %H:%M") if j.created_at else "—"
        job_rows += f"""
        <tr>
            <td>#{j.id}</td>
            <td><span style="color:{status_color};font-weight:600">{j.status.value}</span></td>
            <td>{cat}</td>
            <td>{price}</td>
            <td>{platform}</td>
            <td>{trial}</td>
            <td>{created}</td>
            <td style="max-width:250px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">{j.raw_request[:80] if j.raw_request else '—'}</td>
        </tr>"""

    # Status breakdown
    status_items = "".join(
        f'<div class="stat-chip"><span class="label">{k}</span><span class="value">{v}</span></div>'
        for k, v in stats["status_counts"].items()
    )

    avg_revenue = (
        stats["total_revenue"] / stats["paid_jobs"]
        if stats["paid_jobs"]
        else 0
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Demand Engine Dashboard</title>
    <style>
        * {{ margin:0; padding:0; box-sizing:border-box; }}
        body {{ font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif; background:#0f172a; color:#e2e8f0; }}
        .container {{ max-width:1200px; margin:0 auto; padding:24px; }}
        h1 {{ font-size:28px; margin-bottom:8px; color:#f8fafc; }}
        .subtitle {{ color:#94a3b8; margin-bottom:32px; }}
        .cards {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(200px,1fr)); gap:16px; margin-bottom:32px; }}
        .card {{ background:#1e293b; border-radius:12px; padding:20px; border:1px solid #334155; }}
        .card .label {{ font-size:13px; color:#94a3b8; text-transform:uppercase; letter-spacing:0.5px; }}
        .card .value {{ font-size:32px; font-weight:700; color:#f8fafc; margin-top:4px; }}
        .card .value.green {{ color:#22c55e; }}
        .status-bar {{ display:flex; flex-wrap:wrap; gap:8px; margin-bottom:32px; }}
        .stat-chip {{ background:#1e293b; border:1px solid #334155; border-radius:8px; padding:8px 14px; display:flex; gap:8px; align-items:center; }}
        .stat-chip .label {{ color:#94a3b8; font-size:13px; }}
        .stat-chip .value {{ color:#f8fafc; font-weight:600; }}
        table {{ width:100%; border-collapse:collapse; background:#1e293b; border-radius:12px; overflow:hidden; border:1px solid #334155; }}
        th {{ text-align:left; padding:12px 16px; background:#0f172a; color:#94a3b8; font-size:12px; text-transform:uppercase; letter-spacing:0.5px; }}
        td {{ padding:10px 16px; border-top:1px solid #1e293b; font-size:14px; }}
        tr:hover {{ background:#334155; }}
        .refresh {{ color:#3b82f6; text-decoration:none; font-size:14px; }}
        .header {{ display:flex; justify-content:space-between; align-items:center; margin-bottom:32px; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <div>
                <h1>Demand Engine</h1>
                <p class="subtitle">Real-time job monitoring and analytics</p>
            </div>
            <a href="/dashboard" class="refresh">Refresh</a>
        </div>

        <div class="cards">
            <div class="card">
                <div class="label">Total Jobs</div>
                <div class="value">{stats['total_jobs']}</div>
            </div>
            <div class="card">
                <div class="label">Revenue</div>
                <div class="value green">${stats['total_revenue']:.2f}</div>
            </div>
            <div class="card">
                <div class="label">Net Margin</div>
                <div class="value green">${stats['total_margin']:.2f}</div>
            </div>
            <div class="card">
                <div class="label">Avg per Job</div>
                <div class="value">${avg_revenue:.2f}</div>
            </div>
            <div class="card">
                <div class="label">Paid Jobs</div>
                <div class="value">{stats['paid_jobs']}</div>
            </div>
            <div class="card">
                <div class="label">Free Trials</div>
                <div class="value">{stats['free_trials']}</div>
            </div>
        </div>

        <div class="status-bar">{status_items}</div>

        <table>
            <thead>
                <tr>
                    <th>ID</th>
                    <th>Status</th>
                    <th>Category</th>
                    <th>Price</th>
                    <th>Platform</th>
                    <th>Trial</th>
                    <th>Created</th>
                    <th>Request</th>
                </tr>
            </thead>
            <tbody>{job_rows or '<tr><td colspan="8" style="text-align:center;padding:32px;color:#94a3b8">No jobs yet</td></tr>'}</tbody>
        </table>
    </div>
</body>
</html>"""


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard():
    stats = await _get_stats()
    return _render_dashboard(stats)


@router.get("/api/stats")
async def api_stats():
    stats = await _get_stats()
    # Convert to JSON-safe format
    stats["recent_jobs"] = [
        {
            "id": j.id,
            "status": j.status.value,
            "category": j.category.value if j.category else None,
            "client_price": j.client_price,
            "client_platform": j.client_platform,
            "is_free_trial": j.is_free_trial,
            "raw_request": j.raw_request[:100] if j.raw_request else None,
            "created_at": j.created_at.isoformat() if j.created_at else None,
        }
        for j in stats["recent_jobs"]
    ]
    return stats
